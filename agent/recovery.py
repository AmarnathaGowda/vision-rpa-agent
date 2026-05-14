"""Unexpected-state detection and recovery.

The recovery layer is consulted by the loop when:
  - perception flags `error_present` / `blocking_modal`
  - the executor returns `status='failed'`
  - the RDPHandler reports a disconnect

It returns a `RecoveryDirective` telling the loop what to do next:
  - "retry"   — re-run the current step (counter incremented)
  - "skip"    — accept the failure, advance to the next step
  - "rdp_reconnect" — schedule an `rdp_reconnect` action
  - "hitl"    — pause and escalate
  - "abort"   — terminate the task as failed

This is intentionally a thin policy layer; the actual reconnect/modal-dismiss
mechanics live in the executors.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent.schemas import ActionPlan, ScreenState
from config.logging_config import get_logger

if TYPE_CHECKING:
    from executors.rdp import RDPHandler
    from memory.working import WorkingMemory

log = get_logger(__name__)


@dataclass
class RecoveryDirective:
    action: str   # retry | skip | rdp_reconnect | hitl | abort
    reason: str
    follow_up_plan: ActionPlan | None = None


# Failure-message substrings that we know how to handle.
RDP_DISCONNECT_MARKERS = (
    "rdp_error", "rdp window did not appear",
    "session has been disconnected", "connection lost",
)
TRANSIENT_MARKERS = (
    "selector_unresolved", "timeout", "stale element", "navigation timeout",
)


class RecoveryHandler:
    """Stateless policy — instance kept for future caching of error patterns.

    Note: the RDP reconnect limit is owned by ``RDPHandler.MAX_RECONNECTS``
    (the executor is the authoritative counter). Recovery reads it from the
    handler when one is provided; otherwise falls back to a safe default so
    the policy still works in isolation.
    """

    DEFAULT_MAX_RDP_RECONNECTS = 3
    MAX_TRANSIENT_RETRIES = 3

    def __init__(self, rdp: "RDPHandler | None" = None) -> None:
        self.rdp = rdp

    @property
    def max_rdp_reconnects(self) -> int:
        # ``getattr`` on a MagicMock returns a child MagicMock (not the default),
        # so we must verify the attribute is actually an int.
        candidate = getattr(self.rdp, "MAX_RECONNECTS", None) if self.rdp else None
        if isinstance(candidate, int):
            return candidate
        return self.DEFAULT_MAX_RDP_RECONNECTS

    # ── error-from-screen path ──────────────────────────────────────────────
    def detect(self, screen: ScreenState, working: "WorkingMemory") -> RecoveryDirective | None:
        """Consulted after perception, before plan execution."""
        if screen.blocking_modal:
            log.info("recovery_blocking_modal", summary=screen.state_summary)
            return RecoveryDirective(
                action="retry",
                reason="blocking_modal",
                follow_up_plan=ActionPlan(
                    action_type="click",
                    target="close",  # SelectorResolver will hunt for a close/dismiss control
                    fallback="[aria-label='Close']",
                    confidence=0.6,
                    reason="dismiss blocking modal",
                ),
            )
        if screen.error_present:
            log.info("recovery_error_present", issue=screen.blocking_issue)
            return RecoveryDirective(
                action="hitl",
                reason=f"error_present: {screen.blocking_issue or 'unspecified'}",
            )
        return None

    # ── error-from-result path ──────────────────────────────────────────────
    def recover(self, plan: ActionPlan, result, working: "WorkingMemory") -> RecoveryDirective:
        """Consulted after an executor returns status='failed'."""
        err = (result.error_msg or "").lower()
        step_key = str(working.step)
        attempts = working.retry_counts.get(step_key, 0)

        # 1. RDP disconnect → reconnect if quota left.
        if any(m in err for m in RDP_DISCONNECT_MARKERS):
            if self.rdp is None or not self.rdp.session:
                return RecoveryDirective(action="hitl",
                                         reason="rdp_disconnect_no_session")
            if self.rdp.session.reconnect_count >= self.max_rdp_reconnects:
                return RecoveryDirective(action="hitl",
                                         reason="rdp_reconnect_limit")
            return RecoveryDirective(
                action="rdp_reconnect",
                reason="rdp_disconnect",
                follow_up_plan=ActionPlan(
                    action_type="rdp_reconnect",
                    target=str(self.rdp.session.rdp_file),
                    app="rdp",
                    confidence=1.0,
                    reason="reconnect after detected disconnect",
                ),
            )

        # 2. Transient executor error → retry up to MAX_TRANSIENT_RETRIES.
        if any(m in err for m in TRANSIENT_MARKERS):
            if attempts < self.MAX_TRANSIENT_RETRIES:
                return RecoveryDirective(action="retry", reason=f"transient ({err[:60]})")
            return RecoveryDirective(action="hitl",
                                     reason=f"transient_retry_limit ({err[:60]})")

        # 3. Anything else → escalate.
        return RecoveryDirective(action="hitl", reason=f"unhandled: {err[:80]}")
