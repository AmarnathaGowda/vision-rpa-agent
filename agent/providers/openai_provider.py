"""OpenAIProvider — external OpenAI API for demo/testing only.

⚠️  EXTERNAL CALL — this provider sends data to api.openai.com.

Architectural contract:
- Must NOT be selected when ``settings.runtime_mode != "demo"`` — the provider
  factory in ``agent/providers/__init__.py`` enforces this gate at startup.
- Prompts are passed through ``agent/redaction.redact_prompt()`` before send
  when ``settings.redact_external_payloads`` is True. The provider does not
  validate redaction itself — that lives in the factory wrapper.
- Streaming is not used: the planner needs a complete JSON object before it
  can parse it, and the perception layer is the same. Streaming would only
  reduce visible latency, not real latency — skipped for now.

Resilience:
- 30s default timeout (vs 120s for Ollama) — external APIs are faster but a
  hung request shouldn't pause the loop indefinitely.
- Tenacity retry on ``APITimeoutError`` / ``APIConnectionError`` (not on
  ``RateLimitError`` or ``AuthenticationError`` — those need human action).
- ``RateLimitError`` retried with longer backoff via a separate predicate so
  bursty demos don't fail-fast on 429.
"""
from __future__ import annotations


class OpenAIProvider:
    """OpenAI API provider (demo-only, gated by runtime_mode)."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 30.0,
        max_retries: int = 8,
    ) -> None:
        from openai import OpenAI

        if not api_key:
            raise RuntimeError(
                "OpenAIProvider requires an API key. Set OPENAI_API_KEY."
            )
        # max_retries=0 here so tenacity (below) is the single retry layer.
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
        )
        self._model = model
        self._max_retries = max_retries
        self._timeout = timeout

    def complete(
        self,
        messages: list[dict],
        max_tokens: int = 512,
        temperature: float = 0.1,
    ) -> str:
        import httpx
        from openai import (
            APIConnectionError,
            APITimeoutError,
            RateLimitError,
        )
        from tenacity import (
            Retrying,
            retry_if_exception_type,
            stop_after_attempt,
            wait_exponential,
        )

        transient = (
            httpx.TimeoutException,
            APITimeoutError,
            APIConnectionError,
            RateLimitError,
        )

        for attempt in Retrying(
            retry=retry_if_exception_type(transient),
            stop=stop_after_attempt(self._max_retries + 1),
            # 2, 4, 8, 16, 30, 60, 60, 60, 60 — total ~5min, enough to
            # ride out a full TPM-minute reset on bursty demos.
            wait=wait_exponential(multiplier=1, min=2, max=60),
            reraise=True,
        ):
            with attempt:
                resp = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                return resp.choices[0].message.content or ""
        return ""  # unreachable; satisfies type checker

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
