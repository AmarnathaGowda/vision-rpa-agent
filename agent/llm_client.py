"""LLM client shim — delegates to the provider abstraction layer.

``get_provider()`` is the preferred entry point. It returns an ``LLMProvider``
backed by Ollama (default) or Claude API (demo mode, LLM_PROVIDER=claude).

``get_client()`` is kept for backward compatibility with code that still uses
the raw OpenAI client interface; it wraps the active provider's underlying
client where possible.

``strip_json_fence()`` strips ```json fences that some VLMs add to output.
"""
from __future__ import annotations

from agent.providers import get_provider, reset_provider  # noqa: F401 — re-exported


def get_client():
    """Return the underlying OpenAI client for the active Ollama/vLLM provider.

    Deprecated: prefer ``get_provider()`` for new code. This function exists
    so legacy call sites (e.g. direct ``client.chat.completions.create``) keep
    working during the migration.
    """
    from agent.providers import get_provider as _get_provider
    provider = _get_provider()
    # OllamaProvider exposes ``._client`` (openai.OpenAI).
    if hasattr(provider, "_client"):
        return provider._client
    # Fall back: return the provider itself — callers must use the provider API.
    return provider


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
