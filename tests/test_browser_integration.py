"""Integration tests — real Chromium + vendored sim HTML pages.

Skipped automatically if Playwright's chromium isn't installed (CI without
`playwright install chromium`). Each test owns its own headless browser; no
shared state between tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.router import ActionRouter
from agent.schemas import ActionPlan
from config.locators import rdweb

try:
    from playwright.sync_api import sync_playwright
    _PW_AVAILABLE = True
except ImportError:
    _PW_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _PW_AVAILABLE, reason="playwright not installed"
)

SIM_DIR = Path(__file__).parent / "sim" / "pages"


def _file_url(name: str) -> str:
    return (SIM_DIR / name).resolve().as_uri()


@pytest.fixture
def browser_page():
    """Provides a fresh headless Chromium page per test."""
    from executors.browser import BrowserSession
    with BrowserSession(headless=True) as session:
        yield session.page


@pytest.fixture
def browser_router(browser_page):
    """ActionRouter wired to a real BrowserExecutor on the fresh page."""
    from executors.browser import BrowserExecutor
    from executors.selectors import SelectorResolver
    executor = BrowserExecutor(browser_page, resolver=SelectorResolver(locator_map=rdweb.ALL))
    return ActionRouter(browser=executor)


def test_login_flow(browser_router, browser_page):
    """LD-shaped login: navigate → fill → fill → click."""
    plans = [
        ActionPlan(action_type="navigate", target=_file_url("login.html")),
        ActionPlan(action_type="type", target="username", value="automation_user"),
        ActionPlan(action_type="type", target="password", value="hunter2"),
        ActionPlan(action_type="click", target="sign_in"),
    ]
    for p in plans:
        result = browser_router.execute(p)
        assert result.status == "ok", f"step failed: {p.action_type}/{p.target} -> {result.error_msg}"

    # The login button redirects to claim_search.html — confirm we landed.
    browser_page.wait_for_url("**/claim_search.html*", timeout=4_000)
    assert "claim_search" in browser_page.url


def test_claim_search_extracts_amount(browser_router):
    """Claim search: search → read status → extract financial amount."""
    seq = [
        ActionPlan(action_type="navigate", target=_file_url("claim_search.html")),
        ActionPlan(action_type="type", target="claim_id", value="CLM-12345"),
        ActionPlan(action_type="click", target="search"),
        ActionPlan(action_type="wait", target="result_row"),
        ActionPlan(action_type="read", target="result_status"),
        ActionPlan(action_type="extract", target="result_amount", is_financial=True),
    ]
    results = [browser_router.execute(p) for p in seq]

    assert all(r.status == "ok" for r in results), [r.error_msg for r in results]
    assert results[4].extracted_value == "In Review"
    assert "$10,640.58" in results[5].extracted_value


def test_form_fill_and_submit(browser_router):
    """IIM-shaped form: fill → submit → verify toast appears."""
    seq = [
        ActionPlan(action_type="navigate", target=_file_url("form.html")),
        ActionPlan(action_type="type", target="loan_number", value="0156312522"),
        ActionPlan(action_type="type", target="amount", value="10640.58", is_financial=True),
        ActionPlan(action_type="click", target="submit"),
        ActionPlan(action_type="wait", target="success_toast"),
        ActionPlan(action_type="read", target="success_toast"),
    ]
    results = [browser_router.execute(p) for p in seq]
    assert all(r.status == "ok" for r in results), [r.error_msg for r in results]
    assert "Saved successfully" in results[5].extracted_value


def test_missing_selector_fails_gracefully(browser_router):
    """Resolver miss must return failed (not crash) so HITL can engage."""
    seq = [
        ActionPlan(action_type="navigate", target=_file_url("login.html")),
        ActionPlan(action_type="click", target="totally_made_up_target"),
    ]
    r1 = browser_router.execute(seq[0])
    r2 = browser_router.execute(seq[1])
    assert r1.status == "ok"
    assert r2.status == "failed"
    assert "selector_unresolved" in r2.error_msg
