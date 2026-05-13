"""Core agent loop — observe → reason → act → store."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.session import SessionMemory
    from memory.knowledge import KnowledgeStore


class AgentLoop:
    def __init__(self, session: "SessionMemory", knowledge: "KnowledgeStore") -> None:
        self.session = session
        self.knowledge = knowledge

    def run(self, task) -> dict:
        raise NotImplementedError

    def resume(self, working, task_goal) -> dict:
        raise NotImplementedError
