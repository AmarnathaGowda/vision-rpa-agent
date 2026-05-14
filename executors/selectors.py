"""Selector resolution strategy.

Priority (enforced by CLAUDE.md):
    1. data-testid       ← always try first
    2. aria-label
    3. name attribute
    4. text content (button/link text)
    5. raw CSS / XPath   ← when the planner produces one explicitly
    6. flag_for_human    ← never guess

The resolver accepts a free-form ``target`` from an ActionPlan and returns the
*first* Playwright-compatible selector whose locator exists on the page (or
raises ``SelectorResolutionError`` if none do).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from config.logging_config import get_logger

if TYPE_CHECKING:
    from playwright.sync_api import Page

log = get_logger(__name__)


class SelectorResolutionError(RuntimeError):
    """Raised when no candidate selector matches an element on the page."""


@dataclass
class ResolvedSelector:
    selector: str
    strategy: str          # "testid" | "aria" | "name" | "text" | "raw" | "fallback"
    target: str            # original target string for audit


class SelectorResolver:
    # Per-candidate probe must be SHORT — we may try 8+ candidates per action.
    DEFAULT_TIMEOUT_MS = 300

    def __init__(self, locator_map: dict[str, str] | None = None,
                 timeout_ms: int = DEFAULT_TIMEOUT_MS) -> None:
        # Friendly-name → canonical selector lookup (e.g. from config/locators/rdweb.py).
        self.locator_map = locator_map or {}
        self.timeout_ms = timeout_ms

    def resolve(self, page: "Page", target: str,
                fallback: str | None = None) -> ResolvedSelector:
        if not target:
            raise SelectorResolutionError("empty target")

        for selector, strategy in self._candidates(target):
            if self._exists(page, selector):
                log.debug("selector_resolved", target=target,
                          strategy=strategy, selector=selector)
                return ResolvedSelector(selector=selector, strategy=strategy, target=target)

        if fallback:
            if self._exists(page, fallback):
                log.debug("selector_fallback_used", target=target, fallback=fallback)
                return ResolvedSelector(selector=fallback, strategy="fallback", target=target)

        raise SelectorResolutionError(
            f"no candidate matched on page for target={target!r}"
        )

    # ── candidate generation ────────────────────────────────────────────────
    def _candidates(self, target: str):
        # 1. Locator map override — the surest selector we have.
        if target in self.locator_map:
            yield self.locator_map[target], "testid"   # map entries are pre-vetted

        # 2. If the target *is* a raw selector (css / xpath / data-testid attr),
        #    yield it directly first.
        if self._looks_like_raw_selector(target):
            yield target, "raw"

        # 3. Heuristic guesses driven by the human-readable target.
        yield f"[data-testid='{target}']", "testid"
        yield f"[data-testid*='{target}']", "testid"
        yield f"[aria-label='{target}']", "aria"
        yield f"[aria-label*='{target}' i]", "aria"
        yield f"[name='{target}']", "name"
        yield f"#{target}", "name"           # id selector
        yield f"text={target}", "text"
        yield f"button:has-text({target!r})", "text"
        yield f"a:has-text({target!r})", "text"

    @staticmethod
    def _looks_like_raw_selector(s: str) -> bool:
        return any(s.startswith(p) for p in ("//", "css=", "xpath=", "text=", "#", ".", "[")) \
            or " >>" in s

    def _exists(self, page: "Page", selector: str) -> bool:
        try:
            return page.locator(selector).first.is_visible(timeout=self.timeout_ms)
        except Exception:
            return False
