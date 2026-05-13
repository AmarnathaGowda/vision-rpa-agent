"""Shared pytest fixtures — available to all tests without import."""
from __future__ import annotations
import pytest

from tests.fixtures.mock_llm import MockOpenAIClient, make_screen_state
from tests.fixtures.mock_browser import make_mock_page
from tests.fixtures.mock_desktop import make_mock_window, make_mock_desktop


@pytest.fixture
def llm_client():
    return MockOpenAIClient(responses=[make_screen_state()])


@pytest.fixture
def browser_page():
    return make_mock_page()


@pytest.fixture
def desktop_window():
    return make_mock_window()


@pytest.fixture
def mock_desktop():
    return make_mock_desktop()


@pytest.fixture
def session_store():
    from tests.fixtures.mock_session import make_test_session
    store = make_test_session()
    yield store
    store._conn.close()


@pytest.fixture
def working_memory():
    from memory.working import WorkingMemory
    return WorkingMemory(
        task_id="test-task-001",
        task_type="case1",
        goal="test run",
        agent_id="agent_test",
    )
