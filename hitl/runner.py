"""HITL supervisor — runs an AgentLoop end-to-end, pausing on HITL and
resuming once a human resolves the review.

Flow:

    runner.run_task(task) →
        loop.run(task)
        while loop exited with hitl_pending:
            resolution = queue.wait_for_resolution(task_id)
            queue.apply_resolution(resolution, loop.working)
            loop.resume(loop.working, loop.task_goal)
        return final_result

The runner is the *only* place that knows how to bridge a paused agent and a
dashboard-driven resolution. Keep this layer thin — orchestration only.
"""
from __future__ import annotations

import time
from typing import Any

from agent.loop import AgentLoop
from config.logging_config import get_logger
from hitl.queue import HITLQueue, HITLTimeoutError

log = get_logger(__name__)


class HITLRunner:
    MAX_RESUMES = 10   # safety bound — same task can't bounce through HITL forever

    def __init__(self, loop: AgentLoop, queue: HITLQueue | None = None,
                 sleep: Any = time.sleep) -> None:
        self.loop = loop
        self.queue = queue or HITLQueue(session=loop.session)
        self._sleep = sleep

    def run_task(self, task: dict) -> dict:
        result = self.loop.run(task)
        resumes = 0
        while result.get("hitl_pending") and resumes < self.MAX_RESUMES:
            task_id = result["task_id"]
            log.info("hitl_runner_waiting", task_id=task_id, resumes=resumes)
            try:
                resolution = self.queue.wait_for_resolution(task_id, sleep=self._sleep)
            except HITLTimeoutError as e:
                log.warning("hitl_runner_timeout", task_id=task_id, error=str(e))
                return {**result, "status": "incomplete",
                        "exit_reason": "hitl_timeout"}

            assert self.loop.working is not None and self.loop.task_goal is not None
            self.queue.apply_resolution(resolution, self.loop.working)

            if self.loop.working.task_complete:
                # Human aborted — finalise without re-entering the loop.
                return self.loop._finalise()

            result = self.loop.resume(self.loop.working, self.loop.task_goal)
            resumes += 1

        if result.get("hitl_pending"):
            log.warning("hitl_runner_max_resumes",
                        task_id=result.get("task_id"), resumes=resumes)
        return result
