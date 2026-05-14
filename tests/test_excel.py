"""Tests for FileExecutor Excel ops — real openpyxl + temp files."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.schemas import ActionPlan
from executors.file_ops import FileError, FileExecutor

openpyxl = pytest.importorskip("openpyxl")


def _make_xlsx(path: Path, rows: list[list]) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    wb.save(str(path))


def test_read_excel_returns_dicts_keyed_by_header(tmp_path):
    f = tmp_path / "data.xlsx"
    _make_xlsx(f, [
        ["loan_number", "status", "amount"],
        ["0156312522", "Open", 10640.58],
        ["0156312523", "Closed", 4200.00],
    ])
    fx = FileExecutor(download_dir=str(tmp_path / "_dl"))
    rows = fx.read_excel(f)
    assert len(rows) == 2
    assert rows[0]["loan_number"] == "0156312522"
    assert rows[1]["amount"] == 4200.0


def test_read_excel_skips_blank_rows(tmp_path):
    f = tmp_path / "blanks.xlsx"
    _make_xlsx(f, [
        ["a", "b"],
        [1, 2],
        [None, None],   # entirely blank — must be skipped
        [3, 4],
    ])
    fx = FileExecutor(download_dir=str(tmp_path / "_dl"))
    rows = fx.read_excel(f)
    assert len(rows) == 2
    assert rows[1] == {"a": 3, "b": 4}


def test_read_excel_missing_file_raises(tmp_path):
    fx = FileExecutor(download_dir=str(tmp_path / "_dl"))
    with pytest.raises(FileError):
        fx.read_excel(tmp_path / "nope.xlsx")


def test_write_excel_round_trip(tmp_path):
    f = tmp_path / "out.xlsx"
    fx = FileExecutor(download_dir=str(tmp_path / "_dl"))
    fx.write_excel(f, [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    assert f.exists()
    rows = fx.read_excel(f)
    assert rows == [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]


def test_write_excel_refuses_overwrite_when_disabled(tmp_path):
    f = tmp_path / "out.xlsx"
    fx = FileExecutor(download_dir=str(tmp_path / "_dl"))
    fx.write_excel(f, [{"a": 1}])
    with pytest.raises(FileError):
        fx.write_excel(f, [{"a": 2}], overwrite=False)


def test_update_excel_cell_by_letter_and_index(tmp_path):
    f = tmp_path / "update.xlsx"
    _make_xlsx(f, [
        ["name", "amount"],
        ["A", 1],
        ["B", 2],
    ])
    fx = FileExecutor(download_dir=str(tmp_path / "_dl"))
    fx.update_excel_cell(f, sheet="Sheet", row=2, column="B", value=99)
    fx.update_excel_cell(f, sheet="Sheet", row=3, column=2, value=88)
    rows = fx.read_excel(f)
    assert rows[0]["amount"] == 99
    assert rows[1]["amount"] == 88


def test_execute_routes_read_excel(tmp_path):
    f = tmp_path / "x.xlsx"
    _make_xlsx(f, [["k"], ["v1"], ["v2"]])
    fx = FileExecutor(download_dir=str(tmp_path / "_dl"))
    result = fx.execute(ActionPlan(action_type="read_excel", target=str(f), app="file"))
    assert result.status == "ok"
    import json
    rows = json.loads(result.extracted_value)
    assert rows == [{"k": "v1"}, {"k": "v2"}]
