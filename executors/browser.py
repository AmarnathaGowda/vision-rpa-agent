"""Playwright executor — primary executor for LD and IIM (browser-based apps).

Stateless wrt the agent loop: each call accepts (page, plan-like args) and
returns an ActionResult. Page/context lifecycle is managed by BrowserSession.
"""
from __future__ import annotations

import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from agent.schemas import ActionPlan, ActionResult
from config.logging_config import get_logger
from config.settings import settings
from executors.selectors import SelectorResolutionError, SelectorResolver


# Recognises both {{KEY}} and {KEY} forms (the LLM frequently picks one
# brace despite the prompt). Both `{{settings.KEY}}` / `{{working.KEY}}`
# variants are also accepted. The single-brace variant requires
# all-uppercase keys to reduce the false-positive rate (we don't want to
# accidentally match a literal `{path}` template in a CSS selector).
_TEMPLATE_RE = re.compile(
    r"""
    \{\{\s*(?:settings\.|working\.)?([A-Za-z_][A-Za-z0-9_]*)\s*\}\}  # {{KEY}}
    |
    \{\s*([A-Z][A-Z0-9_]+)\s*\}                                       # {KEY}
    """,
    re.VERBOSE,
)


def _resolve_templates(value: str, *, working: dict | None = None) -> tuple[str, list[str]]:
    """Substitute ``{{KEY}}`` placeholders from settings / working memory / env.

    Lookup order for each key (case-insensitive in settings, exact in working):
      1. ``working[key]`` if a working-memory dict was provided
      2. ``settings.<key.lower()>``
      3. ``os.environ[key.upper()]``
    Returns (resolved_string, list_of_unresolved_keys).
    """
    if not value or "{" not in value:
        return value, []

    unresolved: list[str] = []

    def _sub(m: re.Match) -> str:
        # Either group 1 ({{KEY}}) or group 2 ({KEY}) will be set.
        key = m.group(1) or m.group(2)
        # working memory first
        if working and key in working and working[key] not in (None, ""):
            return str(working[key])
        # settings
        v = getattr(settings, key.lower(), None)
        if v not in (None, ""):
            return str(v)
        # env vars (uppercase)
        env_v = os.environ.get(key.upper())
        if env_v:
            return env_v
        unresolved.append(key)
        return m.group(0)  # leave the placeholder so HITL can flag it

    resolved = _TEMPLATE_RE.sub(_sub, value)
    return resolved, unresolved


class UnresolvedCredentialError(RuntimeError):
    """Raised when a {{KEY}} placeholder can't be resolved — the loop catches
    this and routes to HITL with a credential-input prompt."""
    def __init__(self, keys: list[str]):
        super().__init__(f"unresolved credentials: {keys}")
        self.keys = keys


