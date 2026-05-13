"""OpenAI-compatible client for local inference servers (Ollama / vLLM).

No external API calls — `base_url` always points at an on-prem endpoint
configured via INFERENCE_URL.
"""
from __future__ import annotations

from functools import lru_cache

from openai import OpenAI

from config.settings import settings


@lru_cache(maxsize=1)
def get_client() -> OpenAI:
    return OpenAI(
        base_url=settings.inference_url,
        api_key="ignored",
        timeout=120.0,
        max_retries=2,
    )


def strip_json_fence(raw: str) -> str:
    """Remove leading/trailing ```json fences some VLMs add."""
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) >= 2:
            raw = parts[1]
            if raw.startswith("json"):
                raw = raw[4:]
    return raw.strip()
