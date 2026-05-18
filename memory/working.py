"""In-process task-scoped memory. Lost on crash — always checkpoint to SQLite."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkingMemory:
    task_id: str
    task_type: str
    goal: str
    agent_id: str
    step: int = 0
    current_app: str = "browser"
    current_url: str = ""
    extracted_values: dict = field(default_factory=dict)
    open_tabs: list = field(default_factory=list)
    rdp_session: Any = None
    last_action: dict | None = None
    last_result: dict | None = None
    retry_counts: dict = field(default_factory=dict)
    decisions_log: list = field(default_factory=list)
    hitl_pending: bool = False
    task_complete: bool = False
    exit_reason: str = ""
    # Workflow-stage tracker (string id matches the SOP file basename
    # without prefix, e.g. "login", "document_management"). Used by the
    # planner to retrieve the right stage SOP and surface progress in
    # the runtime UI. Empty when the task has no declared stages.
    current_stage: str = ""
    stages_completed: list = field(default_factory=list)

    def to_json(self) -> dict:
        return {k: v for k, v in self.__dict__.items()
                if k not in ("open_tabs", "rdp_session")}

    @classmethod
    def from_checkpoint(cls, data: dict) -> "WorkingMemory":
        fields = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in fields})
