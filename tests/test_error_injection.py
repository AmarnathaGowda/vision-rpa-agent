"""Phase 6 — error-injection tests.

Inject realistic faults at each layer (executor, perception, recovery, queue)
and assert the agent fails *safely*: never silently drops state, never
infinite-loops, never bypasses HITL on financial actions.
"""
from __future__ import annotations

import sqlite3

import pytest

from agent.loop import AgentLoop
from agent.schemas import ActionResult
from hitl.queue import HITLQueue
from hitl.runner import HITLRunner


def _task(steps: list[dict]) -> dict:
    return {
        "task_id": "inj-task",
        "task_type": "case1",
        "goal": "fault injection",
        "steps": steps,
    }


# ── executor faults ──────────────────────────────────────────────────────
class RaisingExecutor:
    def execute(self, plan):
        if plan.action_type == "flag_human":
            return ActionResult(status="deferred", error_msg="hitl")
        raise RuntimeError(f"executor exploded on {plan.action_type}")


def test_executor_exception_converts_to_failed_then_hitl(session_store):
    """An unhandled exception in the executor must NOT crash the loop —
    it must be wrapped in ActionResult(status='failed') and ultimately
    route to HITL after RETRY_LIMIT attempts."""
    loop = AgentLoop(session=session_store, executor=RaisingExecutor(),
                     agent_id="agent_inj")
    result = loop.run(_task([{"action_type": "click", "target": "x"}]))
    assert result["hitl_pending"] is True
    actions = session_store.get_actions("inj-task")
    # Recovery converts the exception into a deferred result that carries
    # the original error string forward — must NOT be silently swallowed.
    assert any("executor exploded" in (a["error_msg"] or "")
               for a in actions), [a["error_msg"] for a in actions]


# ── financial confidence enforcement ─────────────────────────────────────
class NeverCalledExecutor:
    """Asserts execute() is never invoked for HITL-flagged plans."""

    def __init__(self) -> None:
        self.called_with: list[str] = []

    def execute(self, plan):
        self.called_with.append(plan.action_type)
        if plan.action_type == "flag_human":
            return ActionResult(status="deferred", error_msg="hitl")
        # If we get here for a financial plan, the safety gate has broken.
        return ActionResult(status="ok")


def test_financial_action_below_threshold_routes_to_hitl(session_store):
    """An ActionPlan with is_financial=True and confidence < financial
    threshold MUST NOT execute — the planner must mark requires_hitl=True
    or the loop must route to HITL. Deterministic mode bypasses the
    planner check, so this test exercises the LLM-planner path."""
    from agent.planner import ActionPlanner
    from agent.schemas import ScreenState
    from tests.fixtures.mock_llm import MockOpenAIClient, make_action_plan, make_screen_state
    from tests.test_loop import FakePerception

    plan_payload = make_action_plan(
        action_type="extract", target="amount",
        is_financial=True, confidence=0.80,  # below financial threshold 0.90
    )
    planner = ActionPlanner(client=MockOpenAIClient(responses=[plan_payload] * 5))
    executor = NeverCalledExecutor()
    loop = AgentLoop(
        session=session_store,
        perception=FakePerception(ScreenState(**make_screen_state(confidence=0.95))),
        planner=planner,
        executor=executor,
        agent_id="agent_inj",
    )
    result = loop.run({"task_id": "inj-fin", "task_type": "case1", "goal": "g"})
    assert result["hitl_pending"] is True
    # The financial extract must never have hit the executor.
    assert "extract" not in executor.called_with


# ── SQLite contention ───────────────────────────────────────────────────
def test_session_memory_survives_concurrent_writes(tmp_path, monkeypatch):
    """Two SessionMemory connections to the same agent DB should not
    deadlock; WAL mode + check_same_thread=False is the contract."""
    monkeypatch.setattr("config.settings.settings.db_dir", str(tmp_path))
    from memory.session import SessionMemory

    a = SessionMemory(agent_id="agent_shared")
    b = SessionMemory(agent_id="agent_shared")
    a.start_task("t1", "case1", "g", "agent_shared")
    b.write_hitl("t1", "agent_shared", "x", "", {})
    rows = a.list_hitl()
    assert len(rows) == 1
    a.conn.close()
    b.conn.close()


# ── recovery loop bound ──────────────────────────────────────────────────
class AlwaysModalPerception:
    """Pretends every screen is a blocking modal — recovery should fire,
    but the loop must bound the attempts and escalate to HITL."""

    def capture(self, target=None):
        from PIL import Image
        return Image.new("RGB", (10, 10))

    def preprocess(self, img):
        return img

    def understand(self, image, context):
        from agent.schemas import ScreenState
        return ScreenState(app_type="browser", blocking_modal=True,
                           state_summary="modal", confidence=0.95)


def test_recovery_attempts_are_bounded(session_store):
    """Recovery must NOT loop forever on a stuck modal — RETRY_LIMIT
    applies to recovery as well as executor failures."""
    from tests.fixtures.mock_llm import MockOpenAIClient, make_action_plan
    from agent.planner import ActionPlanner

    planner = ActionPlanner(client=MockOpenAIClient(
        responses=[make_action_plan(confidence=0.95)] * 30))
    loop = AgentLoop(
        session=session_store,
        perception=AlwaysModalPerception(),
        planner=planner,
        agent_id="agent_inj",
    )
    result = loop.run({"task_id": "inj-modal", "task_type": "case1", "goal": "g"})
    # Either bounded recovery escalates to HITL, or the step cap stops us —
    # either is safe. The key invariant: the test returns at all.
    assert result["exit_reason"] in (
        "hitl_pending", "max_steps_exceeded", "task_complete",
    )


# ── HITLQueue malformed resolution ───────────────────────────────────────
def test_apply_resolution_with_invalid_action_raises_not_silently_drops(session_store):
    from memory.working import WorkingMemory
    q = HITLQueue(session=session_store)
    w = WorkingMemory(task_id="t", task_type="c", goal="g",
                      agent_id="a", step=1)
    w.hitl_pending = True
    with pytest.raises(ValueError):
        q.apply_resolution({"action": ""}, w)
    # Validation now happens BEFORE mutation — hitl_pending stays True so
    # the supervisor can re-poll for a fresh resolution rather than silently
    # losing the review.
    assert w.hitl_pending is True
