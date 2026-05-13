"""Integration test for AgentLoop — wires real loop with mocked I/O.

Verifies the Phase 1 exit criterion: loop captures → perceives → plans → logs,
without executing actions (stub executor).
"""
from __future__ import annotations

from PIL import Image

from agent.loop import AgentLoop, StubExecutor
from agent.perception import PerceptionLayer
from agent.planner import ActionPlanner
from agent.schemas import ScreenState
from tests.fixtures.mock_llm import MockOpenAIClient, make_action_plan, make_screen_state


class FakePerception(PerceptionLayer):
    """Bypasses mss — returns a synthetic image and a fixed ScreenState."""

    def __init__(self, state: ScreenState) -> None:
        super().__init__(client=MockOpenAIClient())
        self._state = state

    def capture(self, target=None):
        return Image.new("RGB", (100, 100))

    def understand(self, image, context):
        return self._state


def _make_loop(session_store, screen_state: ScreenState, plan_payload: dict) -> AgentLoop:
    perception = FakePerception(screen_state)
    planner = ActionPlanner(client=MockOpenAIClient(responses=[plan_payload] * 10))
    return AgentLoop(
        session=session_store,
        perception=perception,
        planner=planner,
        executor=StubExecutor(),
        agent_id="agent_test",
    )


def test_loop_routes_low_confidence_to_hitl(session_store):
    screen = ScreenState(**make_screen_state(confidence=0.95))
    plan_payload = make_action_plan(confidence=0.30)  # below threshold → HITL
    loop = _make_loop(session_store, screen, plan_payload)
    result = loop.run({"task_id": "t-hitl", "task_type": "case1", "goal": "test"})
    assert result["hitl_pending"] is True
    assert result["exit_reason"] == "hitl_pending"


def test_loop_respects_max_steps(session_store, monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "max_loop_steps", 2)
    screen = ScreenState(**make_screen_state(confidence=0.95))
    # Plan above threshold + executor returns "deferred" → loop never completes,
    # so it must hit the step cap.
    plan_payload = make_action_plan(confidence=0.95)
    loop = _make_loop(session_store, screen, plan_payload)
    result = loop.run({"task_id": "t-cap", "task_type": "case1", "goal": "test"})
    assert result["exit_reason"] == "max_steps_exceeded"
    assert result["steps"] == 2


def test_loop_writes_checkpoints(session_store, monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "max_loop_steps", 1)
    screen = ScreenState(**make_screen_state(confidence=0.95))
    plan_payload = make_action_plan(confidence=0.95)
    loop = _make_loop(session_store, screen, plan_payload)
    loop.run({"task_id": "t-ckpt", "task_type": "case1", "goal": "test"})
    ckpt = session_store.load_checkpoint("t-ckpt")
    assert ckpt is not None
    assert ckpt["task_id"] == "t-ckpt"


def test_resume_preserves_working_state(session_store, monkeypatch):
    """resume() must NOT reset WorkingMemory — it must continue from the existing step."""
    from agent.loop import TaskGoal
    from memory.working import WorkingMemory
    from config.settings import settings

    monkeypatch.setattr(settings, "max_loop_steps", 5)

    screen = ScreenState(**make_screen_state(confidence=0.95))
    plan_payload = make_action_plan(confidence=0.95)
    loop = _make_loop(session_store, screen, plan_payload)

    # Simulate a task that was interrupted after step 3 — working was checkpointed.
    pre_existing = WorkingMemory(
        task_id="t-resume", task_type="case2", goal="resume test", agent_id="agent_test",
    )
    pre_existing.step = 3
    pre_existing.extracted_values = {"loan_number": "0156312522"}

    task_goal = TaskGoal(
        task_id="t-resume", task_type="case2", goal="resume test",
        raw={"task_id": "t-resume", "task_type": "case2", "goal": "resume test"},
    )

    result = loop.resume(pre_existing, task_goal)

    # Loop must have started from step 3, not step 0.
    assert result["steps"] >= 3
    # Extracted values from the pre-existing working memory must be intact.
    assert loop.working.extracted_values["loan_number"] == "0156312522"


def test_hitl_reason_financial_vs_general(session_store):
    """_route_to_hitl must use the financial message for financial plans, general for others."""
    from agent.schemas import ActionPlan
    from unittest.mock import patch

    screen = ScreenState(**make_screen_state(confidence=0.95))
    plan_payload = make_action_plan(confidence=0.30)
    loop = _make_loop(session_store, screen, plan_payload)
    loop._init_task({"task_id": "t-reason", "task_type": "case1", "goal": "test"})

    # Non-financial plan
    general_plan = ActionPlan(
        action_type="click", target="btn", reason="test",
        confidence=0.50, is_financial=False,
    )
    captured_reasons: list[str] = []
    original_write_hitl = session_store.write_hitl

    def capturing_write_hitl(**kwargs):
        captured_reasons.append(kwargs.get("reason", ""))
        return original_write_hitl(**kwargs)

    with patch.object(session_store, "write_hitl", side_effect=capturing_write_hitl):
        loop._route_to_hitl(general_plan, screen)

    assert "threshold" in captured_reasons[0]
    assert "financial" not in captured_reasons[0]

    # Financial plan
    financial_plan = ActionPlan(
        action_type="extract", target="claim_amount", reason="test",
        confidence=0.80, is_financial=True,
    )
    with patch.object(session_store, "write_hitl", side_effect=capturing_write_hitl):
        loop._route_to_hitl(financial_plan, screen)

    assert "financial" in captured_reasons[1]
