"""Validation tests for the Pydantic models used by the loop."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.schemas import ActionPlan, ActionResult, ScreenState


def test_screen_state_minimum():
    s = ScreenState()
    assert s.app_type == "unknown"
    assert s.confidence == 0.0
    assert s.task_progress == "in_progress"


def test_screen_state_full():
    s = ScreenState(
        app_type="browser",
        state_summary="login page",
        current_url="http://localhost:8000",
        visible_elements=[{"label": "Sign in", "type": "button", "testid": "submit"}],
        confidence=0.93,
    )
    assert s.visible_elements[0].testid == "submit"


def test_screen_state_rejects_bad_confidence():
    with pytest.raises(ValidationError):
        ScreenState(confidence=1.4)


def test_screen_state_rejects_bad_app_type():
    with pytest.raises(ValidationError):
        ScreenState(app_type="mobile")


def test_action_plan_defaults():
    p = ActionPlan(action_type="click")
    assert p.confidence == 0.0
    assert p.requires_hitl is False
    assert p.cache_hit is False


def test_action_plan_rejects_bad_action_type():
    with pytest.raises(ValidationError):
        ActionPlan(action_type="teleport")


def test_action_result_defaults():
    r = ActionResult()
    assert r.status == "skipped"
    assert r.duration_ms == 0
