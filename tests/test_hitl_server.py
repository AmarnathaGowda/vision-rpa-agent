"""FastAPI HITL dashboard tests — uses TestClient, no real network."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hitl import server as srv
from memory.session import SessionMemory


@pytest.fixture
def db_root(tmp_path, monkeypatch):
    """Point settings.db_dir at a fresh tmp dir and clear cached sessions."""
    monkeypatch.setattr("config.settings.settings.db_dir", str(tmp_path))
    srv._clear_session_cache()
    yield tmp_path
    srv._clear_session_cache()


def _seed_agent(db_root, agent_id: str, *, pending_reason: str = "low conf") -> int:
    """Create a real SessionMemory file under db_root and return the HITL id."""
    store = SessionMemory(agent_id=agent_id)
    store.start_task("t1", "case1", "g", agent_id)
    hid = store.write_hitl("t1", agent_id, pending_reason, "", {"plan": {}})
    store.conn.close()
    return hid


def test_dashboard_lists_agents_and_counts(db_root):
    _seed_agent(db_root, "agent_a")
    _seed_agent(db_root, "agent_b")
    client = TestClient(srv.app)
    r = client.get("/api/agents")
    assert r.status_code == 200
    data = {a["agent_id"]: a for a in r.json()}
    assert set(data) == {"agent_a", "agent_b"}
    assert data["agent_a"]["pending"] == 1
    assert data["agent_b"]["pending"] == 1


def test_dashboard_html_root_renders(db_root):
    _seed_agent(db_root, "agent_a")
    client = TestClient(srv.app)
    r = client.get("/")
    assert r.status_code == 200
    assert "agent_a" in r.text
    assert "1 review(s) pending" in r.text or "1</td>" in r.text


def test_api_resolve_marks_row_resolved_and_unblocks_task(db_root):
    hid = _seed_agent(db_root, "agent_a")
    client = TestClient(srv.app)
    r = client.post(
        f"/api/agent/agent_a/resolve/{hid}",
        json={"action": "approve", "resolver": "qa"},
    )
    assert r.status_code == 200, r.text

    # Verify by re-opening DB directly.
    srv._clear_session_cache()
    store = SessionMemory(agent_id="agent_a")
    row = store.get_hitl(hid)
    assert row["status"] == "resolved"
    task = store.conn.execute("SELECT status FROM tasks WHERE task_id='t1'").fetchone()
    assert task["status"] == "running"
    store.conn.close()


def test_api_resolve_rejects_invalid_action(db_root):
    hid = _seed_agent(db_root, "agent_a")
    client = TestClient(srv.app)
    r = client.post(
        f"/api/agent/agent_a/resolve/{hid}",
        json={"action": "nuke"},
    )
    assert r.status_code == 400


def test_api_resolve_rejects_double_resolution(db_root):
    hid = _seed_agent(db_root, "agent_a")
    client = TestClient(srv.app)
    client.post(f"/api/agent/agent_a/resolve/{hid}",
                json={"action": "approve"})
    r = client.post(f"/api/agent/agent_a/resolve/{hid}",
                    json={"action": "approve"})
    assert r.status_code == 409


def test_html_review_page_shows_reason_and_form(db_root):
    hid = _seed_agent(db_root, "agent_a", pending_reason="financial conf below 0.90")
    client = TestClient(srv.app)
    r = client.get(f"/agent/agent_a/review/{hid}")
    assert r.status_code == 200
    assert "financial conf below 0.90" in r.text
    assert "<form" in r.text
    assert "approve" in r.text
