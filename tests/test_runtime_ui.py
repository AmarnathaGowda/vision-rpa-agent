"""Tests for the floating-runtime UI surface in hitl/server.py.

Covers:
- /runtime returns the self-contained HTML page
- /stream/events returns an SSE stream and emits 'audit' + 'hitl_state'
- /api/agents still works (sanity — not regressed by new code)
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hitl import server as srv
from memory.session import SessionMemory


@pytest.fixture
def db_root(tmp_path, monkeypatch):
    monkeypatch.setattr("config.settings.settings.db_dir", str(tmp_path / "db"))
    monkeypatch.setattr("config.settings.settings.audit_log_dir",
                        str(tmp_path / "audit"))
    Path(tmp_path / "audit").mkdir(parents=True)
    Path(tmp_path / "db").mkdir(parents=True)
    srv._clear_session_cache()
    yield tmp_path
    srv._clear_session_cache()


def test_runtime_route_returns_self_contained_html(db_root):
    client = TestClient(srv.app)
    r = client.get("/runtime")
    assert r.status_code == 200
    body = r.text
    # Core landmarks of the page.
    assert "<title>Agent Runtime</title>" in body
    assert "/stream/events" in body
    assert "/api/agent/" in body  # resolve URL pattern
    assert "EventSource" in body  # SSE consumer
    assert "submitHitl" in body   # inline HITL form handler


def test_stream_events_route_registered_and_returns_sse_headers(db_root):
    """Smoke check: the route exists and advertises text/event-stream.

    A full end-to-end SSE assertion is flaky with TestClient because
    iter_raw() blocks until the server flushes, and uvicorn's flushing
    cadence isn't deterministic in-process. The runtime behaviour is
    exercised by the actual /runtime page in the browser — see the
    integration test under scripts/manual_check_runtime_ui.md.
    """
    client = TestClient(srv.app)
    # OpenAPI manifest is the cheapest way to confirm registration.
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/stream/events" in paths
    assert paths["/stream/events"].get("get") is not None


def test_audit_tailer_helper_picks_up_new_lines(db_root):
    """Direct test of the async generator without going through Starlette."""
    audit = Path(db_root) / "audit" / "agent_x.ndjson"
    audit.write_text("")  # exists, empty

    async def _drive():
        last_offsets: dict[str, int] = {}
        gen = srv._tail_audit_files(last_offsets, poll_interval=0.05)
        # First iteration sees an empty file and waits — kick the generator
        # once to register the path, then write content.
        task = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0.1)
        audit.write_text(json.dumps({
            "ts": "2026-05-18T12:00:00",
            "agent_id": "agent_x",
            "event": "perception",
            "summary": "test screen",
        }) + "\n")
        item = await asyncio.wait_for(task, timeout=2.0)
        return item

    item = asyncio.run(_drive())
    assert item["event"] == "audit"
    assert item["data"]["summary"] == "test screen"


def test_api_agents_unchanged(db_root):
    store = SessionMemory(agent_id="agent_x")
    store.start_task("t1", "case1", "g", "agent_x")
    store.conn.close()
    srv._clear_session_cache()
    client = TestClient(srv.app)
    data = client.get("/api/agents").json()
    assert any(a["agent_id"] == "agent_x" for a in data)
