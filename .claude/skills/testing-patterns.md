# Skill: Testing Patterns

How to write tests for this project without real Ollama, browsers, or Windows.

## Golden Rule

**Never let a test fail because Ollama is down, a browser isn't open, or the code runs on macOS.**
Every external dependency must be injectable and mockable at the boundary.

## Fixture Hierarchy

```
tests/
  fixtures/
    mock_llm.py      — MockOpenAIClient (already exists)
    mock_browser.py  — Playwright page stub
    mock_desktop.py  — pywinauto window stub
    mock_session.py  — in-memory SQLite session
  conftest.py        — shared pytest fixtures
  unit/              — single-module tests (no I/O)
  integration/       — multi-module tests (SQLite OK, no network)
  golden_set/        — real extraction inputs with expected outputs
```

## MockOpenAIClient — Usage Patterns

```python
from tests.fixtures.mock_llm import MockOpenAIClient, make_screen_state, make_action_plan

# Sequence of responses (consumed in order)
client = MockOpenAIClient(responses=[
    make_screen_state(app_type="browser", url="http://localhost:8000/ld"),
    make_action_plan(action_type="click", target="#submit-btn", confidence=0.92),
    make_screen_state(app_type="browser", progress="done"),
])

# Inject into perception layer
from agent.perception import PerceptionLayer
perception = PerceptionLayer(client=client)

# Exhaust — returns default make_screen_state() after all responses used
resp4 = client.chat.completions.create(model="test", messages=[])  # default
```

## Mock Browser Page

```python
# tests/fixtures/mock_browser.py
from unittest.mock import AsyncMock, MagicMock


def make_mock_page(url: str = "http://localhost:8000", title: str = "Test") -> AsyncMock:
    """Minimal Playwright Page stub — extend per test."""
    page = AsyncMock()
    page.url = url
    page.title = AsyncMock(return_value=title)
    page.screenshot = AsyncMock(return_value=b"\x89PNG\r\n" + b"\x00" * 100)
    page.query_selector = AsyncMock(return_value=None)
    page.query_selector_all = AsyncMock(return_value=[])
    page.wait_for_selector = AsyncMock(return_value=MagicMock())
    page.fill = AsyncMock()
    page.click = AsyncMock()
    page.evaluate = AsyncMock(return_value=None)
    page.goto = AsyncMock()
    page.content = AsyncMock(return_value="<html><body></body></html>")

    # Simulate an element that exists
    def make_element(text: str = "", value: str = "") -> MagicMock:
        el = MagicMock()
        el.text_content = AsyncMock(return_value=text)
        el.input_value = AsyncMock(return_value=value)
        el.is_visible = AsyncMock(return_value=True)
        el.click = AsyncMock()
        el.fill = AsyncMock()
        return el

    page._make_element = make_element
    return page
```

## Mock pywinauto Window

```python
# tests/fixtures/mock_desktop.py
from unittest.mock import MagicMock


def make_mock_window(title: str = "Test Window") -> MagicMock:
    """pywinauto window stub — safe to import on macOS (no Windows required)."""
    win = MagicMock()
    win.window_text.return_value = title
    win.exists.return_value = True
    win.rectangle.return_value = MagicMock(left=0, top=0, width=lambda: 1920, height=lambda: 1080)

    def make_child(**kwargs) -> MagicMock:
        child = MagicMock()
        child.window_text.return_value = kwargs.get("title", "")
        child.click_input = MagicMock()
        child.set_focus = MagicMock()
        child.type_keys = MagicMock()
        return child

    win.child_window.side_effect = make_child
    return win
```

## In-Memory SQLite Session

```python
# tests/fixtures/mock_session.py
import sqlite3
from memory.session import SessionStore


def make_test_session() -> SessionStore:
    """Real SessionStore backed by :memory: — no file I/O, isolated per test."""
    store = SessionStore.__new__(SessionStore)
    store.db_path = ":memory:"
    store._conn = sqlite3.connect(":memory:", check_same_thread=False)
    store._conn.execute("PRAGMA journal_mode=WAL")
    store._init_schema()   # creates all tables
    return store
```

