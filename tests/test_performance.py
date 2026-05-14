"""Phase 6 — performance smoke benchmarks.

These tests assert *budget envelopes*, not exact timings — so they're stable
across CI hardware. A regression that doubles the loop's per-step cost will
trip the budget; minor jitter will not.

Run with ``pytest tests/test_performance.py -v -s`` to see actual numbers.
"""
from __future__ import annotations

import time

from agent.loop import AgentLoop
from agent.schemas import ActionResult


class NoOpExecutor:
    def execute(self, plan):
        if plan.action_type == "flag_human":
            return ActionResult(status="deferred", error_msg="hitl")
        return ActionResult(status="ok", duration_ms=0)


def _deterministic_task(n_steps: int) -> dict:
    return {
        "task_id": f"perf-{n_steps}",
        "task_type": "case1",
        "goal": "perf",
        "steps": [{"action_type": "click", "target": f"btn-{i}"}
                  for i in range(n_steps)],
    }


def test_per_step_overhead_under_budget(session_store, monkeypatch):
    """A 40-step deterministic task with a no-op executor must complete in
    well under 5 seconds — i.e. the loop's per-step overhead (plan +
    audit + checkpoint + action log) stays under ~100ms even on a
    laptop. Budget is generous to avoid CI flakiness."""
    monkeypatch.setattr("config.settings.settings.max_loop_steps", 100)
    loop = AgentLoop(session=session_store, executor=NoOpExecutor(),
                     agent_id="agent_perf")
    start = time.monotonic()
    result = loop.run(_deterministic_task(40))
    elapsed = time.monotonic() - start

    assert result["status"] == "success", result
    assert result["steps"] == 40

    per_step_ms = (elapsed / 40) * 1000
    print(f"\n[perf] 40 steps in {elapsed:.3f}s — {per_step_ms:.1f} ms/step")
    assert elapsed < 5.0, f"loop overhead regressed: {elapsed:.2f}s for 40 steps"


def test_checkpoint_write_volume(session_store):
    """Sanity: every step writes exactly one checkpoint + one action row.
    Counts grow linearly with steps — guards against accidental N²
    write storms."""
    loop = AgentLoop(session=session_store, executor=NoOpExecutor(),
                     agent_id="agent_perf")
    loop.run(_deterministic_task(20))

    n_ck = session_store.conn.execute(
        "SELECT COUNT(*) AS c FROM checkpoints"
    ).fetchone()["c"]
    n_act = session_store.conn.execute(
        "SELECT COUNT(*) AS c FROM actions"
    ).fetchone()["c"]
    assert n_ck == 20
    assert n_act == 20


def test_session_memory_open_close_overhead(tmp_path, monkeypatch):
    """Spawning a SessionMemory should be cheap (schema is idempotent).
    Budget: 100 sequential opens in under 2 seconds."""
    monkeypatch.setattr("config.settings.settings.db_dir", str(tmp_path))
    from memory.session import SessionMemory

    start = time.monotonic()
    for i in range(100):
        s = SessionMemory(agent_id=f"agent_{i}")
        s.conn.close()
    elapsed = time.monotonic() - start
    print(f"\n[perf] 100 SessionMemory(open+close) in {elapsed:.3f}s")
    assert elapsed < 2.0
