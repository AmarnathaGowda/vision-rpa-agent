"""Playwright Page stub — no real browser required."""
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock


def make_mock_element(text: str = "", value: str = "") -> MagicMock:
    el = MagicMock()
    el.text_content = AsyncMock(return_value=text)
    el.input_value = AsyncMock(return_value=value)
    el.is_visible = AsyncMock(return_value=True)
    el.click = AsyncMock()
    el.fill = AsyncMock()
    return el


def make_mock_page(url: str = "http://localhost:8000", title: str = "Test") -> AsyncMock:
    page = AsyncMock()
    page.url = url
    page.title = AsyncMock(return_value=title)
    page.screenshot = AsyncMock(return_value=b"\x89PNG\r\n" + b"\x00" * 100)
    page.query_selector = AsyncMock(return_value=None)
    page.query_selector_all = AsyncMock(return_value=[])
    page.wait_for_selector = AsyncMock(return_value=make_mock_element())
    page.fill = AsyncMock()
    page.click = AsyncMock()
    page.evaluate = AsyncMock(return_value=None)
    page.goto = AsyncMock()
    page.content = AsyncMock(return_value="<html><body></body></html>")
    page.wait_for_load_state = AsyncMock()
    page.is_closed = MagicMock(return_value=False)
    page._make_element = make_mock_element
    return page
