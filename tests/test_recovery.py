"""Tests for RecoveryHandler — policy mapping from failures to directives."""
from __future__ import annotations

from unittest.mock import MagicMock

from agent.recovery import RecoveryHandler
from agent.schemas import ActionPlan, ActionResult, ScreenState
from memory.working import WorkingMemory


def _working(step: int = 0, retries: dict | None = None) -> WorkingMemory:
    wm = WorkingMemory(task_id="t", task_type="case4", goal="g", agent_id="a")
    wm.step = step
    wm.retry_counts = retries or {}
    return wm


def test_blocking_modal_returns_retry_with_close_plan():
    rh = RecoveryHandler()
    screen = ScreenState(app_type="browser", blocking_modal=True, confidence=0.9)
    directive = rh.detect(screen, _working())
    assert directive is not None
    assert directive.action == "retry"
    assert directive.follow_up_plan.action_type == "click"


def test_error_present_routes_to_hitl():
    rh = RecoveryHandler()
    screen = ScreenState(app_type="browser", error_present=True,
                         blocking_issue="session expired", confidence=0.5)
    directive = rh.detect(screen, _working())
    assert directive.action == "hitl"
    assert "session expired" in directive.reason


def test_no_anomaly_returns_none():
    rh = RecoveryHandler()
    screen = ScreenState(app_type="browser", confidence=0.95)
    assert rh.detect(screen, _working()) is None


def test_rdp_disconnect_triggers_reconnect_directive():
    rdp = MagicMock()
    rdp.session.reconnect_count = 0
    rh = RecoveryHandler(rdp=rdp)
    plan = ActionPlan(action_type="click", target="OK", app="desktop")
    result = ActionResult(status="failed", error_msg="rdp_error: connection lost")
    directive = rh.recover(plan, result, _working(step=4))
    assert directive.action == "rdp_reconnect"
    assert directive.follow_up_plan.action_type == "rdp_reconnect"


def test_rdp_disconnect_without_session_routes_to_hitl():
    rdp = MagicMock()
    rdp.session = None
    rh = RecoveryHandler(rdp=rdp)
    plan = ActionPlan(action_type="click", target="OK")
    result = ActionResult(status="failed", error_msg="rdp_error: session has been disconnected")
    directive = rh.recover(plan, result, _working(step=4))
    assert directive.action == "hitl"


def test_transient_error_retries_then_hitl():
    rh = RecoveryHandler()
    plan = ActionPlan(action_type="click", target="x")
    result = ActionResult(status="failed", error_msg="selector_unresolved: missing")

    # First failure — retry.
    d1 = rh.recover(plan, result, _working(step=1, retries={"1": 0}))
    assert d1.action == "retry"

    # Third failure — escalate.
    d2 = rh.recover(plan, result, _working(step=1, retries={"1": rh.MAX_TRANSIENT_RETRIES}))
    assert d2.action == "hitl"


def test_unknown_error_goes_straight_to_hitl():
    rh = RecoveryHandler()
    plan = ActionPlan(action_type="click", target="x")
    result = ActionResult(status="failed", error_msg="something nobody recognises")
    directive = rh.recover(plan, result, _working())
    assert directive.action == "hitl"


def test_rdp_reconnect_limit_escalates():
    rdp = MagicMock()
    rdp.session.reconnect_count = 3
    rh = RecoveryHandler(rdp=rdp)
    plan = ActionPlan(action_type="click", target="OK")
    result = ActionResult(status="failed", error_msg="rdp_error: connection lost")
    directive = rh.recover(plan, result, _working())
    assert directive.action == "hitl"
    assert "reconnect_limit" in directive.reason