## conftest.py — Shared Fixtures

```python
# tests/conftest.py
from __future__ import annotations
import pytest
from tests.fixtures.mock_llm import MockOpenAIClient, make_screen_state, make_action_plan
from tests.fixtures.mock_browser import make_mock_page
from tests.fixtures.mock_session import make_test_session


@pytest.fixture
def llm_client():
    return MockOpenAIClient()


@pytest.fixture
def browser_page():
    return make_mock_page()


@pytest.fixture
def session_store():
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
```

## Testing Extraction (Golden Set Pattern)

```python
# tests/golden_set/test_pdf_extraction.py
from __future__ import annotations
import pytest
from pathlib import Path
from executors.extraction import ExtractionPipeline

GOLDEN_DIR = Path(__file__).parent / "samples"


@pytest.mark.parametrize("pdf_name,expected_claim_id,min_confidence", [
    ("clean_scan.pdf",  "CLM-20240101-001", 0.85),
    ("fax_copy.pdf",    "CLM-20240215-033", 0.70),
    ("digital_pdf.pdf", "CLM-20231122-099", 0.95),
])
def test_extraction_golden(pdf_name, expected_claim_id, min_confidence):
    pdf_bytes = (GOLDEN_DIR / pdf_name).read_bytes()
    pipeline = ExtractionPipeline()
    result = pipeline.extract(pdf_bytes, task_id="golden-test")
    assert result.confidence >= min_confidence
    assert expected_claim_id in result.text or \
           result.fields.get("claim_id", {}).get("value") == expected_claim_id
```

## Testing WorkingMemory Checkpoint

```python
def test_checkpoint_round_trip(session_store, working_memory):
    working_memory.step = 5
    working_memory.claim_ids["header"] = "CLM-001"

    data = working_memory.to_json()
    session_store.write_checkpoint("test-task-001", data)
    loaded = session_store.load_checkpoint("test-task-001")

    from memory.working import WorkingMemory
    restored = WorkingMemory.from_checkpoint(loaded)
    assert restored.step == 5
    assert restored.claim_ids["header"] == "CLM-001"
```

## Testing Recovery Handler

```python
def test_session_expired_triggers_relogin(llm_client, browser_page):
    """RecoveryHandler must detect 'Session Expired' and re-run login."""
    from agent.recovery import RecoveryHandler
    browser_page.content = AsyncMock(return_value="<html>Session Expired</html>")
    # ... inject and assert relogin called
```

## What NOT to Do

- **Don't `patch("pywinauto.Desktop")`** at the module level — it breaks import on macOS.
  Instead, inject the desktop handle as a parameter and pass the mock in tests.
- **Don't use `time.sleep()` in tests** — use `monkeypatch.setattr(time, "sleep", lambda s: None)`.
- **Don't write integration tests that require Ollama running** — those belong in a separate
  `tests/e2e/` suite skipped in CI with `@pytest.mark.e2e`.
- **Don't use `@pytest.mark.asyncio` on every test** — `asyncio_mode = "auto"` is set in
  `pyproject.toml`; the decorator is redundant and clutters test files.

## Marking Tests

```python
# Skip on non-Windows (pywinauto only works on Windows)
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_rdp_keepalive(): ...

# Requires real Ollama — excluded from CI
@pytest.mark.e2e
def test_full_case1_run(): ...

# Requires golden PDF samples — skip if not present
@pytest.mark.skipif(not (Path(__file__).parent / "samples").exists(),
                    reason="golden set not checked in")
def test_extraction_golden(): ...
```

## Running Tests

```bash
# All unit + integration (no real services needed)
poetry run pytest tests/unit tests/integration -q

# Golden set only (requires PDF samples)
poetry run pytest tests/golden_set/ -v

# Full suite including E2E (requires Ollama + browser)
poetry run pytest --run-e2e

# Single test with live output
poetry run pytest tests/test_smoke.py::test_imports_agent -s
```
