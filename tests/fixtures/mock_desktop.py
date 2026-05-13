"""pywinauto window stub — safe to import on macOS/Linux (no Windows required)."""
from __future__ import annotations
from unittest.mock import MagicMock


def make_mock_child(**kwargs) -> MagicMock:
    child = MagicMock()
    child.window_text.return_value = kwargs.get("title", "")
    child.click_input = MagicMock()
    child.set_focus = MagicMock()
    child.type_keys = MagicMock()
    child.exists.return_value = True
    return child


def make_mock_window(title: str = "Test Window") -> MagicMock:
    win = MagicMock()
    win.window_text.return_value = title
    win.exists.return_value = True
    win.rectangle.return_value = MagicMock(
        left=0, top=0,
        width=MagicMock(return_value=1920),
        height=MagicMock(return_value=1080),
    )
    win.child_window.side_effect = make_mock_child
    win.set_focus = MagicMock()
    win.close = MagicMock()
    return win


def make_mock_desktop(windows: list[str] | None = None) -> MagicMock:
    """Stub for pywinauto.Desktop(backend='uia')."""
    desktop = MagicMock()
    win_titles = windows or ["Test Window"]
    mock_windows = [make_mock_window(t) for t in win_titles]
    desktop.windows.return_value = mock_windows
    desktop.window.return_value = mock_windows[0]
    return desktop
