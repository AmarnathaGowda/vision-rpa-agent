"""HITL queue — pause agent, write review request, poll for human resolution.

HumanGuidance — the operator's instruction payload (see ``apply_resolution``).
The planner reads it from working memory and prioritises it over its own
inferred plan. Persisted to ``ui_patterns`` / ``sop_chunks`` knowledge
collections when ``save_to_memory`` / ``save_to_sop`` are set.

HITLQueue contract:
  1. flag()                  — write a pending review.
  2. wait_for_resolution()   — block (poll) until resolved.
  3. apply_resolution()      — mutate WorkingMemory so the loop can resume.
Orchestration lives in hitl/runner.py.
"""
from __future__ import annotations

from dataclasses import dataclass


# ── Safety: regex patterns that must NOT appear in operator instructions ──
# Operator hints become a system message in the planner prompt and may
# influence selector strings. We block obvious shell / JS injection
# patterns; the planner's action_type allowlist is the durable safety net.
_DANGEROUS_PATTERNS = [
    "<script", "</script>", "javascript:", "data:text/html",
    "eval(", "Function(", "subprocess", "os.system",
    "rm -rf", "; rm ", "| rm ", "$(", "`",
]


def _scrub_instruction(text: str) -> str:
    """Return ``text`` with dangerous fragments removed and length capped."""
    if not text:
        return ""
    s = str(text)
    for pat in _DANGEROUS_PATTERNS:
        s = s.replace(pat, "[REDACTED]")
    return s[:2000]


@dataclass
class HumanGuidance:
    """One operator's correction for one HITL review.

    Attached to ``working_memory.extracted_values["human_guidance"]`` so the
    planner can read it on the next iteration. The dataclass shape is the
    contract between the UI submission, the queue, and the planner.
    """
    instruction: str = ""
    corrected_target: str | None = None    # e.g. "Domain\\user name"
    corrected_value: str | None = None     # e.g. "vsonawane001" — value to TYPE
    selector_hint: str | None = None       # e.g. "[data-testid='login-username']"
    save_to_memory: bool = False           # → ui_patterns collection
    save_to_sop: bool = False              # → sop_chunks collection
    confidence: float = 0.9
    created_by: str = "floating-ui"

    def to_dict(self) -> dict:
        return {
            "instruction": _scrub_instruction(self.instruction),
            "corrected_target": self.corrected_target,
            "corrected_value": self.corrected_value,
            "selector_hint": self.selector_hint,
            "save_to_memory": bool(self.save_to_memory),
            "save_to_sop": bool(self.save_to_sop),
            "confidence": float(self.confidence),
            "created_by": self.created_by,
        }

    @classmethod
    def from_resolution(cls, resolution: dict) -> "HumanGuidance | None":
        """Extract a HumanGuidance from a resolution dict if guidance fields
        are present. Returns None for control-only resolutions."""
        keys = ("instruction", "corrected_target", "corrected_value",
                "selector_hint", "save_to_memory", "save_to_sop")
        if not any(resolution.get(k) for k in keys):
            return None
        return cls(
            instruction=resolution.get("instruction", "") or "",
            corrected_target=resolution.get("corrected_target") or None,
            corrected_value=resolution.get("corrected_value") or None,
            selector_hint=resolution.get("selector_hint") or None,
            save_to_memory=bool(resolution.get("save_to_memory", False)),
            save_to_sop=bool(resolution.get("save_to_sop", False)),
            confidence=float(resolution.get("confidence", 0.9)),
            created_by=resolution.get("resolver", "floating-ui"),
        )


import time
from typing import Any

from config.logging_config import get_logger
from config.settings import settings
from memory.session import SessionMemory
from memory.working import WorkingMemory

log = get_logger(__name__)


class HITLTimeoutError(RuntimeError):
    """Raised when a HITL review is not resolved within the timeout."""


