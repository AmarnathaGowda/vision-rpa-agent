"""Unit tests for ActionRouter."""
from __future__ import annotations

from unittest.mock import MagicMock

from agent.router import ActionRouter
from agent.schemas import ActionPlan, ActionResult


def test_routes_browser_action():
    browser = MagicMock()
    browser.execute.return_value = ActionResult(status="ok")
    router = ActionRouter(browser=browser)
    result = router.execute(ActionPlan(action_type="click", target="x"))
    assert result.status == "ok"
    browser.execute.assert_called_once()


def test_noop_for_flag_human():
    router = ActionRouter(browser=MagicMock())
    result = router.execute(ActionPlan(action_type="flag_human", target="x"))
    assert result.status == "skipped"


def test_browser_missing_returns_failed():
    router = ActionRouter()
    result = router.execute(ActionPlan(action_type="click", target="x"))
    assert result.status == "failed"
    assert "browser" in result.error_msg
