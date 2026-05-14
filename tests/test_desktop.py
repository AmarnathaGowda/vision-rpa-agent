"""Unit tests for DesktopExecutor — pywinauto entirely mocked.

These tests run cross-platform; the only Windows-specific path
(`_pywinauto_application`) is mocked away. See docs/assumptions.md A-06.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.schemas import ActionPlan
from executors.desktop import DesktopError, DesktopExecutor


def _fake_window():
    """Return a mock that satisfies the chain `window.child_window(...).wrapper_object()`."""
    window = MagicMock()
    window.exists.return_value = True
    return window


def _executor_with_attached_window():
    de = DesktopExecutor()
    fake_app = MagicMock()
    fake_app.top_window.return_value = _fake_window()
    de._apps[12345] = fake_app
    return de, fake_app


def test_split_target_with_and_without_window_prefix():
    assert DesktopExecutor._split_target("Notepad::OK") == ("Notepad", "OK")
    assert DesktopExecutor._split_target("OK") == (None, "OK")
    assert DesktopExecutor._split_target("  A::  B  ") == ("A", "B")


def test_click_uses_invoke_when_available():
    de, app = _executor_with_attached_window()
    ctrl = MagicMock()
    app.top_window.return_value.child_window.return_value.wrapper_object.return_value = ctrl

    de.click(None, "OK")
    ctrl.invoke.assert_called_once()


def test_click_falls_back_to_click_input():
    de, app = _executor_with_attached_window()
    ctrl = MagicMock()
    ctrl.invoke.side_effect = RuntimeError("not invokable")
    app.top_window.return_value.child_window.return_value.wrapper_object.return_value = ctrl
    de.click(None, "OK")
    ctrl.click_input.assert_called_once()


def test_type_text_prefers_set_edit_text():
    de, app = _executor_with_attached_window()
    ctrl = MagicMock()
    app.top_window.return_value.child_window.return_value.wrapper_object.return_value = ctrl
    de.type_text(None, "input", "hello")
    ctrl.set_edit_text.assert_called_once_with("hello")


def test_read_text_returns_window_text():
    de, app = _executor_with_attached_window()
    ctrl = MagicMock()
    ctrl.window_text.return_value = "  Hello World  "
    app.top_window.return_value.child_window.return_value.wrapper_object.return_value = ctrl
    assert de.read_text(None, "label") == "Hello World"


def test_select_option_propagates_failure_as_desktop_error():
    de, app = _executor_with_attached_window()
    ctrl = MagicMock()
    ctrl.select.side_effect = RuntimeError("bad option")
    app.top_window.return_value.child_window.return_value.wrapper_object.return_value = ctrl
    with pytest.raises(DesktopError):
        de.select_option(None, "dropdown", "Closed")


def test_find_element_tries_auto_id_then_title_then_best_match():
    de, app = _executor_with_attached_window()
    win = app.top_window.return_value

    # auto_id and title fail; best_match succeeds.
    auto_branch = MagicMock()
    auto_branch.wrapper_object.side_effect = RuntimeError("no auto_id")
    title_branch = MagicMock()
    title_branch.wrapper_object.side_effect = RuntimeError("no title")
    match_branch = MagicMock()
    success_ctrl = MagicMock()
    match_branch.wrapper_object.return_value = success_ctrl

    win.child_window.side_effect = [auto_branch, title_branch, match_branch]
    assert de._find_element(None, "OK") is success_ctrl


def test_find_element_raises_when_nothing_matches():
    de, app = _executor_with_attached_window()
    win = app.top_window.return_value
    branch = MagicMock()
    branch.wrapper_object.side_effect = RuntimeError("nope")
    win.child_window.return_value = branch
    with pytest.raises(DesktopError):
        de._find_element(None, "ghost")


def test_execute_wraps_failure_into_action_result():
    de, app = _executor_with_attached_window()
    win = app.top_window.return_value
    branch = MagicMock()
    branch.wrapper_object.side_effect = RuntimeError("nope")
    win.child_window.return_value = branch
    plan = ActionPlan(action_type="click", target="ghost", app="desktop")
    result = de.execute(plan)
    assert result.status == "failed"
    assert "desktop_error" in result.error_msg


def test_execute_succeeds_for_noop():
    de = DesktopExecutor()
    plan = ActionPlan(action_type="noop")
    result = de.execute(plan)
    assert result.status == "ok"


def test_pywinauto_unavailable_returns_failed_result_via_attach():
    de = DesktopExecutor()
    # Force the lazy import path to raise even on a Windows runner.
    with patch.object(DesktopExecutor, "_pywinauto_application",
                      staticmethod(lambda: (_ for _ in ()).throw(
                          DesktopError("pywinauto missing")))):
        with pytest.raises(DesktopError):
            de.attach(title_re="Whatever")
