"""Mock LLM client for tests — no Ollama required."""
from __future__ import annotations
from unittest.mock import MagicMock
import json


def make_screen_state(app_type: str = "browser", summary: str = "Test page",
                      confidence: float = 0.92, **kwargs) -> dict:
    return {
        "app_type": app_type,
        "state_summary": summary,
        "current_url": kwargs.get("url", "http://localhost:8000"),
        "visible_elements": kwargs.get("elements", []),
        "error_present": kwargs.get("error", False),
        "blocking_modal": kwargs.get("modal", False),
        "task_progress": kwargs.get("progress", "in_progress"),
        "blocking_issue": kwargs.get("issue"),
        "confidence": confidence,
    }


def make_action_plan(action_type: str = "click", target: str = "submit",
                     confidence: float = 0.90, **kwargs) -> dict:
    return {
        "action_type": action_type,
        "target": target,
        "value": kwargs.get("value", ""),
        "reason": kwargs.get("reason", "test plan"),
        "confidence": confidence,
        "fallback": kwargs.get("fallback", ""),
        "is_financial": kwargs.get("is_financial", False),
        "requires_hitl": kwargs.get("requires_hitl", False),
        "cache_hit": kwargs.get("cache_hit", False),
    }


class MockOpenAIClient:
    """Drop-in replacement for openai.OpenAI — returns configurable JSON responses."""

    def __init__(self, responses: list[dict] | None = None) -> None:
        self._responses = responses or []
        self._call_count = 0
        self.chat = MagicMock()
        self.chat.completions.create = self._create

    def _create(self, **kwargs) -> MagicMock:
        if self._call_count < len(self._responses):
            content = json.dumps(self._responses[self._call_count])
        else:
            content = json.dumps(make_screen_state())

        self._call_count += 1
        response = MagicMock()
        response.choices[0].message.content = content
        return response

    def models(self) -> MagicMock:
        m = MagicMock()
        m.list.return_value = MagicMock(data=[])
        return m
