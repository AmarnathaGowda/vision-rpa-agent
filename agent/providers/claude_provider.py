"""ClaudeProvider — Anthropic Claude API for demo/testing only.

IMPORTANT: This provider makes EXTERNAL API calls to Anthropic's servers.
It MUST NOT be used in production (CLAUDE.md non-negotiable: zero external
API calls). Enable it only via LLM_PROVIDER=claude in .env for demos.

Features:
- Prompt caching (cache_control: ephemeral) on system prompts — reduces
  input token cost ~70% for repeated loop iterations.
- Tenacity retry on transient API errors (timeout, connection).
- 30-second default timeout per request vs Ollama's 120-second default.
"""
from __future__ import annotations


class ClaudeProvider:
    """Anthropic Claude API provider (demo/testing mode only)."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        self._model = model
        self._max_retries = max_retries

    def complete(
        self,
        messages: list[dict],
        max_tokens: int = 512,
        temperature: float = 0.1,
    ) -> str:
        import anthropic
        from tenacity import (
            Retrying,
            retry_if_exception_type,
            stop_after_attempt,
            wait_exponential,
        )

        # Anthropic separates system from user messages.
        system_text = next(
            (m["content"] for m in messages if m["role"] == "system"), None
        )
        user_msgs = [m for m in messages if m["role"] != "system"]

        kwargs: dict = dict(
            model=self._model,
            max_tokens=max_tokens,
            messages=user_msgs,
        )
        if system_text:
            # cache_control: ephemeral caches this block for up to 5 minutes —
            # saves ~70% input tokens when the same system prompt is reused
            # across loop iterations.
            kwargs["system"] = [
                {
                    "type": "text",
                    "text": system_text,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        for attempt in Retrying(
            retry=retry_if_exception_type(
                (anthropic.APITimeoutError, anthropic.APIConnectionError)
            ),
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            reraise=True,
        ):
            with attempt:
                r = self._client.messages.create(**kwargs)
                return r.content[0].text

        return ""  # unreachable

    def complete_with_image(
        self,
        image_b64: str,
        mime: str,
        prompt: str,
        max_tokens: int = 512,
    ) -> str:
        messages = [{"role": "user", "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime,
                    "data": image_b64,
                },
            },
            {"type": "text", "text": prompt},
        ]}]
        return self.complete(messages, max_tokens)
