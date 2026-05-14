"""Unit tests for RDPHandler — subprocess + pywinauto entirely mocked."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.schemas import ActionPlan
from executors.rdp import RDPError, RDPHandler, RDPSession


def _fake_desktop_with_window():
    desktop = MagicMock()
    window_ref = MagicMock()
    window_ref.title = "App VM - Remote Desktop Connection"
    window_ref.handle.rectangle.return_value = MagicMock(
        left=10, top=20, right=810, bottom=620,
    )
    window_ref.handle.exists.return_value = True
    window_ref.process_id = 4242
    desktop.attach.return_value = window_ref
    return desktop


def _fake_popen_factory():
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 9999
    factory = MagicMock(return_value=proc)
    return factory, proc


def test_launch_requires_existing_rdp_file(tmp_path):
    handler = RDPHandler(desktop=_fake_desktop_with_window(), _subprocess=MagicMock())
    with patch("executors.rdp.sys") as sysmod:
        sysmod.platform = "win32"
        with pytest.raises(RDPError, match="not found"):
            handler.launch(tmp_path / "missing.rdp")


def test_launch_starts_mstsc_and_attaches_window(tmp_path):
    rdp = tmp_path / "session.rdp"
    rdp.write_text("dummy")
    factory, proc = _fake_popen_factory()
    desktop = _fake_desktop_with_window()
    handler = RDPHandler(desktop=desktop, keepalive_seconds=86400,
                         _subprocess=factory)
    with patch("executors.rdp.sys") as sysmod:
        sysmod.platform = "win32"
        session = handler.launch(rdp, connect_timeout_s=2)
    assert isinstance(session, RDPSession)
    assert session.rdp_file == rdp
    assert session.window is not None
    # mstsc invoked with the file path:
    factory.assert_called_once()
    call_args = factory.call_args[0][0]
    assert call_args[0] == "mstsc.exe"
    assert call_args[1] == str(rdp)
    # Keep-alive thread spawned & running:
    assert session.keep_alive is not None
    assert session.keep_alive.is_alive() or session.keep_alive_stop.is_set()


def test_launch_outside_windows_raises():
    handler = RDPHandler(desktop=MagicMock(), _subprocess=MagicMock())
    with patch("executors.rdp.sys") as sysmod:
        sysmod.platform = "darwin"
        with pytest.raises(RDPError, match="Windows-only"):
            handler.launch(Path("/tmp/nope.rdp"))


def test_disconnect_stops_keepalive_and_terminates_process(tmp_path):
    rdp = tmp_path / "x.rdp"; rdp.write_text("x")
    factory, proc = _fake_popen_factory()
    handler = RDPHandler(desktop=_fake_desktop_with_window(),
                         keepalive_seconds=86400, _subprocess=factory)
    with patch("executors.rdp.sys") as sysmod:
        sysmod.platform = "win32"
        handler.launch(rdp, connect_timeout_s=2)
    session = handler.session
    handler.disconnect()
    proc.terminate.assert_called_once()
    assert handler.session is None
    assert session.keep_alive_stop.is_set()


def test_reconnect_increments_count(tmp_path):
    rdp = tmp_path / "x.rdp"; rdp.write_text("x")
    factory, _ = _fake_popen_factory()
    handler = RDPHandler(desktop=_fake_desktop_with_window(),
                         keepalive_seconds=86400, _subprocess=factory)
    with patch("executors.rdp.sys") as sysmod:
        sysmod.platform = "win32"
        handler.launch(rdp, connect_timeout_s=2)
        new = handler.reconnect()
    assert new.reconnect_count == 1


def test_reconnect_limit_raises(tmp_path):
    rdp = tmp_path / "x.rdp"; rdp.write_text("x")
    factory, _ = _fake_popen_factory()
    handler = RDPHandler(desktop=_fake_desktop_with_window(),
                         keepalive_seconds=86400, _subprocess=factory)
    with patch("executors.rdp.sys") as sysmod:
        sysmod.platform = "win32"
        handler.launch(rdp, connect_timeout_s=2)
        handler.session.reconnect_count = handler.MAX_RECONNECTS
        with pytest.raises(RDPError, match="reconnect_limit_exceeded"):
            handler.reconnect()


def test_window_bbox_returns_dict_when_attached(tmp_path):
    rdp = tmp_path / "x.rdp"; rdp.write_text("x")
    factory, _ = _fake_popen_factory()
    handler = RDPHandler(desktop=_fake_desktop_with_window(),
                         keepalive_seconds=86400, _subprocess=factory)
    with patch("executors.rdp.sys") as sysmod:
        sysmod.platform = "win32"
        handler.launch(rdp, connect_timeout_s=2)
    bbox = handler.window_bbox()
    assert bbox == {"left": 10, "top": 20, "width": 800, "height": 600}


def test_window_bbox_none_when_no_session():
    handler = RDPHandler(desktop=MagicMock())
    assert handler.window_bbox() is None


def test_execute_returns_failed_on_unknown_action():
    handler = RDPHandler(desktop=MagicMock())
    result = handler.execute(ActionPlan(action_type="click", target="x"))
    assert result.status == "failed"


def test_execute_routes_rdp_disconnect_when_no_session():
    handler = RDPHandler(desktop=MagicMock())
    # No session is fine — disconnect should no-op cleanly.
    result = handler.execute(ActionPlan(action_type="rdp_disconnect"))
    assert result.status == "ok"


def test_detect_disconnect_true_when_no_session():
    handler = RDPHandler(desktop=MagicMock())
    assert handler.detect_disconnect() is True


def test_detect_disconnect_false_when_window_exists(tmp_path):
    rdp = tmp_path / "x.rdp"; rdp.write_text("x")
    factory, _ = _fake_popen_factory()
    handler = RDPHandler(desktop=_fake_desktop_with_window(),
                         keepalive_seconds=86400, _subprocess=factory)
    with patch("executors.rdp.sys") as sysmod:
        sysmod.platform = "win32"
        handler.launch(rdp, connect_timeout_s=2)
    # window.handle.exists() is True in the fake — so detect_disconnect returns False.
    assert handler.detect_disconnect() is False


def test_detect_disconnect_true_when_window_vanishes(tmp_path):
    rdp = tmp_path / "x.rdp"; rdp.write_text("x")
    factory, _ = _fake_popen_factory()
    handler = RDPHandler(desktop=_fake_desktop_with_window(),
                         keepalive_seconds=86400, _subprocess=factory)
    with patch("executors.rdp.sys") as sysmod:
        sysmod.platform = "win32"
        handler.launch(rdp, connect_timeout_s=2)
    # Simulate the window disappearing after connection.
    handler.session.window.handle.exists.return_value = False
    assert handler.detect_disconnect() is True
