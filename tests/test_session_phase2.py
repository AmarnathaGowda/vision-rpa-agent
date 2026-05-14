"""Tests for Phase-2 SessionMemory additions: start/complete task, log_action,
log_extraction, get_actions."""
from __future__ import annotations

from agent.schemas import ActionPlan, ActionResult


def test_start_and_complete_task(session_store):
    session_store.start_task("t1", "case1", "test goal", "agent_test")
    rows = session_store.conn.execute(
        "SELECT task_id, status FROM tasks WHERE task_id='t1'"
    ).fetchall()
    assert rows[0]["status"] == "running"

    session_store.complete_task("t1", status="success", result={"steps": 5})
    rows = session_store.conn.execute(
        "SELECT status, result_json FROM tasks WHERE task_id='t1'"
    ).fetchall()
    assert rows[0]["status"] == "success"
    assert "steps" in rows[0]["result_json"]


def test_log_action_and_get_actions(session_store):
    session_store.start_task("t2", "case1", "g", "agent_test")
    plan = ActionPlan(action_type="click", target="login", confidence=0.95)
    result = ActionResult(status="ok", duration_ms=42, screenshot_path="s.png")

    rid = session_store.log_action("t2", step=0, plan=plan, result=result)
    assert rid > 0

    rows = session_store.get_actions("t2")
    assert len(rows) == 1
    assert rows[0]["action_type"] == "click"
    assert rows[0]["target"] == "login"
    assert rows[0]["result_status"] == "ok"
    assert rows[0]["duration_ms"] == 42


def test_log_extraction_marks_financial(session_store):
    session_store.start_task("t3", "case2", "g", "agent_test")
    rid = session_store.log_extraction(
        "t3", field_name="amount", raw_value="$10,640.58",
        normalized="10640.58", confidence=0.93, method="vlm",
        is_financial=True,
    )
    assert rid > 0
    row = session_store.conn.execute(
        "SELECT field_name, is_financial FROM extractions WHERE id=?",
        (rid,),
    ).fetchone()
    assert row["field_name"] == "amount"
    assert row["is_financial"] == 1
