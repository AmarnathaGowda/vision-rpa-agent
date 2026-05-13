# Skill: Agent Memory

Patterns for the three-tier memory system: working dict, SQLite session, ChromaDB knowledge.

## Tier 1 — Working Memory (in-process dict)

Zero-latency. Lost on crash. Always paired with SQLite checkpoint after every action.

```python
# memory/working.py
from dataclasses import dataclass, field
from typing import Any

@dataclass
class WorkingMemory:
    task_id: str
    task_type: str                       # case1 | case2 | case3 | case4
    goal: str
    agent_id: str
    step: int = 0
    current_app: str = "browser"         # browser | desktop | rdp | file_explorer
    current_url: str = ""
    extracted_values: dict = field(default_factory=dict)
    open_tabs: list = field(default_factory=list)  # Playwright page refs
    rdp_session: Any = None
    last_action: dict | None = None
    last_result: dict | None = None
    retry_counts: dict = field(default_factory=dict)   # step_key → count
    decisions_log: list = field(default_factory=list)  # last N actions
    hitl_pending: bool = False
    task_complete: bool = False
    exit_reason: str = ""

    def to_json(self) -> dict:
        """Serialisable snapshot for SQLite checkpoint — excludes live objects."""
        return {
            k: v for k, v in self.__dict__.items()
            if k not in ("open_tabs", "rdp_session")  # not serialisable
        }

    @classmethod
    def from_checkpoint(cls, data: dict) -> "WorkingMemory":
        return cls(**{k: v for k, v in data.items()
                      if k in cls.__dataclass_fields__})
```

## Tier 2 — Session Memory (SQLite)

File-based, crash-safe, WAL mode. One `.db` file per agent.

```python
# memory/session.py
import sqlite3
from pathlib import Path
from config.settings import settings

class SessionMemory:

    def __init__(self, agent_id: str):
        db_path = Path(settings.db_dir) / f"{agent_id}.db"
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")   # concurrent read/write
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def write_checkpoint(self, task_id: str, step: int, working: WorkingMemory) -> None:
        self.conn.execute(
            "INSERT INTO checkpoints (task_id, step, working_json) VALUES (?,?,?)",
            (task_id, step, json.dumps(working.to_json()))
        )
        self.conn.commit()

    def load_checkpoint(self, task_id: str) -> WorkingMemory | None:
        row = self.conn.execute(
            "SELECT working_json FROM checkpoints WHERE task_id=? ORDER BY id DESC LIMIT 1",
            (task_id,)
        ).fetchone()
        if row:
            return WorkingMemory.from_checkpoint(json.loads(row["working_json"]))
        return None

    def get_running_tasks(self, agent_id: str) -> list[dict]:
        """Used at startup to detect crash recovery scenarios."""
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE agent_id=? AND status='running'",
            (agent_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def claim_task(self, task_id: str, agent_id: str) -> bool:
        """Atomic claim — prevents two agents taking the same task."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE task_queue SET status='running', agent_id=?, claimed_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND status='pending'",
                (agent_id, task_id)
            )
            return cur.rowcount == 1

    def log_action(self, task_id: str, step: int, plan, result) -> None:
        self.conn.execute(
            "INSERT INTO actions "
            "(task_id, step, action_type, target, value, result_status, error_msg, duration_ms, screenshot) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (task_id, step, plan.action_type, plan.target, plan.value,
             result.status, result.error, result.duration_ms, result.screenshot_path)
        )
        self.conn.commit()

    def write_hitl(self, task_id: str, agent_id: str, reason: str,
                   screenshot: str, context: dict, timeout_minutes: int = 30) -> int:
        cur = self.conn.execute(
            "INSERT INTO hitl_queue "
            "(task_id, agent_id, reason, screenshot, context_json, timeout_at) "
            "VALUES (?,?,?,?,?,datetime('now',?))",
            (task_id, agent_id, reason, screenshot, json.dumps(context),
             f"+{timeout_minutes} minutes")
        )
        self.conn.commit()
        # Update task status
        self.conn.execute(
            "UPDATE tasks SET status='hitl_wait' WHERE task_id=?", (task_id,)
        )
        self.conn.commit()
        return cur.lastrowid

    def poll_hitl(self, task_id: str) -> dict | None:
        """Returns resolution if human has responded, None if still pending."""
        row = self.conn.execute(
            "SELECT status, resolution FROM hitl_queue "
            "WHERE task_id=? ORDER BY id DESC LIMIT 1",
            (task_id,)
        ).fetchone()
        if row and row["status"] == "resolved":
            return json.loads(row["resolution"]) if row["resolution"] else {}
        if row and row["status"] == "timeout":
            raise HITLTimeoutError(f"HITL for task {task_id} timed out")
        return None
```

## Tier 3 — Long-term Knowledge (ChromaDB)

