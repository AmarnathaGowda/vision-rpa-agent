"""End-to-end: AgentLoop driving a real Chromium browser via deterministic YAML."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

try:
    from playwright.sync_api import sync_playwright  # noqa: F401
    _PW_AVAILABLE = True
except ImportError:
    _PW_AVAILABLE = False

pytestmark = pytest.mark.skipif(not _PW_AVAILABLE, reason="playwright not installed")

SIM_DIR = Path(__file__).parent / "sim" / "pages"


def _materialise_task(task_path: Path) -> dict:
    raw = yaml.safe_load(task_path.read_text())
    # Replace {{LD_BASE_URL}} / {{IIM_BASE_URL}} with the local file URL.
    base = SIM_DIR.resolve().as_uri()
    for step in raw.get("steps", []):
        if isinstance(step.get("target"), str):
            step["target"] = (step["target"]
                              .replace("{{LD_BASE_URL}}", base)
                              .replace("{{IIM_BASE_URL}}", base))
    return raw


@pytest.fixture
def loop_with_browser(session_store):
    from agent.loop import AgentLoop
    from agent.router import ActionRouter
    from executors.browser import BrowserExecutor, BrowserSession
    from executors.selectors import SelectorResolver
    from config.locators import rdweb

    with BrowserSession(headless=True) as session:
        executor = BrowserExecutor(session.page,
                                   resolver=SelectorResolver(locator_map=rdweb.ALL))
        router = ActionRouter(browser=executor)
        loop = AgentLoop(session=session_store, executor=router, agent_id="agent_test")
        yield loop


def test_loop_runs_claim_search_yaml(loop_with_browser, session_store):
    task = _materialise_task(Path("config/tasks/claim_search.yaml"))
    result = loop_with_browser.run(task)
    assert result["status"] == "success"
    assert result["exit_reason"] == "task_complete"
    assert result["steps"] == len(task["steps"])

    actions = session_store.get_actions(task["task_id"])
    assert len(actions) == len(task["steps"])
    assert all(a["result_status"] == "ok" for a in actions)


def test_loop_runs_form_fill_yaml(loop_with_browser, session_store):
    task = _materialise_task(Path("config/tasks/form_fill.yaml"))
    result = loop_with_browser.run(task)
    assert result["status"] == "success"
    assert result["steps"] == len(task["steps"])