class HITLQueue:
    DEFAULT_POLL_INTERVAL_S = 5

    def __init__(
        self,
        session: SessionMemory,
        poll_interval_s: float | None = None,
        timeout_minutes: int | None = None,
        knowledge: Any | None = None,
    ) -> None:
        self.session = session
        self.poll_interval_s = poll_interval_s if poll_interval_s is not None else self.DEFAULT_POLL_INTERVAL_S
        self.timeout_minutes = timeout_minutes if timeout_minutes is not None else settings.hitl_timeout_minutes
        # KnowledgeStore — optional. When supplied, save_to_memory and
        # save_to_sop flags in operator guidance write the correction to
        # ui_patterns / sop_chunks for cross-session reuse.
        self._knowledge = knowledge

    @property
    def knowledge(self):
        if self._knowledge is None:
            from memory.knowledge import get_knowledge_store
            self._knowledge = get_knowledge_store()
        return self._knowledge

    # ── enqueue ───────────────────────────────────────────────────────────
    def flag(
        self,
        task_id: str,
        agent_id: str,
        reason: str,
        screenshot: str = "",
        context: dict | None = None,
    ) -> int:
        """Write a pending HITL review and mark the task as ``hitl_wait``."""
        hitl_id = self.session.write_hitl(
            task_id=task_id,
            agent_id=agent_id,
            reason=reason,
            screenshot=screenshot,
            context=context or {},
            timeout_minutes=self.timeout_minutes,
        )
        log.info("hitl_flagged",
                 task_id=task_id, agent_id=agent_id, hitl_id=hitl_id, reason=reason)
        return hitl_id

    # ── poll ──────────────────────────────────────────────────────────────
    def wait_for_resolution(
        self,
        task_id: str,
        *,
        timeout_minutes: int | None = None,
        sleep: Any = time.sleep,
    ) -> dict:
        """Block until the latest HITL row for ``task_id`` is resolved.

        ``sleep`` is injectable so tests can run without real wall-clock waits.
        Raises ``HITLTimeoutError`` after ``timeout_minutes``.
        """
        budget_min = timeout_minutes if timeout_minutes is not None else self.timeout_minutes
        budget_s = budget_min * 60
        deadline = time.monotonic() + budget_s
        # Even when callers inject a no-op ``sleep`` (e.g. tests with
        # poll_interval_s=0), we yield the GIL once per iteration so
        # peer threads — like the dashboard or a resolver task — can run.
        yield_call = (lambda: time.sleep(0)) if self.poll_interval_s == 0 else None
        while True:
            resolution = self.session.poll_hitl(task_id)
            if resolution is not None:
                log.info("hitl_resolved", task_id=task_id, resolution=resolution)
                return resolution
            if time.monotonic() >= deadline:
                raise HITLTimeoutError(
                    f"HITL review for task {task_id} timed out after "
                    f"{budget_min} minutes"
                )
            if yield_call is not None:
                yield_call()
            sleep(self.poll_interval_s)

    def _persist_guidance_to_knowledge(
        self, guidance: HumanGuidance, working: WorkingMemory,
    ) -> None:
        """Persist operator corrections to the org-wide knowledge store.

        ui_patterns      — corrected_target + selector_hint pairs (so future
                           runs can resolve the same target via cache).
        sop_chunks       — full guidance text as a new SOP chunk (so the
                           planner can retrieve it next time it sees a
                           similar screen/goal).

        Best-effort: never raises into the caller.
        """
        try:
            store = self.knowledge
        except Exception as e:  # noqa: BLE001
            log.warning("knowledge_unavailable", error=str(e))
            return

        if guidance.save_to_memory and guidance.corrected_target and guidance.selector_hint:
            try:
                store.store_ui_pattern(
                    app=working.current_app or "browser",
                    element_desc=guidance.corrected_target,
                    selector=guidance.selector_hint,
                    action_type="hitl_taught",
                )
                # ui_patterns are buffered — flush so the next planner run sees it.
                if hasattr(store, "flush"):
                    store.flush()
                log.info("hitl_guidance_saved_to_memory",
                         target=guidance.corrected_target,
                         selector=guidance.selector_hint)
            except Exception as e:  # noqa: BLE001
                log.warning("hitl_save_to_memory_failed", error=str(e))

        if guidance.save_to_sop and (guidance.instruction or guidance.corrected_target):
            try:
                from memory.sop_loader import SOPChunk
                import hashlib

                body_parts = []
                if guidance.instruction:
                    body_parts.append(f"Operator instruction:\n{guidance.instruction}")
                if guidance.corrected_target:
                    body_parts.append(
                        f"Correct target name: {guidance.corrected_target}"
                    )
                if guidance.selector_hint:
                    body_parts.append(
                        f"Verified selector: {guidance.selector_hint}"
                    )
                text = (
                    f"# HITL-taught correction (task: {working.task_id})\n\n"
                    + "\n\n".join(body_parts)
                )
                cid = hashlib.sha256(text.encode()).hexdigest()[:32]
                chunk = SOPChunk(
                    id=f"hitl_{cid}",
                    text=text,
                    metadata={
                        "source": f"hitl/{working.task_id}",
                        "kind": "hitl_correction",
                        "created_by": guidance.created_by,
                    },
                )
                store.upsert_sop_chunks([chunk])
                log.info("hitl_guidance_saved_to_sop", chunk_id=chunk.id)
            except Exception as e:  # noqa: BLE001
                log.warning("hitl_save_to_sop_failed", error=str(e))

    # ── Flag-human-approval semantics ──────────────────────────────────
    _PROCEED_VERBS = (
        "proceed", "continue", "go ahead", "approve", "yes",
        "ok", "okay", "do it", "execute", "submit", "click",
    )

    @classmethod
    def _is_proceed_instruction(cls, text: str | None) -> bool:
        if not text:
            return False
        t = text.strip().lower()
        if not t:
            return False
        # Very short messages → check whole phrase. Longer → check tokens.
        return any(verb in t for verb in cls._PROCEED_VERBS)

    @staticmethod
    def _maybe_set_flag_human_override(working: WorkingMemory) -> None:
        """If the most recent plan in decisions_log was ``flag_human``, write
        a one-shot override into working memory so the next loop iteration
        executes the *concrete* action that was flagged (typically a click
        on the named target).

        The agent loop reads ``extracted_values["next_action_override"]``
        in ``_reason()``, runs it once, and clears it. No LLM call.
        """
        for entry in reversed(working.decisions_log):
            if "action_type" not in entry:
                continue
            if entry.get("action_type") == "flag_human":
                target = entry.get("target") or ""
                if not target:
                    return
                working.extracted_values["next_action_override"] = {
                    "action_type": "click",
                    "target": target,
                    "value": "",
                    "reason": f"operator-approved execution of flagged action on {target!r}",
                    "confidence": 1.0,
                    "requires_hitl": False,
                    "cache_hit": True,
                }
            return  # only check the most recent concrete plan

    @staticmethod
    def _clear_retry_counters(working: WorkingMemory) -> None:
        """Drop retry + recovery counters for the current step so the next
        planning iteration starts fresh."""
        step_key = str(working.step)
        working.retry_counts.pop(step_key, None)
        working.retry_counts.pop(f"recovery_{step_key}", None)

    # ── apply ─────────────────────────────────────────────────────────────
    def apply_resolution(
        self,
        resolution: dict,
        working: WorkingMemory,
    ) -> None:
        """Mutate ``working`` so ``AgentLoop.resume()`` continues correctly.

        Actions:
          approve  — retry the failed step (clear retry counters for it).
          correct  — accept human-provided values, advance past the step.
          skip     — advance past the step without re-attempting.
          abort    — terminate the task as ``aborted_by_human``.

        Always clears ``hitl_pending`` so the loop is runnable. Records the
        resolution in ``decisions_log`` for the audit trail.
        """
        action = (resolution.get("action") or "").lower()
        valid_actions = (
            "approve", "correct", "skip", "abort",
            "retry_with_values",
            # New guidance-bearing actions:
            "retry_with_hint",   # carries instruction + optional corrected_target
            "correct_target",    # operator names the real selector/target
            "teach_selector",    # corrected_target + save_to_memory
            "save_as_sop",       # capture this correction as an SOP chunk
        )
        if action not in valid_actions:
            # Validate BEFORE mutating working memory — otherwise a malformed
            # resolution would clear hitl_pending and silently swallow the
            # review. The runner can then re-poll for a fresh resolution.
            raise ValueError(f"unknown HITL resolution action: {action!r}")

        note = resolution.get("note", "")
        resolver = resolution.get("resolver", "unknown")

        working.hitl_pending = False
        working.decisions_log.append({
            "step": working.step,
            "hitl_resolution": action,
            "resolver": resolver,
            "note": note,
        })

        if action == "approve":
            # Clear retry counters for the current step so the planner can
            # re-attempt it without immediately re-tripping the retry guard.
            self._clear_retry_counters(working)
            # If the loop is paused on a flag_human plan, the operator's
            # "approve" means "do the thing you flagged" — not "ask me
            # again". Stash a one-shot override the loop consumes next turn.
            self._maybe_set_flag_human_override(working)
            log.info("hitl_apply_approve",
                     task_id=working.task_id, step=working.step,
                     override=bool(working.extracted_values.get("next_action_override")))
            return

        if action == "correct":
            overrides = resolution.get("extracted_values") or {}
            if overrides:
                working.extracted_values.update(overrides)
            # Treat the failed step as done — advance past it.
            working.step += 1
            log.info("hitl_apply_correct",
                     task_id=working.task_id, step=working.step,
                     fields_overridden=list(overrides.keys()))
            return

        if action == "skip":
            working.step += 1
            log.info("hitl_apply_skip",
                     task_id=working.task_id, step=working.step)
            return

        # ── Guidance-bearing actions ─────────────────────────────────
        # All of these stash a HumanGuidance into working memory so the
        # next planner iteration prioritises it over inferred reasoning.
        # They do NOT advance the step — the agent re-tries with hints.
        if action in ("retry_with_hint", "correct_target",
                       "teach_selector", "save_as_sop"):
            guidance = HumanGuidance.from_resolution(resolution)
            if guidance is None:
                # Caller passed the action verb but no actual guidance —
                # treat as plain retry.
                self._clear_retry_counters(working)
                log.info("hitl_apply_guidance_action_empty",
                         task_id=working.task_id, action=action)
                return
            # save_to_memory / save_to_sop flags trigger knowledge writes
            # in the runner / planner; the queue's job is to persist the
            # guidance so downstream layers can act on it.
            if action == "teach_selector":
                guidance.save_to_memory = True
            if action == "save_as_sop":
                guidance.save_to_sop = True
            working.extracted_values["human_guidance"] = guidance.to_dict()
            self._clear_retry_counters(working)
            # Best-effort knowledge persistence — failures log but never
            # abort the resume (memory is an enhancement, not a hard dep).
            self._persist_guidance_to_knowledge(guidance, working)
            # Deterministic override: if the operator gave both a target
            # AND a value, build the type action they clearly want next.
            # The loop's _reason() picks this up and runs it without
            # calling the LLM — guarantees their input takes effect.
            if guidance.corrected_target and guidance.corrected_value is not None:
                working.extracted_values["next_action_override"] = {
                    "action_type": "type",
                    "target": guidance.corrected_target,
                    "value": guidance.corrected_value,
                    "reason": (f"operator-supplied: type {guidance.corrected_value!r} "
                               f"into {guidance.corrected_target!r}"),
                    "confidence": 1.0,
                    "requires_hitl": False,
                    "cache_hit": True,
                }
                log.info("hitl_value_override_queued",
                         task_id=working.task_id,
                         target=guidance.corrected_target,
                         value_length=len(guidance.corrected_value))
            # When guidance has no corrected target / selector but reads
            # like a "go ahead" instruction (e.g. "please proceed"), and the
            # paused plan was a flag_human gate, the operator clearly means
            # "execute the flagged action". Set the same one-shot override.
            if self._is_proceed_instruction(guidance.instruction) \
                    and not guidance.corrected_target \
                    and not guidance.selector_hint:
                self._maybe_set_flag_human_override(working)
            log.info("hitl_apply_guidance",
                     task_id=working.task_id, step=working.step,
                     action=action,
                     has_corrected_target=bool(guidance.corrected_target),
                     has_selector_hint=bool(guidance.selector_hint),
                     save_to_memory=guidance.save_to_memory,
                     save_to_sop=guidance.save_to_sop,
                     override=bool(working.extracted_values.get("next_action_override")))
            return

        if action == "retry_with_values":
            # Operator provided missing data (e.g. a credential). Merge into
            # extracted_values, clear the retry counters for the current
            # step so the next iteration starts fresh, but DO NOT advance —
            # we want to retry the same step now that the values are present.
            overrides = resolution.get("extracted_values") or {}
            if overrides:
                working.extracted_values.update(overrides)
            step_key = str(working.step)
            working.retry_counts.pop(step_key, None)
            working.retry_counts.pop(f"recovery_{step_key}", None)
            log.info("hitl_apply_retry_with_values",
                     task_id=working.task_id, step=working.step,
                     fields_overridden=list(overrides.keys()))
            return

        if action == "abort":
            working.task_complete = True
            working.exit_reason = "aborted_by_human"
            log.warning("hitl_apply_abort",
                        task_id=working.task_id, step=working.step, note=note)
            return

        # Unreachable — validated above.
        raise AssertionError(f"unhandled validated action: {action!r}")
