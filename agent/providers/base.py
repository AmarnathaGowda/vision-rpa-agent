"""LLMProvider protocol — shared by OllamaProvider, ClaudeProvider, and test mocks."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    def complete(
        self,
        messages: list[dict],
        max_tokens: int = 512,
        temperature: float = 0.1,
    ) -> str:
        """Send a chat completion request; return the assistant text."""
        ...

    def complete_with_image(
        self,
        image_b64: str,
        mime: str,
        prompt: str,
        max_tokens: int = 512,
    ) -> str:
        """Vision call — base64 image + text prompt → assistant text."""
        ...
