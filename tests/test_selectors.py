"""Unit tests for SelectorResolver — candidate generation + exists() probe."""
from __future__ import annotations

from unittest.mock import MagicMock

from executors.selectors import SelectorResolutionError, SelectorResolver


def _fake_page(visible_selectors: set[str]):
    page = MagicMock()

    def locator(selector):
        loc = MagicMock()
        loc.first.is_visible.return_value = selector in visible_selectors
        return loc

    page.locator.side_effect = locator
    return page


def test_locator_map_wins_first():
    resolver = SelectorResolver(locator_map={"login": "[data-testid='login-btn']"})
    page = _fake_page({"[data-testid='login-btn']"})
    sel = resolver.resolve(page, "login")
    assert sel.selector == "[data-testid='login-btn']"
    assert sel.strategy == "testid"


def test_falls_through_to_text_when_testid_missing():
    resolver = SelectorResolver()
    page = _fake_page({"text=Submit"})
    sel = resolver.resolve(page, "Submit")
    assert sel.selector == "text=Submit"
    assert sel.strategy == "text"


def test_raises_when_nothing_matches():
    resolver = SelectorResolver()
    page = _fake_page(set())
    try:
        resolver.resolve(page, "ghost")
    except SelectorResolutionError as e:
        assert "ghost" in str(e)
    else:
        raise AssertionError("expected SelectorResolutionError")


def test_uses_fallback_when_provided():
    resolver = SelectorResolver()
    page = _fake_page({"#legacy-id"})
    sel = resolver.resolve(page, "no-such-target", fallback="#legacy-id")
    assert sel.strategy == "fallback"


def test_empty_target_raises():
    resolver = SelectorResolver()
    try:
        resolver.resolve(_fake_page(set()), "")
    except SelectorResolutionError:
        pass
    else:
        raise AssertionError("expected SelectorResolutionError")
