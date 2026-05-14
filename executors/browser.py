"""Playwright executor — primary executor for LD and IIM (browser-based apps).

Stateless wrt the agent loop: each call accepts (page, plan-like args) and
returns an ActionResult. Page/context lifecycle is managed by BrowserSession.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from agent.schemas import ActionPlan, ActionResult
from config.logging_config import get_logger
from config.settings import settings
from executors.selectors import SelectorResolutionError, SelectorResolver

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page

log = get_logger(__name__)


class BrowserSession:
    """Owns the Playwright runtime, browser, context, and active page.

    Use as a context manager so the browser is cleaned up on every exit path.

        with BrowserSession() as session:
            executor = BrowserExecutor(session.page)
    """

    def __init__(self, headless: bool | None = None,
                 slow_mo: int | None = None,
                 downloads_path: str | None = None) -> None:
        self.headless = settings.headless if headless is None else headless
        self.slow_mo = settings.demo_slowmo if slow_mo is None else slow_mo
        self.downloads_path = downloads_path or settings.download_dir
        self._pw = None
        self.browser: "Browser | None" = None
        self.context: "BrowserContext | None" = None
        self.page: "Page | None" = None

    def __enter__(self) -> "BrowserSession":
        from playwright.sync_api import sync_playwright

        Path(self.downloads_path).mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=self.headless, slow_mo=self.slow_mo)
        self.context = self.browser.new_context(accept_downloads=True)
        self.page = self.context.new_page()
        log.info("browser_session_open", headless=self.headless, slow_mo=self.slow_mo)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
        finally:
            if self._pw:
                self._pw.stop()
            log.info("browser_session_closed")


class BrowserExecutor:
    """Executes a single ActionPlan against a Playwright Page.

    Boundary: no LLM calls, no business logic, no perception (CLAUDE.md).
    """

    DEFAULT_TIMEOUT_MS = 8_000

    def __init__(self, page: "Page",
                 resolver: SelectorResolver | None = None,
                 screenshot_dir: str | None = None) -> None:
        self.page = page
        self.resolver = resolver or SelectorResolver()
        self.screenshot_dir = Path(screenshot_dir or settings.screenshot_dir)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

    # ── ActionRouter contract ───────────────────────────────────────────────
    def execute(self, plan: ActionPlan) -> ActionResult:
        start = time.monotonic()
        try:
            extracted, screenshot = self._dispatch(plan)
            return ActionResult(
                status="ok",
                extracted_value=extracted,
                screenshot_path=screenshot,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except SelectorResolutionError as e:
            log.warning("selector_unresolved", target=plan.target, error=str(e))
            return ActionResult(
                status="failed",
                error_msg=f"selector_unresolved: {e}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as e:  # noqa: BLE001 — last-line safety net
            log.exception("browser_action_failed", action=plan.action_type, target=plan.target)
            return ActionResult(
                status="failed",
                error_msg=f"{type(e).__name__}: {e}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

    # ── primitives ──────────────────────────────────────────────────────────
    def _dispatch(self, plan: ActionPlan) -> tuple[str, str]:
        """Return (extracted_value, screenshot_path)."""
        if plan.action_type == "navigate":
            self.navigate(plan.value or plan.target)
            return "", self._snap(plan)
        if plan.action_type == "click":
            self.click(plan.target, fallback=plan.fallback)
            return "", self._snap(plan)
        if plan.action_type == "type":
            self.fill(plan.target, plan.value, fallback=plan.fallback)
            return "", self._snap(plan)
        if plan.action_type in ("read", "extract"):
            value = self.read_text(plan.target, fallback=plan.fallback)
            return value, self._snap(plan)
        if plan.action_type == "wait":
            self.wait_for(plan.target, fallback=plan.fallback)
            return "", self._snap(plan)
        if plan.action_type == "js_eval":
            value = self.eval_js(plan.value)
            return str(value), self._snap(plan)
        if plan.action_type in ("flag_human", "noop"):
            return "", ""
        raise ValueError(f"BrowserExecutor cannot handle action_type={plan.action_type!r}")

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def navigate(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded",
                       timeout=self.DEFAULT_TIMEOUT_MS * 2)
        log.info("browser_navigate", url=url)

    def click(self, target: str, fallback: str | None = None) -> None:
        sel = self.resolver.resolve(self.page, target, fallback=fallback)
        self.page.locator(sel.selector).first.click(timeout=self.DEFAULT_TIMEOUT_MS)
        log.info("browser_click", target=target, selector=sel.selector, strategy=sel.strategy)

    def fill(self, target: str, value: str, fallback: str | None = None) -> None:
        sel = self.resolver.resolve(self.page, target, fallback=fallback)
        self.page.locator(sel.selector).first.fill(value, timeout=self.DEFAULT_TIMEOUT_MS)
        log.info("browser_fill", target=target, selector=sel.selector,
                 strategy=sel.strategy, length=len(value))

    def read_text(self, target: str, fallback: str | None = None) -> str:
        sel = self.resolver.resolve(self.page, target, fallback=fallback)
        text = self.page.locator(sel.selector).first.inner_text(
            timeout=self.DEFAULT_TIMEOUT_MS) or ""
        log.info("browser_read", target=target, selector=sel.selector, length=len(text))
        return text.strip()

    def wait_for(self, target: str, fallback: str | None = None,
                 timeout_ms: int | None = None) -> None:
        sel = self.resolver.resolve(self.page, target, fallback=fallback)
        self.page.locator(sel.selector).first.wait_for(
            timeout=timeout_ms or self.DEFAULT_TIMEOUT_MS)
        log.info("browser_wait", target=target, selector=sel.selector)

    def eval_js(self, expression: str):
        return self.page.evaluate(expression)

    @contextmanager
    def expect_download(self):
        """Use with `with executor.expect_download() as dl: executor.click(...)`."""
        with self.page.expect_download() as dl_info:
            yield dl_info
        download = dl_info.value
        path = Path(settings.download_dir) / download.suggested_filename
        download.save_as(str(path))
        log.info("browser_download", path=str(path))

    # ── helpers ────────────────────────────────────────────────────────────
    def _snap(self, plan: ActionPlan) -> str:
        path = self.screenshot_dir / f"{int(time.time() * 1000)}_{plan.action_type}.png"
        try:
            self.page.screenshot(path=str(path), full_page=False)
            return str(path)
        except Exception as e:  # noqa: BLE001 — screenshots are diagnostic, not load-bearing
            log.warning("screenshot_failed", error=str(e))
            return ""
