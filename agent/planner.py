"""Action planning — ScreenState + goal → ActionPlan."""
from __future__ import annotations


class ActionPlanner:
    def decide(self, screen_state: dict, working: dict, goal) -> dict:
        raise NotImplementedError
