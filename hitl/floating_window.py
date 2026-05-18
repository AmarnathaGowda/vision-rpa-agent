"""Floating runtime UI launcher.

Pops a small native window pointing at the FastAPI runtime view
(`/runtime`). The window is decoupled from the agent — it talks to the
FastAPI server via HTTP/SSE only, so the agent loop is never blocked.

Strategy:
  1. Try pywebview — gives a real native always-on-top window.
  2. Fall back to opening the URL in the default browser.

The launcher runs in a child process so it survives the agent exiting
gracefully (the operator can keep watching logs after the run ends).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.request
import webbrowser

from config.logging_config import configure_logging, get_logger


DEFAULT_PORT = 8080
DEFAULT_HOST = "127.0.0.1"
WAIT_FOR_SERVER_S = 10.0
RUNTIME_PATH = "/runtime"


def _wait_for_server(url: str, timeout_s: float) -> bool:
    """Poll the runtime URL until it returns 200 or the timeout elapses."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001 — server not up yet
            pass
        time.sleep(0.25)
    return False


def _try_pywebview(url: str, *, always_on_top: bool) -> bool:
    """Open a pywebview native window. Returns True if shown."""
    try:
        import webview  # type: ignore
    except ImportError:
        return False
    try:
        webview.create_window(
            "Agent Runtime",
            url=url,
            width=520,
            height=720,
            on_top=always_on_top,
            resizable=True,
        )
        webview.start()  # blocks until the window is closed
        return True
    except Exception as e:  # noqa: BLE001 — pywebview can fail on missing native libs
        sys.stderr.write(f"[floating-ui] pywebview failed: {e}\n")
        return False


def _fallback_browser(url: str) -> bool:
    """Open the URL in the default browser. Returns True if opened."""
    try:
        return webbrowser.open(url, new=2)
    except Exception:  # noqa: BLE001
        return False


def launch(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
            always_on_top: bool = True) -> int:
    configure_logging("floating_ui")
    log = get_logger(__name__)

    url = f"http://{host}:{port}{RUNTIME_PATH}"
    log.info("floating_ui_starting", url=url)

    if not _wait_for_server(url, WAIT_FOR_SERVER_S):
        log.warning("floating_ui_server_not_ready",
                    url=url, after_s=WAIT_FOR_SERVER_S)
        # Continue anyway — pywebview / browser will show its own error.

    if _try_pywebview(url, always_on_top=always_on_top):
        return 0
    log.info("floating_ui_falling_back_to_browser", url=url)
    if _fallback_browser(url):
        return 0
    log.error("floating_ui_no_ui_method_worked", url=url)
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent Runtime floating window")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-on-top", action="store_true",
                        help="Don't pin the window always-on-top")
    args = parser.parse_args()
    return launch(host=args.host, port=args.port,
                   always_on_top=not args.no_on_top)


if __name__ == "__main__":
    sys.exit(main())
