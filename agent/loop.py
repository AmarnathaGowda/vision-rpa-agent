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

    def __init__(
        self,
        session: SessionMemory,
        knowledge: Any | None = None,
        perception: PerceptionLayer | None = None,
        planner: ActionPlanner | None = None,
        executor: Any | None = None,
        audit: AuditLog | None = None,
        agent_id: str | None = None,
    ) -> None:
        self.session = session
        self.knowledge = knowledge
        self.perception = perception or PerceptionLayer()
        self.planner = planner or ActionPlanner(retry_limit=self.RETRY_LIMIT)
        self.executor = executor or StubExecutor()
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
            plan = self._reason(screen)

            if plan.requires_hitl:
                self._route_to_hitl(plan, screen)
                self._store(plan, ActionResult(status="deferred",
                                               error_msg="hitl_pending"), screen)
                continue  # _should_continue() will exit with exit_reason="hitl_pending"

            result = self._act(plan)
            self._store(plan, result, screen)

        return self._finalise()

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
            "confidence": plan.confidence,
        })

        if result.status == "failed":
            key = str(step)
            self.working.retry_counts[key] = self.working.retry_counts.get(key, 0) + 1
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
                          screenshot=result.screenshot_path)

    def _route_to_hitl(self, plan: ActionPlan, screen: ScreenState) -> None:
        assert self.working is not None
        reason = (
            f"financial action confidence {plan.confidence:.2f} below "
            f"{settings.financial_confidence_threshold}"
            if plan.is_financial
            else f"plan confidence {plan.confidence:.2f} below threshold "
            f"{settings.confidence_threshold}"
        )
        hitl_id = self.session.write_hitl(
            task_id=self.working.task_id,
            agent_id=self.agent_id,
            reason=reason,
            screenshot="",
            context={"plan": plan.model_dump(), "screen": screen.model_dump()},
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
