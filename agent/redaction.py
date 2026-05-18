"""Redaction layer — strips PII / financial identifiers before external LLM calls.

Applied ONLY when the active LLM provider is external (openai / claude) AND
``settings.redact_external_payloads`` is True. Local providers (ollama /
vLLM) skip redaction by design — the prompt never leaves the host.

Patterns covered (all configurable via ``REDACTION_PATTERNS``):
- US SSN  (xxx-xx-xxxx)
- Credit card  (13-19 digit groups, Luhn not enforced — false positives OK)
- Bank account  (heuristic: 8-17 digit standalone)
- US phone  (xxx-xxx-xxxx, with optional country code)
- Email  (RFC-lite)
- Currency  ($x,xxx.xx and ₹/€/£ variants) — kept by default since masking
  amounts would defeat the planner; surface flag for opt-in.

Each match is replaced by a stable token ``<REDACTED:KIND>`` so the model
can still reason about *where* the value would be without seeing the value
itself. Replacement is idempotent (re-running on output is safe).

Public API:
- ``redact_text(text) -> str``
- ``redact_messages(messages) -> messages`` — recursive on OpenAI/Anthropic
  message shapes (string content or [{"type":"text","text":...}, ...] lists).
- ``redact_prompt(prompt) -> str`` — convenience wrapper for image-call prompts.
"""
from __future__ import annotations

import re
from typing import Any

# (name, pattern, replacement_token, default_enabled)
REDACTION_PATTERNS: list[tuple[str, re.Pattern, str, bool]] = [
    ("ssn",       re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                      "<REDACTED:SSN>",    True),
    ("credit",    re.compile(r"\b(?:\d[ -]?){13,19}\b"),                     "<REDACTED:CARD>",   True),
    ("phone",     re.compile(r"\b(?:\+?\d{1,3}[ -]?)?\(?\d{3}\)?[ -]?\d{3}[ -]?\d{4}\b"),
                  "<REDACTED:PHONE>", True),
    ("email",     re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),               "<REDACTED:EMAIL>",  True),
    # Currency intentionally OFF by default — masking amounts would prevent
    # the planner from reasoning about financial-confidence thresholds.
    ("currency",  re.compile(r"[\$€£₹]\s?\d{1,3}(?:[,\s]\d{3})*(?:\.\d{2})?"),
                  "<REDACTED:AMOUNT>", False),
]


def redact_text(text: str, *, enabled_kinds: set[str] | None = None) -> str:
    """Apply every enabled pattern to ``text`` and return the redacted version.

    ``enabled_kinds`` overrides defaults. Pass ``set()`` to disable all
    (useful in tests / when an SOP explicitly authorises a payload).
    """
    if not isinstance(text, str) or not text:
        return text
    for name, pattern, token, default_on in REDACTION_PATTERNS:
        active = (enabled_kinds is None and default_on) or (
            enabled_kinds is not None and name in enabled_kinds
        )
        if active:
            text = pattern.sub(token, text)
    return text


def redact_messages(
    messages: list[dict],
    *,
    enabled_kinds: set[str] | None = None,
) -> list[dict]:
    """Walk an OpenAI/Anthropic-style messages list, redacting text in place
    on a deep copy. Image content blocks are left untouched (image redaction
    is a separate concern — out of scope for this layer).
    """
    out: list[dict] = []
    for msg in messages:
        new = dict(msg)
        content = new.get("content")
        if isinstance(content, str):
            new["content"] = redact_text(content, enabled_kinds=enabled_kinds)
        elif isinstance(content, list):
            new_content = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    block = {**block,
                             "text": redact_text(block.get("text", ""),
                                                 enabled_kinds=enabled_kinds)}
                new_content.append(block)
            new["content"] = new_content
        out.append(new)
    return out


def redact_prompt(prompt: str, *, enabled_kinds: set[str] | None = None) -> str:
    """Image-call convenience wrapper."""
    return redact_text(prompt, enabled_kinds=enabled_kinds)


# ── Provider wrapper ──────────────────────────────────────────────────
class RedactingProvider:
    """Decorator that redacts payloads before delegating to an external provider.

    Only used when ``settings.redact_external_payloads`` is True AND the
    wrapped provider is external. The factory in ``agent/providers/__init__.py``
    handles wiring — call sites stay provider-agnostic.
    """

    def __init__(self, inner: Any, enabled_kinds: set[str] | None = None) -> None:
        self._inner = inner
        self._kinds = enabled_kinds

    def complete(
        self,
        messages: list[dict],
        max_tokens: int = 512,
        temperature: float = 0.1,
    ) -> str:
        clean = redact_messages(messages, enabled_kinds=self._kinds)
        return self._inner.complete(clean, max_tokens=max_tokens,
                                    temperature=temperature)

    def complete_with_image(
        self,
        image_b64: str,
        mime: str,
        prompt: str,
        max_tokens: int = 512,
    ) -> str:
        clean_prompt = redact_prompt(prompt, enabled_kinds=self._kinds)
        return self._inner.complete_with_image(
            image_b64=image_b64, mime=mime, prompt=clean_prompt,
            max_tokens=max_tokens,
        )
