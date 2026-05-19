"""Pydantic models for the observe → reason → act → store loop."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

AppType = Literal["browser", "desktop", "rdp", "file_explorer", "dialog", "unknown"]
TaskProgress = Literal["not_started", "in_progress", "blocked", "complete"]
ActionType = Literal[
    # Browser + generic
    "click", "type", "navigate", "read", "extract", "wait",
    "flag_human", "js_eval", "noop",
    # Desktop / dropdown
    "select_option",
    # File Explorer / shared folder / extraction pipeline
    "file_navigate", "file_open", "extract_pdf", "read_excel",
    # RDP lifecycle
    "rdp_launch", "rdp_disconnect", "rdp_reconnect",
    # Loop control — the LLM emits this when it believes the task goal
    # has been satisfied. The loop exits cleanly with status=success.
    "task_complete",
    # Workflow control — advances WorkingMemory.current_stage without
    # exiting the task. Used to drive multi-stage SOP-driven flows.
    "stage_complete",
    # Click a link/button whose action triggers a file download, capture
    # the download, and (if it's an HTML launcher with a meta-refresh URL)
    # navigate the current tab to that URL. Used for RDWeb-style "launch
    # app via HTML launcher" patterns (e.g. clicking the Loss Drafts tile).
    "click_download_open",
    # Click a link with target="_blank". Capture the new tab's URL,
    # close the popup, return the URL as extracted_value. Used for the
    # Case 1 PDF link which opens in a popup tab.
    "click_open_popup",
]

ExecutorScope = Literal["browser", "desktop", "rdp", "file", "tool", "auto"]


class VisibleElement(BaseModel):
    label: str = ""
    type: str = ""
    testid: str = ""


class ScreenState(BaseModel):
    app_type: AppType = "unknown"
    state_summary: str = ""
    current_url: str = ""
    visible_elements: list[VisibleElement] = Field(default_factory=list)
    error_present: bool = False
    blocking_modal: bool = False
    task_progress: TaskProgress = "in_progress"
    blocking_issue: str | None = None
    confidence: float = 0.0

    @field_validator("confidence")
    @classmethod
    def _conf_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"confidence must be in [0,1], got {v}")
        return v


class ActionPlan(BaseModel):
    action_type: ActionType
    target: str = ""
    value: str = ""
    reason: str = ""
    confidence: float = 0.0
    fallback: str = ""
    is_financial: bool = False
    requires_hitl: bool = False
    cache_hit: bool = False
    # When "auto" (default), the ActionRouter infers the executor from
    # action_type. Set explicitly to override (e.g. when "click" should hit a
    # desktop app instead of the browser).
    app: ExecutorScope = "auto"
    # Optional keyboard modifiers for click actions ("Meta"=Cmd, "Control",
    # "Alt", "Shift"). Required for Case 2 multi-row selection where rows
    # 2-3 must Cmd+click to keep prior rows selected. Empty list = plain
    # click (Case 1 path — unchanged behaviour).
    modifiers: list[str] = []

    @model_validator(mode="before")
    @classmethod
    def _coerce_null_strings(cls, data):
        # Local VLMs (Ollama/minicpm-v) routinely emit "field": null for
        # optional string fields. Coerce None → "" so we don't trip Pydantic
        # on every plan.
        if isinstance(data, dict):
            for key in ("target", "value", "reason", "fallback"):
                if data.get(key) is None:
                    data[key] = ""
        return data

    @field_validator("confidence")
    @classmethod
    def _conf_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"confidence must be in [0,1], got {v}")
        return v


class ActionResult(BaseModel):
    status: Literal["ok", "skipped", "failed", "deferred"] = "skipped"
    error_msg: str = ""
    extracted_value: str = ""
    duration_ms: int = 0
    screenshot_path: str = ""
