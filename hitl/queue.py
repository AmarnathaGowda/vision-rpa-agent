"""HITL queue — pause agent, write review request, poll for human resolution."""
from __future__ import annotations


class HITLQueue:
    POLL_INTERVAL = 10
    TIMEOUT_MINUTES = 30

    def flag_and_wait(self, task_id: str, agent_id: str, reason: str,
                      page, working) -> dict:
        raise NotImplementedError

    def apply_resolution(self, resolution: dict, working) -> None:
        raise NotImplementedError
