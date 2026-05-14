"""HITL queue — pause agent, write review request, poll for human resolution.

Phase 5 contract:

1. ``flag()``     — write a pending review to ``hitl_queue`` via SessionMemory.
                    The AgentLoop already does this in ``_route_to_hitl``; this
                    method is exposed for callers that want to raise a review
                    outside the loop (e.g. recovery directives, executors).
2. ``wait_for_resolution()`` — block (polling SQLite) until a human resolves
                    the review or the configured timeout elapses. Safe to call
                    from any thread; uses ``time.sleep`` between polls.
3. ``apply_resolution()`` — mutate the in-memory ``WorkingMemory`` so the
                    AgentLoop can ``resume()`` cleanly. Resolution shape:

        {"action": "approve" | "correct" | "skip" | "abort",
         "corrected_plan": {...optional ActionPlan fields...},
         "extracted_values": {...optional field overrides...},
         "note": "...",
         "resolver": "human_user"}

The HITLQueue never touches the loop directly — orchestration lives in the
runner / supervisor (see ``hitl/runner.py``).
"""
from __future__ import annotations

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
    ) -> None:
        self.session = session
        self.poll_interval_s = poll_interval_s if poll_interval_s is not None else self.DEFAULT_POLL_INTERVAL_S
        self.timeout_minutes = timeout_minutes if timeout_minutes is not None else settings.hitl_timeout_minutes

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
        while True:
            resolution = self.session.poll_hitl(task_id)
            if resolution is not None:
                log.info("hitl_resolved", task_id=task_id, resolution=resolution)
                return resolution
            if time.monotonic() >= deadline:
                raise HITLTimeoutError(
                    f"HITL review for task {task_id} timed out after "
                    f"{timeout_minutes or self.timeout_minutes} minutes"
                )
            sleep(self.poll_interval_s)

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
            step_key = str(working.step)
            working.retry_counts.pop(step_key, None)
            working.retry_counts.pop(f"recovery_{step_key}", None)
            log.info("hitl_apply_approve",
                     task_id=working.task_id, step=working.step)
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

        if action == "abort":
            working.task_complete = True
            working.exit_reason = "aborted_by_human"
            log.warning("hitl_apply_abort",
                        task_id=working.task_id, step=working.step, note=note)
            return

        raise ValueError(f"unknown HITL resolution action: {action!r}")