Semantic search over known UI patterns and error recoveries. Read-only during task. Write only after task completes successfully.

```python
# memory/knowledge.py
import chromadb
from chromadb.config import Settings

class KnowledgeStore:

    def __init__(self, persist_path: str):
        self.client = chromadb.PersistentClient(
            path=persist_path,
            settings=Settings(anonymized_telemetry=False),
        )
        self.ui_patterns    = self.client.get_or_create_collection("ui_patterns")
        self.error_recovery = self.client.get_or_create_collection("error_recoveries")
        self.task_templates = self.client.get_or_create_collection("task_templates")

    # ── Read (safe during task) ────────────────────────────────────────

    def query_ui_pattern(self, app: str, element_desc: str) -> dict | None:
        results = self.ui_patterns.query(
            query_texts=[element_desc],
            where={"app_name": {"$eq": app}},
            n_results=1,
        )
        if not results["documents"][0]:
            return None
        distance = results["distances"][0][0]
        if distance > 0.25:   # too dissimilar — cache miss
            return None
        meta = results["metadatas"][0][0]
        if meta.get("confidence", 0) < 0.85:
            return None
        return meta

    def query_error_recovery(self, error_text: str, app: str) -> dict | None:
        results = self.error_recovery.query(
            query_texts=[error_text],
            where={"app_name": {"$eq": app}},
            n_results=1,
        )
        if not results["documents"][0]:
            return None
        meta = results["metadatas"][0][0]
        if meta.get("success_rate", 0) < 0.90:
            return None   # not reliable enough for automatic recovery
        return meta

    # ── Write (only called from _finalise() after task success) ───────

    def store_ui_pattern(self, app: str, element_desc: str, selector: str,
                         action_type: str, success_count: int = 1) -> None:
        doc_id = f"{app}_{element_desc}".replace(" ", "_")[:64]
        existing = self._get_by_id(self.ui_patterns, doc_id)
        if existing:
            # Increment success count and recalculate confidence
            new_count = existing["success_count"] + 1
            confidence = min(0.95, 0.75 + (new_count / 20))   # cap at 0.95
            self.ui_patterns.update(
                ids=[doc_id],
                metadatas=[{**existing, "success_count": new_count,
                            "confidence": confidence}]
            )
        else:
            self.ui_patterns.add(
                ids=[doc_id],
                documents=[element_desc],
                metadatas=[{
                    "app_name": app,
                    "element_description": element_desc,
                    "selector": selector,
                    "action_type": action_type,
                    "confidence": 0.75,   # starts low, grows with use
                    "success_count": 1,
                    "last_validated": utcnow_date(),
                }]
            )

    def store_error_recovery(self, error_pattern: str, app: str,
                             recovery_action: str, succeeded: bool) -> None:
        doc_id = f"{app}_{error_pattern[:32]}".replace(" ", "_")
        existing = self._get_by_id(self.error_recovery, doc_id)
        if existing:
            times = existing["times_seen"] + 1
            successes = existing.get("times_succeeded", 0) + (1 if succeeded else 0)
            self.error_recovery.update(
                ids=[doc_id],
                metadatas=[{**existing, "times_seen": times,
                            "times_succeeded": successes,
                            "success_rate": successes / times}]
            )
        else:
            self.error_recovery.add(
                ids=[doc_id],
                documents=[error_pattern],
                metadatas=[{
                    "app_name": app,
                    "error_pattern": error_pattern,
                    "recovery_action": recovery_action,
                    "success_rate": 1.0 if succeeded else 0.0,
                    "times_seen": 1,
                    "times_succeeded": 1 if succeeded else 0,
                }]
            )
```

## Checkpoint Resume Pattern

```python
# run_agent.py — startup crash recovery check
def start_or_resume(task_goal: TaskGoal, agent_id: str) -> TaskResult:
    session = SessionMemory(agent_id)
    knowledge = KnowledgeStore(settings.chroma_path)

    # Check for unfinished task from previous crash
    running = session.get_running_tasks(agent_id)
    if running:
        task_id = running[0]["task_id"]
        working = session.load_checkpoint(task_id)
        if working:
            log.warning("resuming_from_checkpoint",
                        task_id=task_id, step=working.step)
            return AgentLoop(session, knowledge).resume(working, task_goal)

    # Normal start
    return AgentLoop(session, knowledge).run(task_goal)
```

## Pattern Lifecycle Rules

```python
PATTERN_RULES = {
    "initial_confidence": 0.75,
    "auto_approve_threshold": 0.90,    # auto-execute without HITL above this
    "promotion_after_uses": 5,          # promote to 0.90 after 5 successes
    "max_confidence": 0.95,
    "stale_after_days": 30,             # re-validate if not used in 30 days
    "revalidate_on_app_version_change": True,
}
```
