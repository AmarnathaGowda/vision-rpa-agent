"""Unit tests for HITLQueue: flag → wait_for_resolution → apply_resolution."""
from __future__ import annotations

import pytest

from hitl.queue import HITLQueue, HITLTimeoutError
from memory.working import WorkingMemory


def _make_working(step: int = 2) -> WorkingMemory:
    w = WorkingMemory(task_id="t1", task_type="case1", goal="g",
                      agent_id="agent_test", step=step)
    w.retry_counts[str(step)] = 3
    w.retry_counts[f"recovery_{step}"] = 1
    w.hitl_pending = True
    return w


def test_flag_writes_pending_row(session_store):
    q = HITLQueue(session=session_store)
    session_store.start_task("t1", "case1", "g", "agent_test")
    hid = q.flag("t1", "agent_test", reason="low_conf",
                 context={"plan": {"action_type": "type"}})
    rows = session_store.list_hitl()
    assert len(rows) == 1
    assert rows[0]["id"] == hid
    assert rows[0]["status"] == "pending"
    assert rows[0]["reason"] == "low_conf"


def test_wait_for_resolution_returns_payload(session_store):
    session_store.start_task("t1", "case1", "g", "agent_test")
    q = HITLQueue(session=session_store, poll_interval_s=0)
    hid = q.flag("t1", "agent_test", reason="x")
    session_store.resolve_hitl(hid, {"action": "approve", "resolver": "u"})

    sleeps: list[float] = []
    payload = q.wait_for_resolution("t1", sleep=sleeps.append)
    assert payload["action"] == "approve"
    assert sleeps == []  # already resolved, no sleep


def test_wait_for_resolution_polls_then_resolves(session_store):
    session_store.start_task("t1", "case1", "g", "agent_test")
    q = HITLQueue(session=session_store, poll_interval_s=0.01)
    hid = q.flag("t1", "agent_test", reason="x")

    calls = {"n": 0}

    def fake_sleep(_s: float) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            session_store.resolve_hitl(hid, {"action": "skip"})

    payload = q.wait_for_resolution("t1", sleep=fake_sleep)
    assert payload["action"] == "skip"
    assert calls["n"] >= 2


def test_wait_for_resolution_times_out(session_store):
    session_store.start_task("t1", "case1", "g", "agent_test")
    q = HITLQueue(session=session_store, poll_interval_s=0.001,
                  timeout_minutes=0)  # 0 → instant timeout
    q.flag("t1", "agent_test", reason="x")
    with pytest.raises(HITLTimeoutError):
        q.wait_for_resolution("t1", sleep=lambda s: None)


def test_apply_resolution_approve_clears_retry_counts(session_store):
    q = HITLQueue(session=session_store)
    w = _make_working(step=2)
    q.apply_resolution({"action": "approve", "resolver": "u"}, w)
    assert w.hitl_pending is False
    assert w.step == 2  # not advanced
    assert "2" not in w.retry_counts
    assert "recovery_2" not in w.retry_counts


def test_apply_resolution_correct_advances_and_merges_values(session_store):
    q = HITLQueue(session=session_store)
    w = _make_working(step=2)
    w.extracted_values = {"existing": "v"}
    q.apply_resolution(
        {"action": "correct",
         "extracted_values": {"claim_no": "CL-001"},
         "resolver": "u"},
        w,
    )
    assert w.hitl_pending is False
    assert w.step == 3
    assert w.extracted_values == {"existing": "v", "claim_no": "CL-001"}


def test_apply_resolution_skip_advances(session_store):
    q = HITLQueue(session=session_store)
    w = _make_working(step=2)
    q.apply_resolution({"action": "skip"}, w)
    assert w.step == 3
    assert w.hitl_pending is False


def test_apply_resolution_retry_with_values_merges_and_clears_retries(session_store):
    """retry_with_values: merge extracted_values, clear retry counts, DO NOT advance."""
    q = HITLQueue(session=session_store)
    w = _make_working(step=2)
    w.extracted_values = {"existing": "v"}
    q.apply_resolution(
        {"action": "retry_with_values",
         "extracted_values": {"RDWEB_PASSWORD": "Welcome@123"}},
        w,
    )
    assert w.hitl_pending is False
    assert w.step == 2  # not advanced
    assert w.extracted_values == {"existing": "v", "RDWEB_PASSWORD": "Welcome@123"}
    # Retry counters for the step cleared so executor gets a fresh attempt.
    assert "2" not in w.retry_counts
    assert "recovery_2" not in w.retry_counts


def test_apply_resolution_abort_terminates(session_store):
    q = HITLQueue(session=session_store)
    w = _make_working(step=2)
    q.apply_resolution({"action": "abort", "note": "human stop"}, w)
    assert w.task_complete is True
    assert w.exit_reason == "aborted_by_human"
    assert w.hitl_pending is False


def test_apply_resolution_unknown_action_raises(session_store):
    q = HITLQueue(session=session_store)
    w = _make_working(step=1)
    with pytest.raises(ValueError):
        q.apply_resolution({"action": "explode"}, w)
