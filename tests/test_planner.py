"""Unit tests for ActionPlanner — HITL rules enforced deterministically."""
from __future__ import annotations

from agent.planner import ActionPlanner
from agent.schemas import ActionPlan, ScreenState
from config.settings import settings
from tests.fixtures.mock_llm import MockOpenAIClient, make_action_plan, make_screen_state


def _planner_with(plan_payload: dict) -> ActionPlanner:
    return ActionPlanner(client=MockOpenAIClient(responses=[plan_payload]))


def test_decide_returns_action_plan():
    planner = _planner_with(make_action_plan(action_type="click", target="login",
                                              confidence=0.95))
    screen = ScreenState(**make_screen_state())
    plan = planner.decide(screen, working={"step": 0, "retry_counts": {}}, goal="login")
    assert isinstance(plan, ActionPlan)
    assert plan.action_type == "click"
    assert plan.requires_hitl is False


def test_low_confidence_routes_to_hitl():
    low = settings.confidence_threshold - 0.1
    planner = _planner_with(make_action_plan(confidence=low))
    screen = ScreenState(**make_screen_state())
    plan = planner.decide(screen, working={"step": 0, "retry_counts": {}}, goal="x")
    assert plan.requires_hitl is True


def test_financial_action_requires_higher_confidence():
    # Above general threshold but below financial threshold.
    mid = (settings.confidence_threshold + settings.financial_confidence_threshold) / 2 - 0.01
    planner = _planner_with(make_action_plan(confidence=mid, is_financial=True))
    screen = ScreenState(**make_screen_state())
    plan = planner.decide(screen, working={"step": 0, "retry_counts": {}}, goal="x")
    assert plan.requires_hitl is True


def test_retry_limit_forces_flag_human():
    planner = ActionPlanner(
        client=MockOpenAIClient(responses=[make_action_plan(confidence=0.99)]),
        retry_limit=3,
    )
    screen = ScreenState(**make_screen_state())
    working = {"step": 5, "retry_counts": {"5": 3}}
    plan = planner.decide(screen, working=working, goal="x")
    assert plan.action_type == "flag_human"
    assert plan.requires_hitl is True
