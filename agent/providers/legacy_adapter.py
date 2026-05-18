"""_LegacyClientProvider — wraps an OpenAI-compatible client in the LLMProvider interface.

Allows existing code (and tests) that inject an ``openai.OpenAI`` client (or
``MockOpenAIClient``) to work unchanged while the production path migrates to
the full provider interface.
"""
from __future__ import annotations

from typing import Any


class _LegacyClientProvider:
    """Adapts ``client.chat.completions.create()`` to the LLMProvider protocol."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def complete(
        self,
        messages: list[dict],
        max_tokens: int = 512,
        temperature: float = 0.1,
    ) -> str:
        from config.settings import settings

        resp = self._client.chat.completions.create(
            model=settings.model_name,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""

    def complete_with_image(
        self,
        image_b64: str,
        mime: str,
        prompt: str,
        max_tokens: int = 512,
    ) -> str:
        messages = [{"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
            {"type": "text", "text": prompt},
        ]}]
        return self.complete(messages, max_tokens)
