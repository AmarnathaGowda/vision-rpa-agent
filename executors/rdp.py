"""RDP session management — launch mstsc, detect connection, keep alive, reconnect.

Windows-only at runtime. Cross-platform import is preserved by deferring all
``subprocess`` calls and ``DesktopExecutor`` window probes to method bodies.

Architectural notes (see docs/assumptions.md, A-08 / A-09 / A-10):
- Keep-alive runs in a daemon thread that periodically nudges the mouse via
  pywinauto on the RDP window. If the host's idle policy is shorter than
  RDP_KEEPALIVE_SECONDS, sessions can still expire.
- Reconnect re-launches mstsc with the same .rdp file. It does NOT rotate
  credentials or warn on session lock; reconnect-loop protection is left to
  the recovery layer.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from agent.schemas import ActionPlan, ActionResult
from config.logging_config import get_logger
from config.settings import settings

if TYPE_CHECKING:
    from executors.desktop import DesktopExecutor, WindowRef

log = get_logger(__name__)


class RDPError(RuntimeError):
    """Raised when the RDP lifecycle hits an unrecoverable state."""


@dataclass
class RDPSession:
    rdp_file: Path
    host: str
    process: subprocess.Popen | None = None
    window: "WindowRef | None" = None
    keep_alive: "threading.Thread | None" = None
    keep_alive_stop: threading.Event = field(default_factory=threading.Event)
    reconnect_count: int = 0


class RDPHandler:
    """Wraps mstsc.exe, the connection window, and the keep-alive loop."""

    DEFAULT_CONNECT_TIMEOUT_S = 60.0
    DEFAULT_KEEPALIVE_S = 240
    MAX_RECONNECTS = 3

    def __init__(self,
                 desktop: "DesktopExecutor",
                 keepalive_seconds: int | None = None,
                 _subprocess: type[subprocess.Popen] | None = None) -> None:
        # `desktop` is reused so we don't double-import pywinauto.
        self.desktop = desktop
        self.keepalive_seconds = keepalive_seconds or self.DEFAULT_KEEPALIVE_S
        self._Popen = _subprocess or subprocess.Popen  # injectable for tests
        self.session: RDPSession | None = None

    # ── ActionRouter contract ───────────────────────────────────────────────
    def execute(self, plan: ActionPlan) -> ActionResult:
        start = time.monotonic()
        try:
            if plan.action_type == "rdp_launch":
                self.launch(Path(plan.target or plan.value))
            elif plan.action_type == "rdp_reconnect":
                self.reconnect()
            elif plan.action_type == "rdp_disconnect":
                self.disconnect()
            elif plan.action_type in ("flag_human", "noop"):
                pass
            else:
                return ActionResult(status="failed",
                                    error_msg=f"unsupported rdp action_type={plan.action_type!r}")
            return ActionResult(status="ok",
                                duration_ms=int((time.monotonic() - start) * 1000))
        except RDPError as e:
            log.warning("rdp_action_failed", action=plan.action_type, error=str(e))
            return ActionResult(status="failed", error_msg=f"rdp_error: {e}",
                                duration_ms=int((time.monotonic() - start) * 1000))
        except Exception as e:  # noqa: BLE001
            log.exception("rdp_action_crashed", action=plan.action_type)
            return ActionResult(status="failed", error_msg=f"{type(e).__name__}: {e}",
                                duration_ms=int((time.monotonic() - start) * 1000))

    # ── lifecycle ───────────────────────────────────────────────────────────
    def launch(self, rdp_file: Path,
               connect_timeout_s: float | None = None) -> RDPSession:
        if sys.platform != "win32":
            raise RDPError("mstsc is Windows-only — see docs/assumptions.md A-06")
        rdp_file = Path(rdp_file)
        if not rdp_file.exists():
            raise RDPError(f".rdp file not found: {rdp_file}")

        log.info("rdp_launch_start", rdp_file=str(rdp_file))
        proc = self._Popen(
            ["mstsc.exe", str(rdp_file)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        session = RDPSession(rdp_file=rdp_file, host=settings.rdp_host, process=proc)
        try:
            session.window = self._await_connection(
                connect_timeout_s or self.DEFAULT_CONNECT_TIMEOUT_S
            )
        except Exception:
            # If connection never came up, kill the half-open mstsc to avoid leaks.
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            raise

        self._start_keep_alive(session)
        self.session = session
        log.info("rdp_launch_ok", host=session.host, pid=proc.pid,
                 window=session.window.title if session.window else None)
        return session

    def disconnect(self) -> None:
        if not self.session:
            return
        self._stop_keep_alive(self.session)
        if self.session.process and self.session.process.poll() is None:
            try:
                self.session.process.terminate()
                self.session.process.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    self.session.process.kill()
                except Exception:  # noqa: BLE001
                    pass
        log.info("rdp_disconnected")
        self.session = None

    def reconnect(self) -> RDPSession:
        if not self.session:
            raise RDPError("no prior session to reconnect")
        if self.session.reconnect_count >= self.MAX_RECONNECTS:
            raise RDPError(f"reconnect_limit_exceeded ({self.MAX_RECONNECTS})")
        prior = self.session
        log.warning("rdp_reconnect", attempt=prior.reconnect_count + 1,
                    rdp_file=str(prior.rdp_file))
        self.disconnect()
        new = self.launch(prior.rdp_file)
        new.reconnect_count = prior.reconnect_count + 1
        return new

    # ── connection probe ────────────────────────────────────────────────────
    def _await_connection(self, timeout_s: float) -> "WindowRef":
        """Wait until the RDP window appears + reports connected."""
        deadline = time.monotonic() + timeout_s
        title_re = self._connection_window_title_re()
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            try:
                ref = self.desktop.attach(title_re=title_re, timeout_s=2.0)
                return ref
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(1.0)
        raise RDPError(
            f"RDP window did not appear within {timeout_s}s "
            f"(title_re={title_re!r}): {last_err}"
        )

    def _connection_window_title_re(self) -> str:
        host = settings.rdp_host or ""
        # mstsc titles look like:  "<host> - Remote Desktop Connection"
        # Also match RemoteApp windows that include the host name.
        return rf".*({host}).*"

    def detect_disconnect(self) -> bool:
        if not self.session or not self.session.window:
            return True
        try:
            return not self.session.window.handle.exists()
        except Exception:  # noqa: BLE001
            return True

    # ── keep-alive thread ───────────────────────────────────────────────────
    def _start_keep_alive(self, session: RDPSession) -> None:
        if session.keep_alive and session.keep_alive.is_alive():
            return
        session.keep_alive_stop.clear()
        thread = threading.Thread(
            target=self._keep_alive_loop,
            args=(session,),
            name="rdp-keepalive",
            daemon=True,
        )
        session.keep_alive = thread
        thread.start()
        log.info("rdp_keepalive_started", every_seconds=self.keepalive_seconds)

    def _stop_keep_alive(self, session: RDPSession) -> None:
        session.keep_alive_stop.set()
        if session.keep_alive:
            session.keep_alive.join(timeout=2)
        session.keep_alive = None
        log.info("rdp_keepalive_stopped")

    def _keep_alive_loop(self, session: RDPSession) -> None:
        """Wake the RDP session periodically by moving the mouse cursor inside it."""
        while not session.keep_alive_stop.wait(self.keepalive_seconds):
            try:
                self._nudge(session)
            except Exception as e:  # noqa: BLE001
                log.warning("rdp_keepalive_nudge_failed", error=str(e))

    def _nudge(self, session: RDPSession) -> None:
        """Move the mouse 1px inside the RDP window — a no-op for the user."""
        if not session.window:
            return
        try:
            rect = session.window.handle.rectangle()
            self.desktop.attach(process_id=session.window.process_id, timeout_s=1.0)
            # Use pywinauto's move_mouse, NOT click — input must not affect the app.
            from pywinauto.mouse import move
            move(coords=(rect.left + 5, rect.top + 5))
            log.debug("rdp_keepalive_nudged")
        except ImportError:
            # pywinauto unavailable (non-Windows test path). Treat as no-op.
            return

    # ── external hooks used by recovery/perception ─────────────────────────
    def window_bbox(self) -> dict | None:
        """Return {left,top,width,height} of the RDP window or None."""
        if not self.session or not self.session.window:
            return None
        try:
            r = self.session.window.handle.rectangle()
            return {"left": r.left, "top": r.top,
                    "width": r.right - r.left, "height": r.bottom - r.top}
        except Exception:  # noqa: BLE001
            return None


def make_rdp_handler_for_runtime(desktop: "DesktopExecutor",
                                 popen_factory: Callable | None = None) -> RDPHandler:
    """Factory used by run_agent.py — keeps construction in one place."""
    return RDPHandler(desktop=desktop,
                      keepalive_seconds=settings.rdp_keepalive_seconds,
                      _subprocess=popen_factory)
