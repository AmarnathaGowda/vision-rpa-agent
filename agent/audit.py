"""Append-only NDJSON audit log — every perception and action plan recorded."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import settings


class AuditLog:
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        log_dir = Path(settings.audit_log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        self.path = log_dir / f"{agent_id}.ndjson"

    def append(self, event: str, **fields: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent_id": self.agent_id,
            "event": event,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
