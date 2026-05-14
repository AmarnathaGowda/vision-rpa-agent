"""pywinauto UIA desktop executor.

Windows-only at runtime; module imports cleanly on any OS because pywinauto
is imported lazily inside method bodies. This lets us unit-test on macOS
without raising at import time.

Boundary: no LLM calls, no perception, no business logic (CLAUDE.md).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from agent.schemas import ActionPlan, ActionResult
from config.logging_config import get_logger

if TYPE_CHECKING:
    # Hints only — never imported at runtime on non-Windows.
    from pywinauto.application import Application
    from pywinauto.controls.uiawrapper import UIAWrapper

log = get_logger(__name__)


class DesktopError(RuntimeError):
    """Raised on UIA failures the router can route to HITL."""


@dataclass
class WindowRef:
    """Lightweight handle to a top-level window — pywinauto specifics hidden."""
    title: str
    handle: Any   # actually a pywinauto WindowSpecification at runtime
    process_id: int | None = None


class DesktopExecutor:
    DEFAULT_TIMEOUT_S = 10.0
    POLL_S = 0.5

    def __init__(self, backend: str = "uia") -> None:
        self.backend = backend
        self._apps: dict[int, "Application"] = {}

    # ── public lifecycle ────────────────────────────────────────────────────
    def attach(self, title_re: str | None = None,
               process_id: int | None = None,
               timeout_s: float | None = None) -> WindowRef:
        """Attach to an existing top-level window by title regex or PID.

        Short-circuits immediately on ``DesktopError`` from the pywinauto
        import — there's no point retrying that for ``timeout_s`` seconds
        when the failure is deterministic. Other exceptions (window not
        found yet, transient OS errors) still get the retry loop.
        """
        Application = self._pywinauto_application()
        timeout_s = timeout_s or self.DEFAULT_TIMEOUT_S
        deadline = time.monotonic() + timeout_s

        last_err: Exception | None = None
        while time.monotonic() < deadline:
            try:
                if process_id is not None:
                    app = Application(backend=self.backend).connect(process=process_id)
                else:
                    app = Application(backend=self.backend).connect(title_re=title_re)
                window = app.top_window()
                window.wait("visible", timeout=2)
                pid = process_id or window.process_id()
                self._apps[pid] = app
                log.info("desktop_attach", title_re=title_re, process_id=pid)
                return WindowRef(title=window.window_text(), handle=window, process_id=pid)
            except DesktopError:
                # pywinauto unavailable (non-Windows) — deterministic, do not retry.
                raise
            except Exception as e:  # noqa: BLE001 — pywinauto raises a zoo of types
                last_err = e
                time.sleep(self.POLL_S)
        raise DesktopError(
            f"could not attach to window title_re={title_re!r} pid={process_id} "
            f"within {timeout_s}s: {last_err}"
        )

    # ── ActionRouter contract ───────────────────────────────────────────────
    def execute(self, plan: ActionPlan) -> ActionResult:
        start = time.monotonic()
        try:
            extracted = self._dispatch(plan)
            return ActionResult(
                status="ok",
                extracted_value=str(extracted) if extracted is not None else "",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except DesktopError as e:
            log.warning("desktop_action_failed", action=plan.action_type,
                        target=plan.target, error=str(e))
            return ActionResult(
                status="failed",
                error_msg=f"desktop_error: {e}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as e:  # noqa: BLE001 — last-line safety net
            log.exception("desktop_action_crashed", action=plan.action_type)
            return ActionResult(
                status="failed",
                error_msg=f"{type(e).__name__}: {e}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

    # ── primitives ──────────────────────────────────────────────────────────
    def _dispatch(self, plan: ActionPlan):
        # plan.target convention for desktop actions:
        #   "<window_title_re>::<element_title_or_auto_id>"
        # If no "::" present, the most recently attached app is reused.
        window_re, element_target = self._split_target(plan.target)

        if plan.action_type == "click":
            self.click(window_re, element_target)
            return ""
        if plan.action_type == "type":
            self.type_text(window_re, element_target, plan.value)
            return ""
        if plan.action_type == "select_option":
            self.select_option(window_re, element_target, plan.value)
            return ""
        if plan.action_type in ("read", "extract"):
            return self.read_text(window_re, element_target)
        if plan.action_type == "wait":
            self.wait_for(window_re, element_target)
            return ""
        if plan.action_type in ("flag_human", "noop"):
            return ""
        raise DesktopError(f"unsupported desktop action_type={plan.action_type!r}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception_type(DesktopError),
        reraise=True,
    )
    def click(self, window_re: str | None, element_target: str) -> None:
        ctrl = self._find_element(window_re, element_target)
        try:
            ctrl.invoke()  # invoke pattern preferred — accessibility-aware
        except Exception:  # noqa: BLE001 — fall back to click on non-invokable controls
            ctrl.click_input()
        log.info("desktop_click", window=window_re, target=element_target)

    def type_text(self, window_re: str | None, element_target: str, value: str) -> None:
        ctrl = self._find_element(window_re, element_target)
        try:
            ctrl.set_edit_text(value)
        except Exception:  # noqa: BLE001
            ctrl.set_focus()
            ctrl.type_keys(value, with_spaces=True)
        log.info("desktop_type", window=window_re, target=element_target, length=len(value))

    def select_option(self, window_re: str | None, element_target: str, label: str) -> None:
        ctrl = self._find_element(window_re, element_target)
        try:
            ctrl.select(label)
        except Exception as e:  # noqa: BLE001
            raise DesktopError(f"could not select {label!r}: {e}")
        log.info("desktop_select", window=window_re, target=element_target, label=label)

    def read_text(self, window_re: str | None, element_target: str) -> str:
        ctrl = self._find_element(window_re, element_target)
        try:
            text = ctrl.window_text() or ""
        except Exception:  # noqa: BLE001
            text = ""
        text = text.strip()
        log.info("desktop_read", window=window_re, target=element_target, length=len(text))
        return text

    def wait_for(self, window_re: str | None, element_target: str,
                 timeout_s: float | None = None) -> None:
        timeout_s = timeout_s or self.DEFAULT_TIMEOUT_S
        deadline = time.monotonic() + timeout_s
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self._find_element(window_re, element_target)
                log.info("desktop_wait_ok", window=window_re, target=element_target)
                return
            except DesktopError as e:
                last_err = e
                time.sleep(self.POLL_S)
        raise DesktopError(
            f"element {element_target!r} not present in window {window_re!r} "
            f"within {timeout_s}s: {last_err}"
        )

    # ── helpers ─────────────────────────────────────────────────────────────
    @staticmethod
    def _split_target(target: str) -> tuple[str | None, str]:
        if "::" in target:
            window_re, _, element = target.partition("::")
            return window_re.strip() or None, element.strip()
        return None, target.strip()

    def _find_element(self, window_re: str | None, element_target: str) -> "UIAWrapper":
        """Locate a child control by auto_id, title, or best_match."""
        window = self._window(window_re)
        for kw in ({"auto_id": element_target},
                   {"title": element_target},
                   {"best_match": element_target}):
            try:
                return window.child_window(**kw).wrapper_object()
            except Exception:  # noqa: BLE001 — pywinauto raises a zoo of errors
                continue
        raise DesktopError(
            f"no child matched (auto_id/title/best_match) for {element_target!r} "
            f"in window {window_re!r}"
        )

    def _window(self, window_re: str | None):
        if window_re is None:
            if not self._apps:
                raise DesktopError("no window attached — call attach() first or include "
                                   "'<window_re>::' prefix in plan.target")
            app = next(iter(self._apps.values()))
            return app.top_window()
        for app in self._apps.values():
            try:
                w = app.window(title_re=window_re)
                if w.exists():
                    return w
            except Exception:  # noqa: BLE001
                continue
        ref = self.attach(title_re=window_re)
        return ref.handle

    @staticmethod
    def _pywinauto_application():
        """Lazy import — keeps this module loadable on non-Windows."""
        try:
            from pywinauto.application import Application
        except ImportError as e:
            raise DesktopError(
                "pywinauto is not available (Windows-only). "
                "Install on the Windows agent host: poetry install"
            ) from e
        return Application
