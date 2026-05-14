"""3-agent parallel test — verifies isolation of SQLite, audit logs, and
HITL queues across concurrently-running AgentLoop instances.

Phase 5 exit criterion: three agents on three tasks share no state and
each finishes its own task cleanly.
"""
from __future__ import annotations

import threading

from agent.loop import AgentLoop
from agent.schemas import ActionResult
from hitl.queue import HITLQueue
from hitl.runner import HITLRunner
from memory.session import SessionMemory


class OkExecutor:
    def execute(self, plan):
        if plan.action_type == "flag_human":
            return ActionResult(status="deferred", error_msg="hitl")
        return ActionResult(status="ok", duration_ms=1)


def _task(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "task_type": "case1",
        "goal": f"goal for {task_id}",
        "steps": [
            {"action_type": "click", "target": "first"},
            {"action_type": "click", "target": "second"},
            {"action_type": "click", "target": "third"},
        ],
    }


def test_three_agents_run_in_parallel(tmp_path, monkeypatch):
    """Spawn 3 AgentLoop instances in threads with isolated SessionMemory
    (separate DB files). Verify each completes its own task and that the
    SQLite/audit dirs don't collide."""
    monkeypatch.setattr("config.settings.settings.db_dir", str(tmp_path / "db"))
    monkeypatch.setattr("config.settings.settings.audit_log_dir",
                        str(tmp_path / "audit"))

    results: dict[str, dict] = {}
    errors: list[Exception] = []

    def run_one(agent_id: str) -> None:
        try:
            store = SessionMemory(agent_id=agent_id)
            loop = AgentLoop(session=store, executor=OkExecutor(),
                             agent_id=agent_id)
            runner = HITLRunner(loop=loop,
                                queue=HITLQueue(session=store, poll_interval_s=0),
                                sleep=lambda s: None)
            results[agent_id] = runner.run_task(_task(f"task-{agent_id}"))
            store.conn.close()
        except Exception as e:  # noqa: BLE001 — surface to main thread
            errors.append(e)

    threads = [threading.Thread(target=run_one, args=(a,))
               for a in ("agent_1", "agent_2", "agent_3")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"agent thread raised: {errors}"
    assert set(results) == {"agent_1", "agent_2", "agent_3"}
    for agent_id, res in results.items():
        assert res["status"] == "success", (agent_id, res)
        assert res["exit_reason"] == "task_complete"
        assert res["steps"] == 3

    # Per-agent SQLite files exist, no shared file.
    db_files = sorted(p.name for p in (tmp_path / "db").glob("*.db"))
    assert db_files == ["agent_1.db", "agent_2.db", "agent_3.db"]

    # Per-agent audit logs exist, no interleaved single file.
    audit_files = sorted(p.name for p in (tmp_path / "audit").glob("*.ndjson"))
    assert audit_files == ["agent_1.ndjson", "agent_2.ndjson", "agent_3.ndjson"]


def test_dashboard_aggregates_three_agents(tmp_path, monkeypatch):
    """The FastAPI dashboard should discover all 3 agent DBs and expose
    their HITL rows independently."""
    monkeypatch.setattr("config.settings.settings.db_dir", str(tmp_path))
    from hitl import server as srv
    srv._clear_session_cache()

    try:
        for agent_id in ("a1", "a2", "a3"):
            store = SessionMemory(agent_id=agent_id)
            store.start_task("t", "case1", "g", agent_id)
            store.write_hitl("t", agent_id, "low_conf", "", {})
            store.conn.close()

        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        agents = {a["agent_id"]: a for a in client.get("/api/agents").json()}
        assert set(agents) == {"a1", "a2", "a3"}
        assert all(agents[a]["pending"] == 1 for a in agents)

        # Resolve a2's review and confirm only a2 changes.
        hid = client.get("/api/agent/a2/hitl").json()[0]["id"]
        r = client.post(f"/api/agent/a2/resolve/{hid}",
                        json={"action": "approve"})
        assert r.status_code == 200
        agents = {a["agent_id"]: a for a in client.get("/api/agents").json()}
        assert agents["a2"]["pending"] == 0
        assert agents["a1"]["pending"] == 1
        assert agents["a3"]["pending"] == 1
    finally:
        srv._clear_session_cache()
