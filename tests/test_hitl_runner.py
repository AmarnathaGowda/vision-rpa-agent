"""Integration test: HITLRunner pauses on HITL, resumes after resolution."""
from __future__ import annotations

import threading

from agent.loop import AgentLoop
from agent.schemas import ActionResult
from hitl.queue import HITLQueue
from hitl.runner import HITLRunner
from memory.session import SessionMemory


class ScriptedExecutor:
    """Fails N times then succeeds — drives the deterministic retry path."""

    def __init__(self, fail_until_step: int = 1) -> None:
        self.fail_until_step = fail_until_step
        self.calls: list[str] = []

    def execute(self, plan):
        self.calls.append(plan.action_type)
        if plan.action_type == "flag_human":
            return ActionResult(status="deferred", error_msg="hitl")
        return ActionResult(status="ok", duration_ms=1)


def _two_step_task(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "task_type": "case1",
        "goal": "run two deterministic steps",
        "steps": [
            {"action_type": "click", "target": "btn-1"},
            {"action_type": "click", "target": "btn-2"},
        ],
    }


def test_runner_completes_when_no_hitl(session_store):
    loop = AgentLoop(session=session_store, executor=ScriptedExecutor(),
                     agent_id="agent_test")
    runner = HITLRunner(loop=loop, sleep=lambda s: None)
    result = runner.run_task(_two_step_task("t-ok"))
    assert result["status"] == "success"
    assert result["exit_reason"] == "task_complete"


class FailingExecutor:
    """Always fails — forces the retry guard to trip + route to HITL."""

    def execute(self, plan):
        if plan.action_type == "flag_human":
            return ActionResult(status="deferred", error_msg="hitl")
        return ActionResult(status="failed", error_msg="selector missing")


def test_runner_pauses_then_resumes_after_human_skip(session_store):
    """Loop fails first step → HITL → external resolver writes 'skip' →
    runner applies resolution → loop advances past the broken step and
    completes the second step."""
    executor = FailingExecutor()
    loop = AgentLoop(session=session_store, executor=executor,
                     agent_id="agent_test")
    queue = HITLQueue(session=session_store, poll_interval_s=0)
    runner = HITLRunner(loop=loop, queue=queue, sleep=lambda s: None)

    # Resolver thread: as soon as the runner has flagged HITL, resolve it.
    def resolver() -> None:
        for _ in range(200):
            rows = session_store.list_hitl(status="pending")
            if rows:
                # Skip the broken step; subsequent step will also fail but
                # we'll skip that one too.
                session_store.resolve_hitl(rows[0]["id"], {"action": "skip"})
                return

    t = threading.Thread(target=resolver, daemon=True)
    t.start()

    # Use a fresh executor that lets the second step succeed after the skip.
    class SkipThenOk:
        def __init__(self):
            self.fail_first = True

        def execute(self, plan):
            if plan.action_type == "flag_human":
                return ActionResult(status="deferred", error_msg="hitl")
            if self.fail_first:
                self.fail_first = False
                return ActionResult(status="failed", error_msg="missing")
            return ActionResult(status="ok", duration_ms=1)

    loop.executor = SkipThenOk()
    result = runner.run_task(_two_step_task("t-resume"))
    t.join(timeout=2)
    assert result["status"] == "success", result
    assert result["exit_reason"] == "task_complete"


def test_runner_aborts_when_human_says_abort(session_store):
    loop = AgentLoop(session=session_store, executor=FailingExecutor(),
                     agent_id="agent_test")
    queue = HITLQueue(session=session_store, poll_interval_s=0)
    runner = HITLRunner(loop=loop, queue=queue, sleep=lambda s: None)

    def resolver() -> None:
        for _ in range(200):
            rows = session_store.list_hitl(status="pending")
            if rows:
                session_store.resolve_hitl(rows[0]["id"], {"action": "abort"})
                return

    t = threading.Thread(target=resolver, daemon=True)
    t.start()
    result = runner.run_task(_two_step_task("t-abort"))
    t.join(timeout=2)
    assert result["exit_reason"] == "aborted_by_human"
