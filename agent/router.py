"""ActionRouter — dispatches an ActionPlan to the correct executor.

Routing strategy:
1. If plan.app is set explicitly (browser/desktop/rdp/file), honour it.
2. Otherwise fall back to ROUTING_TABLE keyed on action_type.

This lets the same primitive (e.g. "click") run against either browser or
desktop without inventing parallel action types.
"""
from __future__ import annotations

from typing import Any

from agent.schemas import ActionPlan, ActionResult
from config.logging_config import get_logger

log = get_logger(__name__)

# action_type → "browser" | "desktop" | "rdp" | "file" | "noop"
ROUTING_TABLE: dict[str, str] = {
    # Generic browser primitives (default scope)
    "navigate":            "browser",
    "click":               "browser",
    "type":                "browser",
    "read":                "browser",
    "extract":             "browser",
    "wait":                "browser",
    "js_eval":             "browser",
    "click_download_open": "browser",
    "click_open_popup":    "browser",
    # Desktop-only
    "select_option": "desktop",
    # File Explorer / network share / extraction
    "file_navigate": "file",
    "file_open":     "file",
    "extract_pdf":   "file",
    "read_excel":    "file",
    # RDP lifecycle
    "rdp_launch":     "rdp",
    "rdp_reconnect":  "rdp",
    "rdp_disconnect": "rdp",
    # No-op (loop handles these as exits/flags, not executor dispatches)
    "flag_human":     "noop",
    "noop":           "noop",
    "task_complete":  "noop",
    "stage_complete": "noop",
}


class ActionRouter:
    def __init__(self,
                 browser: Any | None = None,
                 desktop: Any | None = None,
                 rdp: Any | None = None,
                 file: Any | None = None,
                 tool: Any | None = None) -> None:
        self.browser = browser
        self.desktop = desktop
        self.rdp = rdp
        self.file = file
        # `tool` is used for evaluation-only actions that invoke pure-Python
        # legacy handlers (Case 1 evaluator, validators, etc.) — see
        # executors/case1_tool.py.
        self.tool = tool

    def execute(self, plan: ActionPlan) -> ActionResult:
        scope = self._scope_for(plan)

        if scope == "noop":
            log.info("router_noop", action_type=plan.action_type, target=plan.target)
            return ActionResult(status="skipped",
                                error_msg=f"noop for action_type={plan.action_type}")

        executor = {
            "browser": self.browser,
            "desktop": self.desktop,
            "rdp":     self.rdp,
            "file":    self.file,
            "tool":    self.tool,
        }.get(scope)

        if executor is None:
            return ActionResult(status="failed",
                                error_msg=f"no {scope} executor registered for "
                                          f"action_type={plan.action_type!r}")

        log.debug("router_dispatch", scope=scope, action_type=plan.action_type)
        return executor.execute(plan)

    def _scope_for(self, plan: ActionPlan) -> str:
        if plan.app and plan.app != "auto":
            return plan.app
        return ROUTING_TABLE.get(plan.action_type, "unknown")
