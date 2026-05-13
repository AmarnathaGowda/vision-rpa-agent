"""File system operations — Excel, PDF, network drives, lock pattern."""
from __future__ import annotations
from pathlib import Path


class FileExecutor:
    def read_excel(self, path: Path, sheet: str = "") -> list[dict]:
        raise NotImplementedError

    def read_pdf_bytes(self, path: Path) -> bytes:
        raise NotImplementedError

    def copy_to_local(self, network_path: Path) -> Path:
        raise NotImplementedError

    def find_latest_file(self, directory: Path, pattern: str) -> Path | None:
        raise NotImplementedError

    def acquire_lock(self, path: Path) -> bool:
        raise NotImplementedError

    def release_lock(self, path: Path) -> None:
        raise NotImplementedError
