"""Playwright executor — primary executor for LD and IIM (browser-based apps)."""
from __future__ import annotations


class BrowserExecutor:
    def click(self, page, target: str, force: bool = False) -> dict:
        raise NotImplementedError

    def fill(self, page, target: str, value: str) -> dict:
        raise NotImplementedError

    def navigate(self, page, url: str) -> dict:
        raise NotImplementedError

    def select_option(self, page, target: str, label: str) -> dict:
        raise NotImplementedError

    def wait_and_read(self, page, selector: str, timeout: int = 8_000) -> str:
        raise NotImplementedError

    def download_file(self, page, trigger_selector: str):
        raise NotImplementedError
