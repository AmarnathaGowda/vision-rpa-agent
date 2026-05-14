"""Tests for FileExecutor — cross-platform FS ops + mocked file/folder open."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.schemas import ActionPlan
from executors.file_ops import FileError, FileExecutor


def test_copy_to_local_copies_and_returns_path(tmp_path):
    src = tmp_path / "share" / "report.xlsx"
    src.parent.mkdir()
    src.write_bytes(b"\x01\x02\x03")

    fx = FileExecutor(download_dir=str(tmp_path / "local"))
    dst = fx.copy_to_local(str(src))
    assert dst.exists()
    assert dst.read_bytes() == b"\x01\x02\x03"
    assert dst.parent == tmp_path / "local"


def test_copy_to_local_missing_source_raises(tmp_path):
    fx = FileExecutor(download_dir=str(tmp_path / "local"))
    with pytest.raises(FileError):
        fx.copy_to_local(str(tmp_path / "nope.xlsx"))


def test_find_latest_file_picks_most_recent(tmp_path):
    older = tmp_path / "a.pdf"
    older.write_text("a")
    newer = tmp_path / "b.pdf"
    newer.write_text("b")
    # Bump newer's mtime forward.
    os.utime(newer, (older.stat().st_mtime + 100, older.stat().st_mtime + 100))

    fx = FileExecutor(download_dir=str(tmp_path / "_dl"))
    assert fx.find_latest_file(str(tmp_path), "*.pdf") == newer


def test_find_latest_file_returns_none_on_empty(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    fx = FileExecutor(download_dir=str(tmp_path / "_dl"))
    assert fx.find_latest_file(str(empty), "*") is None


def test_lock_acquire_and_release(tmp_path):
    target = tmp_path / "doc.xlsx"
    target.write_text("x")
    fx = FileExecutor(download_dir=str(tmp_path / "_dl"))
    lock = fx.acquire_lock(str(target))
    assert lock.exists()
    with pytest.raises(FileError):
        fx.acquire_lock(str(target))
    fx.release_lock(str(target))
    assert not lock.exists()


def test_with_temp_copy_yields_local_path_and_cleans_up(tmp_path):
    src = tmp_path / "share.xlsx"; src.write_bytes(b"abc")
    fx = FileExecutor(download_dir=str(tmp_path / "_dl"))
    captured = []
    with fx.with_temp_copy(src) as local:
        captured.append(local)
        assert local.exists()
        assert local.read_bytes() == b"abc"
    assert not captured[0].exists()


def test_open_in_explorer_calls_subprocess(tmp_path):
    fx = FileExecutor(download_dir=str(tmp_path / "_dl"),
                      _subprocess_run=MagicMock())
    fx.open_in_explorer(str(tmp_path))
    fx._run.assert_called_once()


def test_execute_routes_file_navigate(tmp_path):
    fx = FileExecutor(download_dir=str(tmp_path / "_dl"),
                      _subprocess_run=MagicMock())
    plan = ActionPlan(action_type="file_navigate", target=str(tmp_path))
    result = fx.execute(plan)
    assert result.status == "ok"


def test_execute_read_returns_file_contents(tmp_path):
    f = tmp_path / "note.txt"; f.write_text("hello")
    fx = FileExecutor(download_dir=str(tmp_path / "_dl"))
    result = fx.execute(ActionPlan(action_type="read", target=str(f), app="file"))
    assert result.status == "ok"
    assert result.extracted_value == "hello"


def test_execute_unsupported_action_returns_failed(tmp_path):
    fx = FileExecutor(download_dir=str(tmp_path / "_dl"))
    plan = ActionPlan(action_type="js_eval", value="1", app="file")
    result = fx.execute(plan)
    assert result.status == "failed"
