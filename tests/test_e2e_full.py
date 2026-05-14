"""Phase 6 — full end-to-end integration test.

Exercises the complete pipeline against deterministic step lists:
  init → perception(stub) → plan → executor → recovery → store → checkpoint
  → HITL pause → resolver writes resolution → resume → task_complete

This test does NOT require Playwright, Ollama, or any external service. It
verifies the *plumbing* between every Phase 0-5 component is intact.
"""
from __future__ import annotations

import threading

from agent.loop import AgentLoop
from agent.schemas import ActionResult
from hitl.queue import HITLQueue
from hitl.runner import HITLRunner
from memory.session import SessionMemory


class FlakyExecutor:
    """Fails the first time it sees a target, then succeeds — drives both
    the retry counter and the HITL/resume path depending on the threshold."""

    def __init__(self, fail_n_times: int = 1) -> None:
        self.fail_n_times = fail_n_times
        self.failures_remaining: dict[str, int] = {}
        self.history: list[tuple[str, str]] = []

    def execute(self, plan):
        self.history.append((plan.action_type, plan.target))
        if plan.action_type == "flag_human":
            return ActionResult(status="deferred", error_msg="hitl")
        remaining = self.failures_remaining.setdefault(plan.target, self.fail_n_times)
        if remaining > 0:
            self.failures_remaining[plan.target] -= 1
            return ActionResult(status="failed", error_msg="selector_unresolved")
        return ActionResult(status="ok", duration_ms=1)


def _claim_task() -> dict:
    return {
        "task_id": "e2e-claim",
        "task_type": "case2",
        "goal": "search claim end-to-end",
        "steps": [
            {"action_type": "navigate", "target": "https://sim/claim_search"},
            {"action_type": "type", "target": "claim_id", "value": "CLM-1"},
            {"action_type": "click", "target": "search"},
            {"action_type": "read", "target": "result_status"},
            {"action_type": "extract", "target": "result_amount",
             "is_financial": True},
        ],
    }


def test_e2e_happy_path_no_failures(session_store):
    """Five-step task with zero failures must complete with status=success
    and write one checkpoint + one action row per step."""
    executor = FlakyExecutor(fail_n_times=0)
    loop = AgentLoop(session=session_store, executor=executor,
                     agent_id="agent_e2e")
    runner = HITLRunner(loop=loop, sleep=lambda s: None)

    result = runner.run_task(_claim_task())
    assert result["status"] == "success"
    assert result["exit_reason"] == "task_complete"
    assert result["steps"] == 5

    actions = session_store.get_actions("e2e-claim")
    # Five ok results, in order.
    assert [a["result_status"] for a in actions] == ["ok"] * 5
    assert [a["action_type"] for a in actions] == [
        "navigate", "type", "click", "read", "extract",
    ]


def test_e2e_transient_failure_recovers_via_retry_counter(session_store):
    """Step 3 fails once; the deterministic-mode retry counter in
    AgentLoop._store should re-attempt and succeed on the second try."""
    executor = FlakyExecutor(fail_n_times=1)
    loop = AgentLoop(session=session_store, executor=executor,
                     agent_id="agent_e2e")
    runner = HITLRunner(loop=loop, sleep=lambda s: None)

    result = runner.run_task(_claim_task())
    assert result["status"] == "success"
    # Five steps × (1 retry + 1 success) = 10 executor calls, plus zero
    # flag_human (no HITL triggered since retries < RETRY_LIMIT).
    non_hitl = [h for h in executor.history if h[0] != "flag_human"]
    assert len(non_hitl) == 10


def test_e2e_hitl_pause_and_resume(session_store):
    """Step 1 fails 3 times → trips RETRY_LIMIT → HITL → human says
    'skip' → loop advances and completes the remaining 4 steps."""
    executor = FlakyExecutor(fail_n_times=99)  # navigate will never succeed
    loop = AgentLoop(session=session_store, executor=executor,
                     agent_id="agent_e2e")
    queue = HITLQueue(session=session_store, poll_interval_s=0)
    runner = HITLRunner(loop=loop, queue=queue, sleep=lambda s: None)

    # On resume, switch to an executor that always succeeds so the rest
    # of the task can finish.
    success_executor = FlakyExecutor(fail_n_times=0)

    import time as _time

    def resolver() -> None:
        deadline = _time.monotonic() + 5
        while _time.monotonic() < deadline:
            rows = session_store.list_hitl(status="pending")
            if rows:
                loop.executor = success_executor
                session_store.resolve_hitl(rows[0]["id"], {"action": "skip"})
                return
            _time.sleep(0.01)

    t = threading.Thread(target=resolver, daemon=True)
    t.start()
    result = runner.run_task(_claim_task())
    t.join(timeout=2)
    assert result["status"] == "success", result
    assert result["steps"] == 5

    # Exactly one HITL row, resolved.
    rows = session_store.list_hitl()
    assert len(rows) == 1
    assert rows[0]["status"] == "resolved"


def test_e2e_checkpoint_written_after_every_step(session_store):
    """Crash safety: every step must persist a checkpoint with monotonically
    increasing step values."""
    executor = FlakyExecutor(fail_n_times=0)
    loop = AgentLoop(session=session_store, executor=executor,
                     agent_id="agent_e2e")
    HITLRunner(loop=loop, sleep=lambda s: None).run_task(_claim_task())

    rows = session_store.conn.execute(
        "SELECT step FROM checkpoints WHERE task_id='e2e-claim' ORDER BY id ASC"
    ).fetchall()
    steps = [r["step"] for r in rows]
    assert steps == [0, 1, 2, 3, 4]  # one checkpoint per step
