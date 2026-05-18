"""Provider factory — returns an LLMProvider gated by runtime_mode + LLM_PROVIDER.

Runtime modes (set via ``RUNTIME_MODE`` env or ``settings.runtime_mode``):
- ``client_onprem`` (default, safe) — local providers only (ollama / vLLM).
- ``production``                    — local providers only. Same safety
                                       contract; signals a deployed env.
- ``demo``                          — external providers (openai / claude)
                                       are permitted in addition to local.

Provider selection (``LLM_PROVIDER`` env or ``settings.llm_provider``):
- ``ollama``  (default) — local OpenAI-compatible endpoint (Ollama / vLLM).
- ``openai``           — OpenAI API. REQUIRES ``runtime_mode=demo``.
- ``claude``           — Anthropic Claude API. REQUIRES ``runtime_mode=demo``.

External providers are wrapped in ``RedactingProvider`` when
``settings.redact_external_payloads`` is True so PII / financial identifiers
never leave the host in cleartext. Local providers skip redaction by design.

The cached singleton is reset between tests via ``reset_provider()``.
"""
from __future__ import annotations

from agent.providers.base import LLMProvider

_cached_provider: LLMProvider | None = None

_EXTERNAL_PROVIDERS = {"openai", "claude"}
_DEMO_MODES = {"demo"}


class ProviderConfigError(RuntimeError):
    """Raised when the configured provider is not permitted by runtime_mode."""


def get_provider() -> LLMProvider:
    """Return the singleton provider, creating it on first call."""
    global _cached_provider
    if _cached_provider is not None:
        return _cached_provider

    from config.settings import settings

    mode = getattr(settings, "runtime_mode", "client_onprem").lower()
    name = getattr(settings, "llm_provider", "ollama").lower()

    # ── Safety gate: external providers require demo mode ─────────────
    if name in _EXTERNAL_PROVIDERS and mode not in _DEMO_MODES:
        raise ProviderConfigError(
            f"LLM_PROVIDER={name!r} is an external API provider but "
            f"RUNTIME_MODE={mode!r}. External providers are only permitted "
            f"when RUNTIME_MODE=demo. Set RUNTIME_MODE=demo explicitly or "
            f"switch LLM_PROVIDER to 'ollama'."
        )

    if name == "claude":
        _cached_provider = _build_claude(settings)
    elif name == "openai":
        _cached_provider = _build_openai(settings)
    else:
        _cached_provider = _build_ollama(settings)

    # Wrap external providers in the redaction decorator when configured.
    if name in _EXTERNAL_PROVIDERS and getattr(
        settings, "redact_external_payloads", True
    ):
        from agent.redaction import RedactingProvider
        _cached_provider = RedactingProvider(_cached_provider)

    return _cached_provider


def _build_ollama(settings) -> LLMProvider:
    from agent.providers.ollama_provider import OllamaProvider
    return OllamaProvider(
        base_url=settings.inference_url,
        model=settings.model_name,
        timeout=getattr(settings, "llm_timeout_s", 120.0),
        max_retries=getattr(settings, "llm_max_retries", 2),
    )


def _build_claude(settings) -> LLMProvider:
    import os
    api_key = getattr(settings, "anthropic_api_key", "") or os.environ.get(
        "ANTHROPIC_API_KEY", ""
    )
    if not api_key:
        raise ProviderConfigError(
            "LLM_PROVIDER=claude but ANTHROPIC_API_KEY is not set."
        )
    from agent.providers.claude_provider import ClaudeProvider
    return ClaudeProvider(
        api_key=api_key,
        model=getattr(settings, "claude_model", "claude-sonnet-4-6"),
        timeout=getattr(settings, "llm_timeout_s", 30.0),
        max_retries=getattr(settings, "llm_max_retries", 3),
    )


def _build_openai(settings) -> LLMProvider:
    import os
    api_key = getattr(settings, "openai_api_key", "") or os.environ.get(
        "OPENAI_API_KEY", ""
    )
    if not api_key:
        raise ProviderConfigError(
            "LLM_PROVIDER=openai but OPENAI_API_KEY is not set."
        )
    from agent.providers.openai_provider import OpenAIProvider
    return OpenAIProvider(
        api_key=api_key,
        model=getattr(settings, "openai_model", "gpt-4o-mini"),
        base_url=getattr(settings, "openai_base_url", "https://api.openai.com/v1"),
        timeout=getattr(settings, "llm_timeout_s", 30.0),
        max_retries=getattr(settings, "llm_max_retries", 3),
    )


def reset_provider() -> None:
    """Reset the cached provider singleton — use in tests to force re-creation."""
    global _cached_provider
    _cached_provider = None


__all__ = ["LLMProvider", "ProviderConfigError", "get_provider", "reset_provider"]
