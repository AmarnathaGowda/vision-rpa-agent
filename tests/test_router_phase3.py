"""Phase-3 routing rules — desktop / rdp / file scopes + plan.app override."""
from __future__ import annotations

from unittest.mock import MagicMock

from agent.router import ActionRouter
from agent.schemas import ActionPlan, ActionResult


def _stub_executor():
    ex = MagicMock()
    ex.execute.return_value = ActionResult(status="ok")
    return ex


def test_routes_select_option_to_desktop():
    desktop = _stub_executor()
    router = ActionRouter(desktop=desktop)
    router.execute(ActionPlan(action_type="select_option", target="x", value="A"))
    desktop.execute.assert_called_once()


def test_routes_file_navigate_to_file_executor():
    file_ex = _stub_executor()
    router = ActionRouter(file=file_ex)
    router.execute(ActionPlan(action_type="file_navigate", target="C:/data"))
    file_ex.execute.assert_called_once()


def test_routes_rdp_launch_to_rdp_handler():
    rdp = _stub_executor()
    router = ActionRouter(rdp=rdp)
    router.execute(ActionPlan(action_type="rdp_launch", target="x.rdp"))
    rdp.execute.assert_called_once()


def test_plan_app_overrides_default_routing():
    """When plan.app is set, the router honours it even if action_type defaults elsewhere."""
    browser = _stub_executor()
    desktop = _stub_executor()
    router = ActionRouter(browser=browser, desktop=desktop)
    # 'click' defaults to browser, but plan.app='desktop' forces the desktop path.
    router.execute(ActionPlan(action_type="click", target="OK", app="desktop"))
    desktop.execute.assert_called_once()
    browser.execute.assert_not_called()


def test_missing_executor_returns_failed_with_scope_in_message():
    router = ActionRouter()  # nothing wired
    r = router.execute(ActionPlan(action_type="rdp_launch", target="x"))
    assert r.status == "failed"
    assert "rdp" in r.error_msg
