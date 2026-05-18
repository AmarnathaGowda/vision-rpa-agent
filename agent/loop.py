"""Core agent loop — observe → reason → act → store.

Phase 1 scope:
- Full cycle wired end-to-end with real PerceptionLayer + ActionPlanner.
- Executors are stubs (StubExecutor below) — no real automation yet.
  Phase 2 swaps in the real BrowserExecutor.
- HITL routing writes to SQLite via SessionMemory and pauses the loop.
- Audit log captures every perception and plan.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from agent.audit import AuditLog
from agent.perception import PerceptionLayer
from agent.planner import ActionPlanner
from agent.recovery import RecoveryDirective, RecoveryHandler
from agent.schemas import ActionPlan, ActionResult, ScreenState
from config.logging_config import get_logger
from config.settings import settings
from memory.session import SessionMemory
from memory.working import WorkingMemory

log = get_logger(__name__)


@dataclass
class TaskGoal:
    task_id: str
    task_type: str
    goal: str
    raw: dict
    steps: list[dict] | None = None   # deterministic step list (optional)

    def deterministic(self) -> bool:
        return bool(self.steps)

    def step_plan(self, index: int) -> dict | None:
        if not self.steps or index >= len(self.steps):
            return None
        return self.steps[index]


class StubExecutor:
    """Phase 1 placeholder — logs the plan and returns 'deferred'.

    Real executors (browser/desktop/rdp) arrive in Phase 2+.
    """

    def execute(self, plan: ActionPlan) -> ActionResult:
        return ActionResult(
            status="deferred",
            error_msg="executor not implemented until Phase 2",
            duration_ms=0,
        )


class AgentLoop:
    RETRY_LIMIT = 3
    DUPLICATE_PLAN_THRESHOLD = 2  # 2 consecutive identical plans → HITL

    def __init__(
        self,
        session: SessionMemory,
        knowledge: Any | None = None,
        perception: PerceptionLayer | None = None,
        planner: ActionPlanner | None = None,
        executor: Any | None = None,
        recovery: RecoveryHandler | None = None,
        audit: AuditLog | None = None,
        agent_id: str | None = None,
    ) -> None:
        self.session = session
        self.knowledge = knowledge
        self.perception = perception or PerceptionLayer()
        self.planner = planner or ActionPlanner(retry_limit=self.RETRY_LIMIT)
        self.executor = executor or StubExecutor()
        self.recovery = recovery or RecoveryHandler()
        self.agent_id = agent_id or settings.agent_id
        self.audit = audit or AuditLog(self.agent_id)
        self.working: WorkingMemory | None = None
        self.task_goal: TaskGoal | None = None

    # ── public API ────────────────────────────────────────────────────────
    def run(self, task: dict) -> dict:
        self._init_task(task)
        return self._run_loop()

    def resume(self, working: WorkingMemory, task_goal: TaskGoal) -> dict:
        """Resume an interrupted task from its last checkpoint.

        Preserves the caller's WorkingMemory exactly — does NOT call _init_task.
        """
        self.working = working
        self.task_goal = task_goal
        self.working.hitl_pending = False
        log.info("task_resume", task_id=working.task_id, step=working.step)
        self.audit.append("task_resume", task_id=working.task_id, step=working.step)
        return self._run_loop()

    # ── pipeline steps ────────────────────────────────────────────────────
    def _run_loop(self) -> dict:
        assert self.working is not None and self.task_goal is not None

        while self._should_continue():
            screen = self._observe()

            # Recovery — pre-action: blocking modal / error_present → override plan or HITL
            pre = self.recovery.detect(screen, self.working)
            if pre is not None:
                if self._apply_directive(pre, screen, planning_phase=True):
                    continue   # directive handled — next loop iteration

            plan = self._reason(screen)

            # ── Task-complete signal ─────────────────────────────────
            # The LLM declares the goal satisfied. Persist the decision
            # and exit the loop cleanly on the next iteration.
            if plan.action_type == "task_complete":
                log.info("task_complete_declared",
                         task_id=self.working.task_id,
                         step=self.working.step,
                         reason=plan.reason or "(no reason given)")
                self.working.task_complete = True
                self._store(plan, ActionResult(status="ok",
                                               extracted_value=plan.reason or "",
                                               duration_ms=0), screen)
                continue

            if plan.requires_hitl:
                self._route_to_hitl(plan, screen)
                self._store(plan, ActionResult(status="deferred",
                                               error_msg="hitl_pending"), screen)
                continue  # _should_continue() will exit with exit_reason="hitl_pending"

            # ── Duplicate-plan guardrail ─────────────────────────────
            # If the agent just successfully executed the same plan (same
            # action_type + target + value) on the previous step, the LLM
            # has misread the screen state. Re-running the same action will
            # produce the same result. Route to HITL once consecutive dupes
            # cross the threshold.
            if self._is_repeated_plan(plan):
                self._route_to_hitl(
                    plan, screen,
                    explicit_reason=(
                        f"The agent has planned the same action "
                        f"({plan.action_type} on '{plan.target}') "
                        f"{self.DUPLICATE_PLAN_THRESHOLD} times in a row "
                        f"without the screen changing. It may be stuck — "
                        f"please look at the browser and choose how to continue."
                    ),
                )
                self._store(plan, ActionResult(status="deferred",
                                               error_msg="duplicate_plan_loop"), screen)
                continue

            result = self._act(plan)

            # Unresolved credential placeholders short-circuit to HITL — no
            # amount of retrying will produce a value the framework doesn't
            # have. The HITL panel surfaces an input field for the operator.
            if result.status == "failed" and result.error_msg.startswith(
                "unresolved_credentials:"
            ):
                keys_csv = result.error_msg.split(":", 1)[1].strip()
                keys = [k.strip() for k in keys_csv.split(",") if k.strip()]
                self._route_to_hitl(
                    plan, screen,
                    explicit_reason=(
                        f"Credential placeholder(s) {keys} could not be "
                        f"resolved from .env / working memory. "
                        f"Enter the value below to continue."
                    ),
                    credential_keys=keys,
                )
                self._store(plan, result, screen)
                continue

            # Recovery — post-action: classify failed results, run rdp_reconnect, etc.
            if result.status == "failed":
                post = self.recovery.recover(plan, result, self.working)
                if self._apply_directive(post, screen, planning_phase=False,
                                          failed_plan=plan, failed_result=result):
                    continue

            self._store(plan, result, screen)

        return self._finalise()

    def _apply_directive(self,
                         directive: RecoveryDirective,
                         screen: ScreenState,
                         planning_phase: bool,
                         failed_plan: ActionPlan | None = None,
                         failed_result: ActionResult | None = None) -> bool:
        """Apply a RecoveryDirective. Returns True if the loop should `continue`.

        Recovery is bounded: each step gets at most ``RETRY_LIMIT`` recovery
        invocations before we hand off to HITL. This protects against
        runaway recover→fail→recover cycles.
        """
        assert self.working is not None
        step_key = str(self.working.step)
        rec_counts = self.working.retry_counts
        rec_attempts = rec_counts.get(f"recovery_{step_key}", 0)

        log.info("recovery_directive",
                 step=self.working.step,
                 action=directive.action,
                 reason=directive.reason,
                 attempts=rec_attempts,
                 phase="pre" if planning_phase else "post")
        self.audit.append("recovery_directive",
                          task_id=self.working.task_id,
                          step=self.working.step,
                          action=directive.action,
                          reason=directive.reason)

        if directive.action == "hitl":
            # Synthesise a placeholder plan when the post-action path didn't
            # produce one (happens only in the pre-action / detect branch).
            hitl_plan = failed_plan or ActionPlan(
                action_type="flag_human",
                target=directive.reason or "recovery_hitl",
                reason=directive.reason or "recovery escalated",
                requires_hitl=True,
                confidence=0.0,
            )
            self._route_to_hitl(hitl_plan, screen)
            self._store(hitl_plan,
                        ActionResult(status="deferred", error_msg=directive.reason),
                        screen)
            return True

        if directive.action == "abort":
            self.working.exit_reason = directive.reason or "recovery_abort"
            self.working.hitl_pending = True
            return True

        if directive.action == "skip":
            # Advance past the current step without re-attempting it.
            self.working.step += 1
            return True

        if directive.action in ("retry", "rdp_reconnect"):
            rec_counts[f"recovery_{step_key}"] = rec_attempts + 1
            if rec_attempts + 1 > self.RETRY_LIMIT:
                log.warning("recovery_attempts_exceeded",
                            step=self.working.step,
                            attempts=rec_attempts + 1)
                explicit = (
                    f"recovery '{directive.action}' exceeded after "
                    f"{rec_attempts + 1} attempts on step {self.working.step}. "
                    f"Last failure: {failed_result.error_msg if failed_result else 'n/a'}"
                )
                self._route_to_hitl(
                    failed_plan or ActionPlan(
                        action_type="flag_human", target="recovery_loop_exceeded",
                        reason=explicit,
                        requires_hitl=True, confidence=0.0,
                    ),
                    screen,
                    explicit_reason=explicit,
                )
                self._store(failed_plan or ActionPlan(action_type="noop"),
                            failed_result or ActionResult(
                                status="failed",
                                error_msg=f"recovery_attempts_exceeded ({directive.action})"),
                            screen)
                return True

            # Execute the follow-up plan (e.g. close-modal click, rdp_reconnect).
            if directive.follow_up_plan is not None:
                followup_result = self._act(directive.follow_up_plan)
                self._store(directive.follow_up_plan, followup_result, screen)
                # Don't advance the original step — the next loop iteration
                # will re-perceive and re-plan.
            # On a plain "retry" with no follow-up, just consume the loop turn.
            return True

        log.warning("recovery_unknown_action", action=directive.action)
        return False

    # ── pipeline steps ────────────────────────────────────────────────────
    def _init_task(self, task: dict) -> None:
        task_id = task.get("task_id") or f"task_{int(time.time())}"
        task_type = task.get("task_type", "unknown")
        goal = task.get("goal", "")
        steps = task.get("steps")
        self.task_goal = TaskGoal(task_id=task_id, task_type=task_type, goal=goal,
                                  raw=task, steps=steps)
        self.working = WorkingMemory(
            task_id=task_id,
            task_type=task_type,
            goal=goal,
            agent_id=self.agent_id,
        )
        self.session.start_task(task_id=task_id, task_type=task_type, goal=goal,
                                agent_id=self.agent_id)
        log.info("task_init", task_id=task_id, task_type=task_type, goal=goal,
                 deterministic=self.task_goal.deterministic())
        self.audit.append("task_init", task_id=task_id, task_type=task_type, goal=goal,
                          deterministic=self.task_goal.deterministic())

    def _should_continue(self) -> bool:
        assert self.working is not None
        if self.working.step >= settings.max_loop_steps:
            self.working.exit_reason = "max_steps_exceeded"
            log.warning("max_steps_exceeded", step=self.working.step)
            return False
        if self.working.hitl_pending:
            self.working.exit_reason = "hitl_pending"
            return False
        if self.working.task_complete:
            self.working.exit_reason = "task_complete"
            return False
        return True

    def _observe(self) -> ScreenState:
        assert self.working is not None and self.task_goal is not None
        # Deterministic mode: skip VLM perception entirely — the YAML drives steps.
        if self.task_goal.deterministic():
            state = ScreenState(
                app_type="browser",
                state_summary="deterministic step list",
                confidence=1.0,
            )
            log.info("perception_skipped",
                     step=self.working.step,
                     reason="deterministic_task")
            self.audit.append("perception",
                              task_id=self.working.task_id,
                              step=self.working.step,
                              screen=state.model_dump(),
                              deterministic=True)
            return state
        img = self.perception.capture()
        img = self.perception.preprocess(img)
        state = self.perception.understand(img, context={
            "task_goal": self.task_goal.goal,
            "last_action": self.working.last_action,
            "step": self.working.step,
        })
        log.info("perception",
                 step=self.working.step,
                 app_type=state.app_type,
                 confidence=state.confidence,
                 summary=state.state_summary)
        self.audit.append("perception",
                          task_id=self.working.task_id,
                          step=self.working.step,
                          screen=state.model_dump())
        return state

    def _reason(self, screen: ScreenState) -> ActionPlan:
        assert self.working is not None and self.task_goal is not None
        # ── HITL-resolution override ─────────────────────────────────
        # If the operator just approved a flag_human plan, the queue
        # stashed a one-shot override here. Run it directly, bypass the
        # LLM entirely. Pop after use.
        override = self.working.extracted_values.pop(
            "next_action_override", None,
        )
        if override:
            plan = ActionPlan(**override)
            log.info("plan",
                     step=self.working.step,
                     action_type=plan.action_type,
                     target=plan.target,
                     source="hitl_override")
            self.audit.append("plan",
                              task_id=self.working.task_id,
                              step=self.working.step,
                              plan=plan.model_dump(),
                              source="hitl_override")
            return plan
        step_rule = self.task_goal.step_plan(self.working.step)
        if step_rule is not None:
            plan = ActionPlan(cache_hit=True, confidence=1.0, **step_rule)
            log.info("plan",
                     step=self.working.step,
                     action_type=plan.action_type,
                     target=plan.target,
                     source="deterministic")
            self.audit.append("plan",
                              task_id=self.working.task_id,
                              step=self.working.step,
                              plan=plan.model_dump(),
                              source="deterministic")
            return plan
        plan = self.planner.decide(
            screen_state=screen,
            working=self.working.to_json(),
            goal=self.task_goal.goal,
        )
        log.info("plan",
                 step=self.working.step,
                 action_type=plan.action_type,
                 target=plan.target,
                 confidence=plan.confidence,
                 hitl=plan.requires_hitl)
        self.audit.append("plan",
                          task_id=self.working.task_id,
                          step=self.working.step,
                          plan=plan.model_dump())
        return plan

    def _act(self, plan: ActionPlan) -> ActionResult:
        start = time.monotonic()
        # Sync working memory's extracted_values onto the browser executor so
        # operator-provided HITL credentials are visible to _resolve_templates.
        browser = getattr(self.executor, "browser", None)
        if browser is not None and self.working is not None:
            browser._working_view = dict(self.working.extracted_values)
        try:
            result = self.executor.execute(plan)
        except NotImplementedError as e:
            # Stub executors raise — convert to deferred result, do not crash.
            result = ActionResult(status="deferred", error_msg=str(e))
        except Exception as e:  # noqa: BLE001 — last-line safety net
            result = ActionResult(status="failed", error_msg=repr(e))
        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result

    def _store(self, plan: ActionPlan, result: ActionResult, screen: ScreenState) -> None:
        assert self.working is not None
        step = self.working.step

        self.working.last_action = plan.model_dump()
        self.working.last_result = result.model_dump()
        self.working.decisions_log.append({
            "step": step,
            "action_type": plan.action_type,
            "target": plan.target,
            "value": plan.value,
            "confidence": plan.confidence,
            "result_status": result.status,
        })

        # One-shot consumption of operator guidance — clear after a plan
        # used it. If the operator wants the same guidance applied again
        # they can re-submit. Avoids the planner re-injecting stale hints.
        if "human_guidance" in self.working.extracted_values:
            self.working.extracted_values.pop("human_guidance", None)

        if result.status == "failed":
            key = str(step)
            self.working.retry_counts[key] = self.working.retry_counts.get(key, 0) + 1
            # Deterministic mode has no planner-side retry guard — enforce one here
            # so a flaky/missing selector can't loop forever. Routes to HITL after
            # RETRY_LIMIT attempts at the same step.
            if self.working.retry_counts[key] >= self.RETRY_LIMIT:
                log.warning("retry_limit_exceeded", step=step,
                            attempts=self.working.retry_counts[key],
                            last_error=result.error_msg)
                self.audit.append("retry_limit_exceeded",
                                  task_id=self.working.task_id, step=step,
                                  attempts=self.working.retry_counts[key])
                self._route_to_hitl(
                    plan, screen,
                    explicit_reason=(
                        f"retry limit exceeded ({self.working.retry_counts[key]} "
                        f"attempts) on step {step}. Last error: {result.error_msg}"
                    ),
                )
        else:
            self.working.step += 1

        # Deterministic exhaustion → mark task complete.
        assert self.task_goal is not None
        if self.task_goal.deterministic() and self.task_goal.step_plan(self.working.step) is None:
            self.working.task_complete = True

        self.session.write_checkpoint(self.working.task_id, step, self.working)
        self.session.log_action(
            task_id=self.working.task_id,
            step=step,
            plan=plan,
            result=result,
            screenshot=result.screenshot_path,
        )
        self.audit.append("action_result",
                          task_id=self.working.task_id,
                          step=step,
                          status=result.status,
                          duration_ms=result.duration_ms,
                          error=result.error_msg,
                          screenshot=result.screenshot_path,
                          extracted_value=result.extracted_value)

    def _route_to_hitl(self, plan: ActionPlan, screen: ScreenState,
                        explicit_reason: str | None = None,
                        credential_keys: list[str] | None = None) -> None:
        assert self.working is not None
        # Explicit reasons (passed by recovery / retry-limit code paths) win
        # — they know *why* they escalated. Without one, classify based on
        # plan flags. Order matters: the planner itself may set
        # requires_hitl=True when the SOP / goal explicitly asks it to.
        if explicit_reason:
            reason = explicit_reason
        elif plan.is_financial and plan.confidence < settings.financial_confidence_threshold:
            reason = (f"financial action confidence {plan.confidence:.2f} "
                      f"below {settings.financial_confidence_threshold}")
        elif plan.confidence < settings.confidence_threshold:
            reason = (f"plan confidence {plan.confidence:.2f} below threshold "
                      f"{settings.confidence_threshold}")
        elif plan.requires_hitl:
            reason = (f"planner requested HITL: {plan.reason or 'no reason given'} "
                      f"(action_type={plan.action_type}, target={plan.target!r})")
        else:
            reason = "HITL routed without identifiable cause (bug)"

        # Capture a screenshot at the moment of escalation so the reviewer
        # sees exactly what the agent saw. Falls back to empty path on any
        # error — perception may not have a page wired up (desktop/RDP/tool).
        screenshot_path = self._capture_hitl_screenshot()

        hitl_id = self.session.write_hitl(
            task_id=self.working.task_id,
            agent_id=self.agent_id,
            reason=reason,
            screenshot=screenshot_path,
            context={
                "plan": plan.model_dump(),
                "screen": screen.model_dump(),
                "friendly_reason": self._friendly_hitl_reason(reason, plan, screen),
                # When set, the floating UI renders an input field per key
                # and submits the values as `correct` + extracted_values.
                "credential_keys": credential_keys or [],
            },
            timeout_minutes=settings.hitl_timeout_minutes,
        )
        self.working.hitl_pending = True
        log.warning("hitl_routed", task_id=self.working.task_id,
                    hitl_id=hitl_id, reason=reason)
        self.audit.append("hitl_routed",
                          task_id=self.working.task_id,
                          step=self.working.step,
                          hitl_id=hitl_id,
                          reason=reason)

    # ── duplicate-plan guardrail helpers ─────────────────────────────────
    def _is_repeated_plan(self, plan: ActionPlan) -> bool:
        """True when the same (action_type, target, value) appears as the
        last N entries of decisions_log. Reads from working memory so the
        check survives across HITL resume / recovery iterations."""
        assert self.working is not None
        N = self.DUPLICATE_PLAN_THRESHOLD
        log_entries = self.working.decisions_log[-N:]
        if len(log_entries) < N:
            return False
        key = (plan.action_type, plan.target, plan.value)
        for entry in log_entries:
            entry_key = (
                entry.get("action_type"),
                entry.get("target"),
                entry.get("value", ""),
            )
            if entry_key != key:
                return False
        return True

    # ── HITL screenshot + friendly reason helpers ────────────────────────
    def _capture_hitl_screenshot(self) -> str:
        """Save a PNG of the current browser page (if available) and return
        its path. Best-effort — failures are logged and an empty string is
        returned, never raised."""
        try:
            page = getattr(self.perception, "page", None)
            if page is None:
                return ""
            from pathlib import Path
            import time as _time

            from config.settings import settings as _settings

            shot_dir = Path(_settings.screenshot_dir) / self.agent_id
            shot_dir.mkdir(parents=True, exist_ok=True)
            path = shot_dir / f"hitl_{int(_time.time() * 1000)}.png"
            page.screenshot(path=str(path), full_page=False)
            return str(path)
        except Exception as e:  # noqa: BLE001
            log.warning("hitl_screenshot_failed", error=str(e))
            return ""

    def _friendly_hitl_reason(self, technical_reason: str,
                               plan: ActionPlan,
                               screen: ScreenState) -> str:
        """Translate the technical reason into a plain-English sentence.

        Designed for non-technical operators reading the floating UI. The
        original technical reason is preserved in the `reason` field for
        engineers; this string lives alongside in `context.friendly_reason`.
        """
        action = plan.action_type
        target = plan.target or "the element"
        tr = (technical_reason or "").lower()

        if "selector_unresolved" in tr or "no candidate matched" in tr:
            return (
                f"The agent wanted to {action} on '{target}', but it could not "
                f"find that element on the current page. The screen looks like: "
                f"{screen.state_summary or 'unknown'}. "
                f"Please check the page in the browser and choose an action below."
            )
        if "retry limit exceeded" in tr or "recovery" in tr and "exceeded" in tr:
            return (
                f"The agent has tried the same step several times without "
                f"success. Last action attempted: {action} on '{target}'. "
                f"Please look at the browser and decide how to continue."
            )
        if plan.is_financial:
            return (
                f"The agent is about to perform a financial action: {action} "
                f"with value '{plan.value}'. Confirmation required before it runs."
            )
        if plan.requires_hitl and plan.reason:
            return (
                f"The agent paused as instructed by the workflow ({plan.reason}). "
                f"Review the browser and decide how to proceed."
            )
        if "below threshold" in tr:
            return (
                f"The agent is not confident enough about the next step "
                f"({action} on '{target}'). Please confirm or correct."
            )
        return (
            f"The agent needs your help with the next step "
            f"({action} on '{target}'). Please check the browser and "
            f"choose an action below."
        )

    def _finalise(self) -> dict:
        assert self.working is not None
        reason = self.working.exit_reason or "unknown"
        status = "success" if reason == "task_complete" else "incomplete"
        self.session.complete_task(
            self.working.task_id,
            status=status,
            result={"exit_reason": reason, "steps": self.working.step},
        )
        log.info("task_finalise",
                 task_id=self.working.task_id,
                 status=status,
                 exit_reason=reason,
                 steps=self.working.step)
        self.audit.append("task_finalise",
                          task_id=self.working.task_id,
                          status=status,
                          exit_reason=reason,
                          steps=self.working.step)
        return {
            "task_id": self.working.task_id,
            "status": status,
            "exit_reason": reason,
            "steps": self.working.step,
            "hitl_pending": self.working.hitl_pending,
        }
