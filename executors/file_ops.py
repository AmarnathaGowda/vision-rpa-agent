"""File-system + File Explorer executor.

Two concerns living together because they share the same data — local paths,
network shares, and the explorer.exe window:

1. **Programmatic FS ops** (read_excel, read_pdf, copy_to_local,
   find_latest_file, acquire_lock / release_lock). Cross-platform. Used by
   the extraction pipeline in Phase 4.
2. **File Explorer driving** (file_navigate / file_open via explorer.exe +
   pywinauto address bar). Windows-only at runtime; routed through
   `DesktopExecutor` when one is provided.

Phase 3 scope: implement (1) so the rest of the system has reliable file
plumbing, and (2) so the ActionRouter can route `file_navigate` / `file_open`
to a real explorer window.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from agent.schemas import ActionPlan, ActionResult
from config.logging_config import get_logger
from config.settings import settings

if TYPE_CHECKING:
    from executors.desktop import DesktopExecutor

log = get_logger(__name__)


class FileError(RuntimeError):
    """Raised on file-system or File Explorer failures."""


class FileExecutor:
    EXPLORER_TITLE_RE = r".*"  # explorer.exe titles equal the folder name — match any

    def __init__(self, desktop: "DesktopExecutor | None" = None,
                 download_dir: str | None = None,
                 _subprocess_run=subprocess.run) -> None:
        self.desktop = desktop
        self.download_dir = Path(download_dir or settings.download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._run = _subprocess_run

    # ── ActionRouter contract ───────────────────────────────────────────────
    def execute(self, plan: ActionPlan) -> ActionResult:
        start = time.monotonic()
        try:
            if plan.action_type == "file_navigate":
                self.open_in_explorer(plan.target)
            elif plan.action_type == "file_open":
                self.open_file(plan.target)
            elif plan.action_type in ("read", "extract"):
                value = self.read_text_file(plan.target)
                return ActionResult(
                    status="ok", extracted_value=value,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            elif plan.action_type in ("flag_human", "noop"):
                pass
            else:
                return ActionResult(
                    status="failed",
                    error_msg=f"unsupported file action_type={plan.action_type!r}",
                )
            return ActionResult(
                status="ok",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except FileError as e:
            log.warning("file_action_failed", action=plan.action_type,
                        target=plan.target, error=str(e))
            return ActionResult(status="failed", error_msg=f"file_error: {e}",
                                duration_ms=int((time.monotonic() - start) * 1000))
        except Exception as e:  # noqa: BLE001
            log.exception("file_action_crashed", action=plan.action_type)
            return ActionResult(status="failed", error_msg=f"{type(e).__name__}: {e}",
                                duration_ms=int((time.monotonic() - start) * 1000))

    # ── programmatic FS primitives ──────────────────────────────────────────
    @retry(stop=stop_after_attempt(3), wait=wait_fixed(1.0),
           retry=retry_if_exception_type(OSError), reraise=True)
    def copy_to_local(self, network_path: str) -> Path:
        """Copy a network/share file to download_dir; retries on transient SMB errors."""
        src = Path(network_path)
        if not src.exists():
            raise FileError(f"source does not exist: {network_path}")
        dst = self.download_dir / src.name
        shutil.copy2(src, dst)
        log.info("file_copy", src=str(src), dst=str(dst), size=dst.stat().st_size)
        return dst

    def find_latest_file(self, directory: str, pattern: str = "*") -> Path | None:
        dir_path = Path(directory)
        if not dir_path.exists():
            return None
        matches = sorted(dir_path.glob(pattern), key=lambda p: p.stat().st_mtime,
                         reverse=True)
        return matches[0] if matches else None

    def read_text_file(self, path: str) -> str:
        p = Path(path)
        if not p.exists():
            raise FileError(f"file does not exist: {path}")
        return p.read_text(encoding="utf-8", errors="replace")

    def acquire_lock(self, path: str) -> Path:
        """Co-operative `.lock` sentinel — caller must release_lock() after."""
        lock = Path(f"{path}.lock")
        if lock.exists():
            raise FileError(f"already locked: {lock}")
        lock.write_text(f"{os.getpid()}\n{time.time()}\n", encoding="utf-8")
        log.info("file_lock_acquired", path=str(lock))
        return lock

    def release_lock(self, path: str) -> None:
        lock = Path(f"{path}.lock")
        try:
            lock.unlink()
        except FileNotFoundError:
            return
        log.info("file_lock_released", path=str(lock))

    # ── File Explorer driving (Windows) ─────────────────────────────────────
    def open_in_explorer(self, folder_path: str) -> None:
        """Open `folder_path` in an Explorer window."""
        if sys.platform == "win32":
            self._run(["explorer.exe", folder_path], check=False)
        elif sys.platform == "darwin":
            self._run(["open", folder_path], check=False)
        else:
            self._run(["xdg-open", folder_path], check=False)
        log.info("file_navigate", folder=folder_path)
        # Verify a window exists if a desktop executor was provided.
        if self.desktop and sys.platform == "win32":
            try:
                self.desktop.attach(title_re=Path(folder_path).name)
            except Exception as e:  # noqa: BLE001
                raise FileError(f"explorer did not present the folder window: {e}")

    def open_file(self, file_path: str) -> None:
        """Open a file with the default OS handler."""
        if not Path(file_path).exists():
            raise FileError(f"file does not exist: {file_path}")
        if sys.platform == "win32":
            os.startfile(file_path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            self._run(["open", file_path], check=False)
        else:
            self._run(["xdg-open", file_path], check=False)
        log.info("file_open", path=file_path)

    # ── helpers used by extraction pipeline (Phase 4) ──────────────────────
    @staticmethod
    def with_temp_copy(source: str | Path):
        """Context manager: copy a (potentially shared) file to a temp local
        path, yield the path, delete on exit. Used to avoid holding network
        file handles longer than needed.
        """
        from contextlib import contextmanager

        @contextmanager
        def _cm():
            src = Path(source)
            if not src.exists():
                raise FileError(f"source does not exist: {source}")
            tmp_dir = Path(tempfile.mkdtemp(prefix="vra_"))
            local = tmp_dir / src.name
            try:
                shutil.copy2(src, local)
                yield local
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        return _cm()
