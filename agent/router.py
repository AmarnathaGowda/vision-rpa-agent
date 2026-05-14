"""ActionRouter — dispatches an ActionPlan to the correct executor.

Phase 2 routes only browser actions to BrowserExecutor; desktop/RDP land in
Phase 3. The router is intentionally a small switch — no business logic.
"""
from __future__ import annotations

from typing import Any

from agent.schemas import ActionPlan, ActionResult
from config.logging_config import get_logger

log = get_logger(__name__)

# action_type → "browser" | "desktop" | "rdp" | "noop"
ROUTING_TABLE: dict[str, str] = {
    "navigate": "browser",
    "click":    "browser",
    "type":     "browser",
    "read":     "browser",
    "extract":  "browser",
    "wait":     "browser",
    "js_eval":  "browser",
    # Phase 3 will add: select / window / file_explorer → "desktop"
    "flag_human": "noop",
    "noop":     "noop",
}


class ActionRouter:
    def __init__(self,
                 browser: Any | None = None,
                 desktop: Any | None = None,
                 rdp: Any | None = None) -> None:
        self.browser = browser
        self.desktop = desktop
        self.rdp = rdp

    def execute(self, plan: ActionPlan) -> ActionResult:
        target_executor = ROUTING_TABLE.get(plan.action_type, "unknown")

        if target_executor == "noop":
            log.info("router_noop", action_type=plan.action_type, target=plan.target)
            return ActionResult(status="skipped",
                                error_msg=f"noop for action_type={plan.action_type}")

        if target_executor == "browser":
            if self.browser is None:
                return ActionResult(status="failed",
                                    error_msg="no browser executor registered")
            return self.browser.execute(plan)

        if target_executor == "desktop":
            if self.desktop is None:
                return ActionResult(status="failed",
                                    error_msg="desktop executor not implemented (Phase 3)")
            return self.desktop.execute(plan)

        if target_executor == "rdp":
            if self.rdp is None:
                return ActionResult(status="failed",
                                    error_msg="rdp executor not implemented (Phase 3)")
            return self.rdp.execute(plan)

        return ActionResult(status="failed",
                            error_msg=f"unroutable action_type={plan.action_type!r}")
