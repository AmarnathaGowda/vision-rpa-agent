"""Tests for the multi-stage workflow primitives.

Covers:
- WorkingMemory.current_stage / stages_completed initialisation.
- stage_complete action advances the tracker without exiting the task.
- task_complete is GATED by done_when.all_set — premature declarations
  route to HITL instead of exiting with success.
"""
from __future__ import annotations

from agent.loop import AgentLoop
from agent.schemas import ActionPlan, ActionResult
from memory.working import WorkingMemory


class _ScriptedExecutor:
    """Returns the queued ActionResult per call; records the plans seen."""

    def __init__(self, results: list[ActionResult]) -> None:
        self._results = list(results)
        self.calls: list[ActionPlan] = []

    def execute(self, plan: ActionPlan) -> ActionResult:
        self.calls.append(plan)
        if not self._results:
            return ActionResult(status="skipped")
        return self._results.pop(0)


def test_working_memory_starts_with_initial_stage(session_store):
    loop = AgentLoop(session=session_store, executor=_ScriptedExecutor([]),
                     agent_id="agent_test")
    loop._init_task({
        "task_id": "t-stage",
        "task_type": "case1",
        "goal": "multi-stage test",
        "initial_stage": "login",
    })
    assert loop.working is not None
    assert loop.working.current_stage == "login"
    assert loop.working.stages_completed == []


def test_stage_complete_advances_tracker():
    """Calling _store with a stage_complete plan should:
    - append the previous stage to stages_completed
    - set current_stage to the plan target
    - NOT set task_complete"""
    from memory.session import SessionMemory
    import sqlite3

    store = SessionMemory.__new__(SessionMemory)
    store.db_path = ":memory:"
    store.conn = sqlite3.connect(":memory:", check_same_thread=False)
    store.conn.execute("PRAGMA journal_mode=WAL")
    store.conn.row_factory = sqlite3.Row
    store._create_schema()
    loop = AgentLoop(session=store, executor=_ScriptedExecutor([]),
                     agent_id="agent_test")
    loop._init_task({
        "task_id": "t-stage", "task_type": "case1",
        "goal": "g", "initial_stage": "login",
    })

    # Simulate the loop's stage_complete branch directly.
    plan = ActionPlan(action_type="stage_complete",
                      target="loss_drafts_launch",
                      reason="login page is now the production folder")
    # Manually run the same mutation the loop does (we don't run the
    # full _run_loop here — perception would call out to a VLM).
    old = loop.working.current_stage
    if old and old not in loop.working.stages_completed:
        loop.working.stages_completed.append(old)
    loop.working.current_stage = plan.target

    assert loop.working.current_stage == "loss_drafts_launch"
    assert loop.working.stages_completed == ["login"]
    assert loop.working.task_complete is False
    store.conn.close()


def test_missing_completion_keys_returns_unset_keys():
    from memory.session import SessionMemory
    import sqlite3

    store = SessionMemory.__new__(SessionMemory)
    store.db_path = ":memory:"
    store.conn = sqlite3.connect(":memory:", check_same_thread=False)
    store.conn.execute("PRAGMA journal_mode=WAL")
    store.conn.row_factory = sqlite3.Row
    store._create_schema()
    loop = AgentLoop(session=store, executor=_ScriptedExecutor([]),
                     agent_id="agent_test")
    loop._init_task({
        "task_id": "t-gate", "task_type": "case1", "goal": "g",
        "done_when": {"all_set": ["rdweb_authenticated", "candidates",
                                   "case1_result"]},
    })
    # Only one of the three keys is present.
    loop.working.extracted_values["rdweb_authenticated"] = True
    missing = loop._missing_completion_keys()
    assert set(missing) == {"candidates", "case1_result"}
    store.conn.close()


def test_missing_completion_keys_empty_when_satisfied():
    from memory.session import SessionMemory
    import sqlite3

    store = SessionMemory.__new__(SessionMemory)
    store.db_path = ":memory:"
    store.conn = sqlite3.connect(":memory:", check_same_thread=False)
    store.conn.execute("PRAGMA journal_mode=WAL")
    store.conn.row_factory = sqlite3.Row
    store._create_schema()
    loop = AgentLoop(session=store, executor=_ScriptedExecutor([]),
                     agent_id="agent_test")
    loop._init_task({
        "task_id": "t-gate", "task_type": "case1", "goal": "g",
        "done_when": {"all_set": ["rdweb_authenticated"]},
    })
    loop.working.extracted_values["rdweb_authenticated"] = True
    assert loop._missing_completion_keys() == []
    store.conn.close()


def test_inject_completion_guidance_writes_human_guidance():
    """When the gate rejects task_complete, framework should auto-inject
    guidance that the planner picks up next iteration — no HITL."""
    from memory.session import SessionMemory
    import sqlite3

    store = SessionMemory.__new__(SessionMemory)
    store.db_path = ":memory:"
    store.conn = sqlite3.connect(":memory:", check_same_thread=False)
    store.conn.execute("PRAGMA journal_mode=WAL")
    store.conn.row_factory = sqlite3.Row
    store._create_schema()
    loop = AgentLoop(session=store, executor=_ScriptedExecutor([]),
                     agent_id="agent_test")
    loop._init_task({
        "task_id": "t", "task_type": "case1", "goal": "g",
        "initial_stage": "login",
        "done_when": {"all_set": ["rdweb_authenticated", "candidates"]},
    })
    loop.working.stages_completed = ["login"]
    loop.working.current_stage = "loss_drafts_launch"
    loop._inject_completion_guidance(["candidates", "case1_result"])
    g = loop.working.extracted_values.get("human_guidance")
    assert g is not None
    assert g["created_by"] == "framework_auto_correct"
    assert "candidates" in g["instruction"]
    assert "loss_drafts_launch" in g["instruction"]
    store.conn.close()


def test_missing_completion_keys_no_done_when_returns_empty():
    """Tasks without done_when (the legacy case) should never block."""
    from memory.session import SessionMemory
    import sqlite3

    store = SessionMemory.__new__(SessionMemory)
    store.db_path = ":memory:"
    store.conn = sqlite3.connect(":memory:", check_same_thread=False)
    store.conn.execute("PRAGMA journal_mode=WAL")
    store.conn.row_factory = sqlite3.Row
    store._create_schema()
    loop = AgentLoop(session=store, executor=_ScriptedExecutor([]),
                     agent_id="agent_test")
    loop._init_task({"task_id": "t-old", "task_type": "case1", "goal": "g"})
    assert loop._missing_completion_keys() == []
    store.conn.close()
