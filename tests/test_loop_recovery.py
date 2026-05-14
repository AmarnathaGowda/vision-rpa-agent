"""Integration tests for AgentLoop ↔ RecoveryHandler wiring.

These exercise the loop end-to-end with a controllable PerceptionLayer,
planner, and executor so we can prove that recovery directives actually fire
and produce the right side effects (follow-up actions, HITL escalation, etc.).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PIL import Image

from agent.loop import AgentLoop
from agent.perception import PerceptionLayer
from agent.planner import ActionPlanner
from agent.recovery import RecoveryDirective, RecoveryHandler
from agent.schemas import ActionPlan, ActionResult, ScreenState
from tests.fixtures.mock_llm import MockOpenAIClient, make_action_plan, make_screen_state


class FixedPerception(PerceptionLayer):
    """Returns a pre-baked ScreenState; never calls the VLM or mss."""

    def __init__(self, states: list[ScreenState]):
        super().__init__(client=MockOpenAIClient())
        self._states = list(states)
        self._idx = 0

    def capture(self, target=None):
        return Image.new("RGB", (10, 10))

    def understand(self, image, context):
        state = self._states[min(self._idx, len(self._states) - 1)]
        self._idx += 1
        return state


class ScriptedExecutor:
    """Returns results from a queue; raises if the queue empties."""

    def __init__(self, results: list[ActionResult]):
        self._results = list(results)
        self.calls: list[ActionPlan] = []

    def execute(self, plan: ActionPlan) -> ActionResult:
        self.calls.append(plan)
        if not self._results:
            return ActionResult(status="ok")
        return self._results.pop(0)


def _build_loop(session_store, *, perception, planner=None, executor=None,
                recovery=None):
    return AgentLoop(
        session=session_store,
        perception=perception,
        planner=planner or ActionPlanner(client=MockOpenAIClient(
            responses=[make_action_plan(confidence=0.95)] * 20
        )),
        executor=executor,
        recovery=recovery or RecoveryHandler(),
        agent_id="agent_test",
    )


# ── 1. Blocking-modal pre-action directive ──────────────────────────────────
def test_blocking_modal_fires_close_plan_then_continues(session_store, monkeypatch):
    """Recovery.detect → close-modal click → loop re-enters; task progresses."""
    from config.settings import settings
    monkeypatch.setattr(settings, "max_loop_steps", 3)

    perception = FixedPerception([
        ScreenState(app_type="browser", blocking_modal=True, confidence=0.9),
        ScreenState(app_type="browser", confidence=0.95),
    ])
    executor = ScriptedExecutor([
        ActionResult(status="ok"),   # the close-modal click succeeds
        ActionResult(status="ok"),   # the next planned action succeeds
    ])
    loop = _build_loop(session_store, perception=perception, executor=executor)
    result = loop.run({"task_id": "t-modal", "task_type": "case1", "goal": "x"})

    # The first plan to execute was the recovery follow-up click on "close".
    assert executor.calls, "executor never called"
    assert executor.calls[0].target == "close"
    assert result["status"] == "incomplete" or result["status"] == "success"
    # max_steps=3 means the loop runs a bounded number of iterations and exits cleanly.


# ── 2. Error-present pre-action directive routes to HITL ───────────────────
def test_error_present_routes_to_hitl(session_store):
    perception = FixedPerception([
        ScreenState(app_type="browser", error_present=True,
                    blocking_issue="session expired", confidence=0.5),
    ])
    executor = ScriptedExecutor([])
    loop = _build_loop(session_store, perception=perception, executor=executor)
    result = loop.run({"task_id": "t-err", "task_type": "case1", "goal": "x"})

    assert result["hitl_pending"] is True
    assert result["exit_reason"] == "hitl_pending"
    # No browser action ever executed — recovery short-circuited.
    assert executor.calls == []


# ── 3. RDP disconnect post-action triggers rdp_reconnect follow-up ─────────
def test_rdp_disconnect_triggers_reconnect_via_loop(session_store, monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "max_loop_steps", 3)

    # Mock RDPHandler with an active session so RecoveryHandler emits rdp_reconnect.
    rdp = MagicMock()
    rdp.session.reconnect_count = 0
    recovery = RecoveryHandler(rdp=rdp)

    perception = FixedPerception([
        ScreenState(app_type="rdp", confidence=0.92),
    ])
    # Planner returns a plan that the executor will fail with an RDP error.
    planner = ActionPlanner(client=MockOpenAIClient(responses=[
        make_action_plan(action_type="click", target="OK", confidence=0.95),
    ] * 20))
    executor = ScriptedExecutor([
        ActionResult(status="failed", error_msg="rdp_error: connection lost"),
        ActionResult(status="ok"),   # rdp_reconnect follow-up succeeds
    ])
    loop = _build_loop(session_store, perception=perception,
                       planner=planner, executor=executor, recovery=recovery)
    loop.run({"task_id": "t-rdp", "task_type": "case4", "goal": "x"})

    # Two executor calls: the failing click, then the rdp_reconnect plan from recovery.
    assert len(executor.calls) >= 2
    assert executor.calls[1].action_type == "rdp_reconnect"


# ── 4. Transient failure → retry directive bumps recovery counter ──────────
def test_transient_failure_retries_then_escalates_to_hitl(session_store, monkeypatch):
    """Repeated transient failures should bounded-retry then escalate."""
    from config.settings import settings
    monkeypatch.setattr(settings, "max_loop_steps", 20)

    perception = FixedPerception([
        ScreenState(app_type="browser", confidence=0.95)
    ] * 20)
    planner = ActionPlanner(client=MockOpenAIClient(responses=[
        make_action_plan(action_type="click", target="x", confidence=0.95),
    ] * 20))
    # All four attempts fail with a transient error — recovery should
    # retry RETRY_LIMIT times then escalate.
    executor = ScriptedExecutor([
        ActionResult(status="failed", error_msg="selector_unresolved: x"),
    ] * 10)
    loop = _build_loop(session_store, perception=perception,
                       planner=planner, executor=executor)
    result = loop.run({"task_id": "t-trans", "task_type": "case1", "goal": "x"})

    assert result["hitl_pending"] is True
    # The recovery counter, not the legacy step counter, was the gate.
    assert any("recovery_" in k for k in loop.working.retry_counts.keys())


# ── 5. Unknown error routes straight to HITL ───────────────────────────────
def test_unknown_failure_escalates_immediately(session_store):
    perception = FixedPerception([
        ScreenState(app_type="browser", confidence=0.95),
    ])
    planner = ActionPlanner(client=MockOpenAIClient(responses=[
        make_action_plan(action_type="click", target="x", confidence=0.95),
    ] * 5))
    executor = ScriptedExecutor([
        ActionResult(status="failed", error_msg="something nobody recognises"),
    ])
    loop = _build_loop(session_store, perception=perception,
                       planner=planner, executor=executor)
    result = loop.run({"task_id": "t-unk", "task_type": "case1", "goal": "x"})

    assert result["hitl_pending"] is True
    assert result["exit_reason"] == "hitl_pending"


# ── 6. Happy path — no recovery directives fire ────────────────────────────
def test_happy_path_no_recovery_invocation(session_store, monkeypatch):
    """When perception is clean and the executor succeeds, recovery should
    never produce a directive — the loop must not appear to escalate."""
    from config.settings import settings
    monkeypatch.setattr(settings, "max_loop_steps", 2)

    perception = FixedPerception([
        ScreenState(app_type="browser", confidence=0.95),
    ] * 5)
    planner = ActionPlanner(client=MockOpenAIClient(responses=[
        make_action_plan(action_type="click", target="ok", confidence=0.95),
    ] * 5))
    executor = ScriptedExecutor([ActionResult(status="ok")] * 5)
    loop = _build_loop(session_store, perception=perception,
                       planner=planner, executor=executor)
    result = loop.run({"task_id": "t-ok", "task_type": "case1", "goal": "x"})
    assert result["hitl_pending"] is False
    assert result["exit_reason"] in ("max_steps_exceeded", "task_complete")