class WrongElementForTypeError(RuntimeError):
    """Raised when an LLM-emitted `type` action targets a non-input element
    (button, link, label, etc.). Surfaces a clearer error than Playwright's
    raw 'Element is not an <input>' so the recovery handler can classify
    it as a planning bug (operator should pick a different target)."""

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
        """Guaranteed cleanup — every step runs even if an earlier one raises.

        Nested try/finally so a failure in ``context.close()`` does not
        prevent ``browser.close()`` from running, and a failure in either
        does not prevent ``_pw.stop()``.
        """
        try:
            try:
                if self.context:
                    self.context.close()
            except Exception as e:  # noqa: BLE001 — log and continue cleanup
                log.warning("browser_context_close_failed", error=str(e))
            try:
                if self.browser:
                    self.browser.close()
            except Exception as e:  # noqa: BLE001
                log.warning("browser_close_failed", error=str(e))
        finally:
            try:
                if self._pw:
                    self._pw.stop()
            except Exception as e:  # noqa: BLE001
                log.warning("browser_playwright_stop_failed", error=str(e))
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
        # AgentLoop assigns a working-memory view here before each act so
        # _resolve_templates can pick up operator-provided values (e.g. a
        # password just entered via HITL credential prompt).
        self._working_view: dict | None = None

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
        except UnresolvedCredentialError as e:
            log.warning("credential_unresolved", target=plan.target, keys=e.keys)
            return ActionResult(
                status="failed",
                error_msg=f"unresolved_credentials: {', '.join(e.keys)}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except WrongElementForTypeError as e:
            log.warning("type_on_non_input", target=plan.target, error=str(e))
            return ActionResult(
                status="failed",
                error_msg=f"wrong_element_for_type: {e}",
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
        # Resolve {{KEY}} placeholders in `value` (credentials, URLs, etc.)
        # before any actual action. Unresolved keys raise — the loop catches
        # this and routes to HITL with a credential-input panel.
        raw_value = plan.value or ""
        resolved_value, unresolved = _resolve_templates(
            raw_value, working=getattr(self, "_working_view", None),
        )
        if unresolved:
            raise UnresolvedCredentialError(unresolved)

        if plan.action_type == "navigate":
            self.navigate(resolved_value or plan.target)
            return "", self._snap(plan)
        if plan.action_type == "click":
            self.click(plan.target, fallback=plan.fallback)
            return "", self._snap(plan)
        if plan.action_type == "click_download_open":
            launch_url = self.click_download_open(plan.target, fallback=plan.fallback)
            # Return the launched URL so the loop can record it in working
            # memory (e.g. as `pdf_url` or `launcher_url`).
            return launch_url or "", self._snap(plan)
        if plan.action_type == "type":
            self.fill(plan.target, resolved_value, fallback=plan.fallback)
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
        # no_wait_after=True — don't block waiting for navigation that the
        # click might trigger. The next perception iteration will see the
        # new page state. Slow simulators / SSO redirects would otherwise
        # exceed DEFAULT_TIMEOUT_MS even though the click itself worked.
        try:
            self.page.locator(sel.selector).first.click(
                timeout=self.DEFAULT_TIMEOUT_MS,
                no_wait_after=True,
            )
        except TypeError:
            # Older Playwright API may not accept no_wait_after on Locator.click
            # — fall back without the kwarg.
            self.page.locator(sel.selector).first.click(timeout=self.DEFAULT_TIMEOUT_MS)
        log.info("browser_click", target=target, selector=sel.selector, strategy=sel.strategy)

    def click_download_open(self, target: str,
                              fallback: str | None = None) -> str:
        """Click a link that triggers a file download, capture the file,
        and (if it's an HTML launcher with a meta-refresh URL) navigate
        the current tab to that URL.

        Returns the final URL that the tab was navigated to (empty string
        if no meta-refresh was found in the downloaded file).

        Used for the RDWeb "click Loss Drafts → download HTML launcher
        with meta-refresh → land at /lossdrafts/" pattern. The launcher
        file itself is also saved to ``settings.download_dir`` for audit.
        """
        import re as _re
        sel = self.resolver.resolve(self.page, target, fallback=fallback)
        log.info("browser_click_download_open_start",
                 target=target, selector=sel.selector, strategy=sel.strategy)

        # expect_download wraps the click — Playwright records the download
        # event that the click triggers. The click itself can complete
        # immediately because the download is being processed in the BG.
        with self.page.expect_download(timeout=self.DEFAULT_TIMEOUT_MS * 2) as dl_info:
            self.page.locator(sel.selector).first.click(
                timeout=self.DEFAULT_TIMEOUT_MS,
                no_wait_after=True,
            )
        download = dl_info.value
        saved_path = self.screenshot_dir.parent / "downloads" / download.suggested_filename
        saved_path.parent.mkdir(parents=True, exist_ok=True)
        download.save_as(str(saved_path))
        log.info("browser_download_saved",
                 path=str(saved_path),
                 suggested=download.suggested_filename)

        # Try to parse the meta-refresh URL out of the launcher HTML.
        try:
            body = saved_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            log.warning("browser_download_read_failed", error=str(e))
            return ""
        m = _re.search(
            r'<meta\s+http-equiv="refresh"\s+content="\d+;\s*url=([^"]+)"',
            body, _re.IGNORECASE,
        )
        if not m:
            # Not an HTML launcher with a redirect — the caller (LLM /
            # next loop iteration) will need to handle the file directly.
            log.info("browser_download_no_meta_refresh",
                     path=str(saved_path),
                     hint="file saved; no auto-navigation performed")
            return ""

        launch_url = m.group(1).strip()
        if not launch_url.startswith("http"):
            # Relative — resolve against the current page origin.
            from urllib.parse import urljoin
            launch_url = urljoin(self.page.url, launch_url)
        log.info("browser_download_meta_refresh", launch_url=launch_url)

        # Navigate the existing tab to the launcher's target URL.
        self.page.goto(launch_url, wait_until="domcontentloaded",
                        timeout=self.DEFAULT_TIMEOUT_MS * 2)
        return launch_url

    def fill(self, target: str, value: str, fallback: str | None = None) -> None:
        sel = self.resolver.resolve(self.page, target, fallback=fallback)
        locator = self.page.locator(sel.selector).first
        # Reject `type` on non-input elements early. The LLM frequently
        # picks button/link/label text as a target for type — Playwright's
        # own error is recoverable but unclear. Surface a typed,
        # actionable failure so the next plan iteration sees what's wrong.
        try:
            tag = (locator.evaluate("el => el.tagName", timeout=1_000) or "").lower()
        except Exception:  # noqa: BLE001 — locator might have detached; let fill() raise
            tag = ""
        # `<label>` is FINE — Playwright's fill() transparently follows the
        # label's `for`/`htmlFor` attribute to the associated input. The
        # planner often picks the label's text (e.g. "Domain\user name")
        # because it's the most visible anchor on the page. Block only the
        # truly wrong tags (button/a/etc.) where fill() will fail loudly.
        BLOCKED_TAGS = {"button", "a", "div", "span", "p", "h1", "h2", "h3",
                        "h4", "h5", "h6", "img", "svg"}
        if tag in BLOCKED_TAGS:
            # Allow contenteditable elements through (rare but valid).
            try:
                editable = bool(locator.evaluate("el => el.isContentEditable",
                                                  timeout=500))
            except Exception:  # noqa: BLE001
                editable = False
            if not editable:
                raise WrongElementForTypeError(
                    f"target {target!r} resolved to <{tag}> via {sel.strategy} "
                    f"— `type` requires an input/textarea/select (or a label "
                    f"that points to one). The LLM likely picked the submit "
                    f"button. Re-plan with the actual input field's target."
                )
        locator.fill(value, timeout=self.DEFAULT_TIMEOUT_MS)
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
