"""SQLite session memory — tasks, actions, checkpoints, HITL queue."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path

from config.settings import settings


class SessionMemory:
    SCHEMA_VERSION = 1

    def __init__(self, agent_id: str) -> None:
        db_path = Path(settings.db_dir) / f"{agent_id}.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def _create_schema(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS schema_version (version INTEGER);

            CREATE TABLE IF NOT EXISTS tasks (
                task_id      TEXT PRIMARY KEY,
                task_type    TEXT NOT NULL,
                goal         TEXT,
                status       TEXT DEFAULT 'pending',
                agent_id     TEXT,
                started_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                result_json  TEXT
            );

            CREATE TABLE IF NOT EXISTS actions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id      TEXT NOT NULL,
                step         INTEGER,
                action_type  TEXT,
                target       TEXT,
                value        TEXT,
                result_status TEXT,
                error_msg    TEXT,
                duration_ms  INTEGER,
                screenshot   TEXT,
                timestamp    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS extractions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id      TEXT NOT NULL,
                field_name   TEXT,
                raw_value    TEXT,
                normalized   TEXT,
                confidence   REAL,
                method       TEXT,
                source_doc   TEXT,
                is_financial INTEGER DEFAULT 0,
                timestamp    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS checkpoints (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id      TEXT NOT NULL,
                step         INTEGER,
                working_json TEXT,
                timestamp    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS hitl_queue (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id      TEXT NOT NULL,
                agent_id     TEXT,
                reason       TEXT,
                screenshot   TEXT,
                context_json TEXT,
                status       TEXT DEFAULT 'pending',
                resolution   TEXT,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at  TIMESTAMP,
                timeout_at   TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS task_queue (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type    TEXT NOT NULL,
                payload      TEXT NOT NULL,
                status       TEXT DEFAULT 'pending',
                agent_id     TEXT,
                claimed_at   TIMESTAMP,
                completed_at TIMESTAMP,
                result       TEXT
            );
        """)
        self.conn.commit()

    def write_checkpoint(self, task_id: str, step: int, working) -> None:
        self.conn.execute(
            "INSERT INTO checkpoints (task_id, step, working_json) VALUES (?,?,?)",
            (task_id, step, json.dumps(working.to_json())),
        )
        self.conn.commit()

    def load_checkpoint(self, task_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT working_json FROM checkpoints WHERE task_id=? ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        return json.loads(row["working_json"]) if row else None

    def get_running_tasks(self, agent_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE agent_id=? AND status='running'",
            (agent_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def write_hitl(self, task_id: str, agent_id: str, reason: str,
                   screenshot: str, context: dict,
                   timeout_minutes: int = 30) -> int:
        cur = self.conn.execute(
            "INSERT INTO hitl_queue "
            "(task_id, agent_id, reason, screenshot, context_json, timeout_at) "
            "VALUES (?,?,?,?,?,datetime('now',?))",
            (task_id, agent_id, reason, screenshot,
             json.dumps(context), f"+{timeout_minutes} minutes"),
        )
        self.conn.execute(
            "UPDATE tasks SET status='hitl_wait' WHERE task_id=?", (task_id,)
        )
        self.conn.commit()
        return cur.lastrowid

    def poll_hitl(self, task_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT status, resolution FROM hitl_queue "
            "WHERE task_id=? ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if row and row["status"] == "resolved":
            return json.loads(row["resolution"]) if row["resolution"] else {}
        return None

    def list_hitl(self, status: str | None = None) -> list[dict]:
        """Return HITL rows (optionally filtered by status) for the dashboard."""
        if status is None:
            rows = self.conn.execute(
                "SELECT * FROM hitl_queue ORDER BY id DESC"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM hitl_queue WHERE status=? ORDER BY id DESC",
                (status,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_hitl(self, hitl_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM hitl_queue WHERE id=?", (hitl_id,),
        ).fetchone()
        return dict(row) if row else None

    def resolve_hitl(self, hitl_id: int, resolution: dict) -> None:
        """Mark a HITL row resolved and persist the resolution payload."""
        self.conn.execute(
            "UPDATE hitl_queue SET status='resolved', resolution=?, "
            "resolved_at=CURRENT_TIMESTAMP WHERE id=?",
            (json.dumps(resolution), hitl_id),
        )
        # Move task back to 'running' so the supervisor knows to resume it.
        row = self.conn.execute(
            "SELECT task_id FROM hitl_queue WHERE id=?", (hitl_id,)
        ).fetchone()
        if row:
            self.conn.execute(
                "UPDATE tasks SET status='running' WHERE task_id=?",
                (row["task_id"],),
            )
        self.conn.commit()

    # ── task lifecycle ────────────────────────────────────────────────────
    def start_task(self, task_id: str, task_type: str, goal: str,
                   agent_id: str) -> None:
        """Idempotent — INSERT OR REPLACE so resume() works."""
        self.conn.execute(
            "INSERT OR REPLACE INTO tasks "
            "(task_id, task_type, goal, status, agent_id) "
            "VALUES (?,?,?, 'running', ?)",
            (task_id, task_type, goal, agent_id),
        )
        self.conn.commit()

    def complete_task(self, task_id: str, status: str, result: dict | None = None) -> None:
        self.conn.execute(
            "UPDATE tasks SET status=?, completed_at=CURRENT_TIMESTAMP, "
            "result_json=? WHERE task_id=?",
            (status, json.dumps(result or {}), task_id),
        )
        self.conn.commit()

    # ── action log ────────────────────────────────────────────────────────
    def log_action(self, task_id: str, step: int, plan, result,
                   screenshot: str = "") -> int:
        """Append one row to actions table. plan/result are pydantic models."""
        cur = self.conn.execute(
            "INSERT INTO actions "
            "(task_id, step, action_type, target, value, result_status, error_msg, "
            " duration_ms, screenshot) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                task_id, step,
                plan.action_type, plan.target, plan.value,
                result.status, result.error_msg, result.duration_ms,
                screenshot or result.screenshot_path,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_actions(self, task_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM actions WHERE task_id=? ORDER BY id ASC",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── extraction log ────────────────────────────────────────────────────
    def log_extraction(self, task_id: str, field_name: str, raw_value: str,
                       normalized: str, confidence: float, method: str,
                       source_doc: str = "", is_financial: bool = False) -> int:
        cur = self.conn.execute(
            "INSERT INTO extractions "
            "(task_id, field_name, raw_value, normalized, confidence, method, "
            " source_doc, is_financial) VALUES (?,?,?,?,?,?,?,?)",
            (task_id, field_name, raw_value, normalized, confidence, method,
             source_doc, 1 if is_financial else 0),
        )
        self.conn.commit()
        return cur.lastrowid
