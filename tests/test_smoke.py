"""Smoke tests — verify imports and project structure are correct."""
from __future__ import annotations


def test_imports_agent():
    from agent.loop import AgentLoop
    from agent.perception import PerceptionLayer
    from agent.planner import ActionPlanner
    from agent.recovery import RecoveryHandler
    assert True


def test_imports_executors():
    from executors.browser import BrowserExecutor
    from executors.desktop import DesktopExecutor
    from executors.rdp import RDPHandler
    from executors.extraction import ExtractionPipeline
    from executors.file_ops import FileExecutor
    assert True


def test_imports_memory():
    from memory.working import WorkingMemory
    assert True


def test_imports_hitl():
    from hitl.queue import HITLQueue
    assert True


def test_imports_config():
    from config.settings import settings
    assert settings.confidence_threshold == 0.75
    assert settings.financial_confidence_threshold == 0.90
    assert settings.max_loop_steps == 50


def test_working_memory_serialise():
    from memory.working import WorkingMemory
    wm = WorkingMemory(task_id="t1", task_type="case2", goal="test", agent_id="agent_01")
    data = wm.to_json()
    assert "task_id" in data
    assert "open_tabs" not in data      # excluded — not serialisable
    assert "rdp_session" not in data    # excluded — not serialisable
    restored = WorkingMemory.from_checkpoint(data)
    assert restored.task_id == "t1"
    assert restored.step == 0


def test_smoke_task_yaml_loads():
    import yaml
    from pathlib import Path
    path = Path("config/tasks/smoke_test.yaml")
    assert path.exists(), f"missing: {path}"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["task_id"] == "smoke_test"
    assert data["task_type"] == "smoke"


def test_logging_config_idempotent():
    from config.logging_config import configure_logging, get_logger
    configure_logging("agent_test")
    configure_logging("agent_test")  # second call must not raise
    log = get_logger("test")
    log.info("smoke_log_event", check=True)


def test_mock_llm_fixture():
    from tests.fixtures.mock_llm import MockOpenAIClient, make_screen_state
    client = MockOpenAIClient(responses=[make_screen_state(app_type="browser")])
    resp = client.chat.completions.create(model="test", messages=[])
    import json
    data = json.loads(resp.choices[0].message.content)
    assert data["app_type"] == "browser"
