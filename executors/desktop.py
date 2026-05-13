"""pywinauto UIA executor — RDP window detection and File Explorer only."""
from __future__ import annotations


class DesktopExecutor:
    def find_window(self, title: str = "", title_contains: str = ""):
        raise NotImplementedError

    def click_element(self, window, auto_id: str = "", name: str = "",
                      control_type: str = "") -> dict:
        raise NotImplementedError

    def type_text(self, window, auto_id: str, value: str) -> dict:
        raise NotImplementedError

    def read_element(self, window, auto_id: str = "", name: str = "") -> str:
        raise NotImplementedError
