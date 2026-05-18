"""OllamaProvider — OpenAI-compatible client for local Ollama / vLLM endpoints.

One threading.Semaphore prevents concurrent callers from flooding a single
Ollama process (which queues requests serially on CPU). Tenacity retries on
transient connection errors but not on model errors.
"""
from __future__ import annotations

import threading

from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

_ollama_semaphore = threading.Semaphore(1)


class OllamaProvider:
    """OpenAI-compatible provider for Ollama (dev) and vLLM (prod)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 120.0,
        max_retries: int = 2,
    ) -> None:
        from openai import OpenAI

        # max_retries=0 — we handle retries ourselves via tenacity so we can
        # apply the semaphore correctly and log each attempt.
        self._client = OpenAI(
            base_url=base_url,
            api_key="ignored",
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
        from openai import APIConnectionError, APITimeoutError

        with _ollama_semaphore:
            for attempt in Retrying(
                retry=retry_if_exception_type(
                    (httpx.TimeoutException, APITimeoutError, APIConnectionError)
                ),
                stop=stop_after_attempt(self._max_retries + 1),
                wait=wait_exponential(multiplier=1, min=2, max=10),
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
