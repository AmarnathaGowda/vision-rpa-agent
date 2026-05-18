"""Provider factory + OpenAIProvider + redaction tests.

No network calls — OpenAI/Anthropic clients are not constructed; we exercise
the factory's mode-gate and redaction wrapper directly.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.providers import (
    ProviderConfigError,
    get_provider,
    reset_provider,
)
from agent.redaction import (
    RedactingProvider,
    redact_messages,
    redact_text,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_provider()
    yield
    reset_provider()


# ── Runtime-mode gate ────────────────────────────────────────────────────
def test_factory_blocks_openai_outside_demo_mode(monkeypatch):
    monkeypatch.setattr("config.settings.settings.runtime_mode", "production")
    monkeypatch.setattr("config.settings.settings.llm_provider", "openai")
    monkeypatch.setattr("config.settings.settings.openai_api_key", "sk-test")
    with pytest.raises(ProviderConfigError, match="external API provider"):
        get_provider()


def test_factory_blocks_claude_outside_demo_mode(monkeypatch):
    monkeypatch.setattr("config.settings.settings.runtime_mode", "client_onprem")
    monkeypatch.setattr("config.settings.settings.llm_provider", "claude")
    monkeypatch.setattr("config.settings.settings.anthropic_api_key", "sk-test")
    with pytest.raises(ProviderConfigError, match="external API provider"):
        get_provider()


def test_factory_allows_ollama_in_every_mode(monkeypatch):
    for mode in ("client_onprem", "production", "demo"):
        reset_provider()
        monkeypatch.setattr("config.settings.settings.runtime_mode", mode)
        monkeypatch.setattr("config.settings.settings.llm_provider", "ollama")
        provider = get_provider()
        # OllamaProvider has a ._client (openai.OpenAI).
        assert hasattr(provider, "_client")


def test_factory_requires_api_key_when_openai_in_demo(monkeypatch):
    monkeypatch.setattr("config.settings.settings.runtime_mode", "demo")
    monkeypatch.setattr("config.settings.settings.llm_provider", "openai")
    monkeypatch.setattr("config.settings.settings.openai_api_key", "")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderConfigError, match="OPENAI_API_KEY"):
        get_provider()


def test_factory_wraps_external_in_redacting_provider(monkeypatch):
    """When runtime_mode=demo + LLM_PROVIDER=openai + redact_external_payloads=True,
    the returned provider is a RedactingProvider decorator."""
    monkeypatch.setattr("config.settings.settings.runtime_mode", "demo")
    monkeypatch.setattr("config.settings.settings.llm_provider", "openai")
    monkeypatch.setattr("config.settings.settings.openai_api_key", "sk-fake")
    monkeypatch.setattr("config.settings.settings.redact_external_payloads", True)
    with patch("openai.OpenAI") as openai_cls:
        openai_cls.return_value = MagicMock()
        provider = get_provider()
    assert isinstance(provider, RedactingProvider)


def test_factory_skips_redaction_when_disabled(monkeypatch):
    monkeypatch.setattr("config.settings.settings.runtime_mode", "demo")
    monkeypatch.setattr("config.settings.settings.llm_provider", "openai")
    monkeypatch.setattr("config.settings.settings.openai_api_key", "sk-fake")
    monkeypatch.setattr("config.settings.settings.redact_external_payloads", False)
    with patch("openai.OpenAI") as openai_cls:
        openai_cls.return_value = MagicMock()
        provider = get_provider()
    assert not isinstance(provider, RedactingProvider)


# ── OpenAIProvider behaviour ────────────────────────────────────────────
def test_openai_provider_requires_key():
    from agent.providers.openai_provider import OpenAIProvider
    with pytest.raises(RuntimeError, match="API key"):
        OpenAIProvider(api_key="")


def test_openai_provider_complete_calls_underlying_client():
    from agent.providers.openai_provider import OpenAIProvider
    with patch("openai.OpenAI") as openai_cls:
        fake = MagicMock()
        fake.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="hello"))]
        )
        openai_cls.return_value = fake
        p = OpenAIProvider(api_key="sk-fake", model="gpt-4o-mini")
        out = p.complete([{"role": "user", "content": "hi"}], max_tokens=10)
    assert out == "hello"
    fake.chat.completions.create.assert_called_once()
    kwargs = fake.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-4o-mini"
    assert kwargs["max_tokens"] == 10


# ── Redaction ───────────────────────────────────────────────────────────
def test_redact_text_masks_ssn_email_phone():
    text = "Contact 555-12-3456 or john@acme.com or 415-555-0100 today."
    out = redact_text(text)
    assert "555-12-3456" not in out
    assert "john@acme.com" not in out
    assert "415-555-0100" not in out
    assert "<REDACTED:SSN>" in out
    assert "<REDACTED:EMAIL>" in out
    assert "<REDACTED:PHONE>" in out


def test_redact_text_leaves_currency_by_default():
    """Masking $ amounts would defeat the financial-confidence threshold."""
    text = "Approve payment of $1,234.56 to vendor."
    assert "$1,234.56" in redact_text(text)


def test_redact_text_can_opt_in_currency():
    out = redact_text("Pay $99.00", enabled_kinds={"currency"})
    assert "$99.00" not in out
    assert "<REDACTED:AMOUNT>" in out


def test_redact_messages_handles_text_blocks():
    msgs = [
        {"role": "user", "content": "Customer SSN 111-22-3333"},
        {"role": "user", "content": [
            {"type": "text", "text": "Email me at a@b.com"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
        ]},
    ]
    out = redact_messages(msgs)
    assert "<REDACTED:SSN>" in out[0]["content"]
    assert "<REDACTED:EMAIL>" in out[1]["content"][0]["text"]
    # Image block untouched.
    assert out[1]["content"][1]["type"] == "image_url"


def test_redacting_provider_passes_clean_payload_to_inner():
    inner = MagicMock()
    inner.complete.return_value = "ok"
    rp = RedactingProvider(inner)
    rp.complete([{"role": "user", "content": "SSN 123-45-6789"}])
    sent = inner.complete.call_args[0][0]
    assert "<REDACTED:SSN>" in sent[0]["content"]
    assert "123-45-6789" not in sent[0]["content"]


def test_redacting_provider_redacts_image_prompt():
    inner = MagicMock()
    inner.complete_with_image.return_value = "ok"
    rp = RedactingProvider(inner)
    rp.complete_with_image(image_b64="…", mime="image/png",
                           prompt="user email a@b.com",
                           max_tokens=10)
    sent_prompt = inner.complete_with_image.call_args.kwargs["prompt"]
    assert "<REDACTED:EMAIL>" in sent_prompt
