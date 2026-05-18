"""Case 1 evaluator tool — bridges the legacy Case1Handler into the new framework.

Exposed as ``ActionPlan(action_type="extract", target="case1_evaluate",
value=<json-encoded ExtractionResult-shaped dict>)``. The agent loop sees
this exactly like any other extract action: result lands in
``WorkingMemory.extracted_values``.

This is the *parity baseline* — it invokes the legacy handler as a black box
so we can validate that the new framework produces byte-identical output.
A subsequent migration step will replace this with SOP-driven step orchestration
(loan_db_lookup → closure check → winner pick → compose result).
"""
from __future__ import annotations

import json
import time

import legacy  # noqa: F401  — side-effect: adds legacy/automation to sys.path

from agent.schemas import ActionPlan, ActionResult


def evaluate_case1(extraction_dict: dict) -> dict:
    """Call the legacy Case1Handler with a JSON-shaped ExtractionResult."""
    from cases.case1.handler import Case1Handler
    from extraction import ExtractionResult
    from extraction.schema import ClaimCandidate

    candidates = [ClaimCandidate(**c) for c in extraction_dict.get("candidates", [])]
    extraction = ExtractionResult(
        raw_text=extraction_dict.get("raw_text", ""),
        candidates=candidates,
        cleaned_lines=extraction_dict.get("cleaned_lines", []),
        ocr_used=bool(extraction_dict.get("ocr_used", False)),
        duration_ms=int(extraction_dict.get("duration_ms", 0)),
    )
    result = Case1Handler().evaluate(extraction)
    return result.model_dump()


class Case1ToolExecutor:
    """Thin executor that the ActionRouter dispatches to when
    ``plan.target == "case1_evaluate"``. The router selects this via
    ``plan.app == "tool"`` (see agent/router.py)."""

    def execute(self, plan: ActionPlan) -> ActionResult:
        if plan.target != "case1_evaluate":
            return ActionResult(
                status="failed",
                error_msg=f"unknown tool target: {plan.target}",
            )
        start = time.monotonic()
        try:
            extraction_dict = json.loads(plan.value) if plan.value else {}
            result = evaluate_case1(extraction_dict)
            return ActionResult(
                status="ok",
                extracted_value=json.dumps(result),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as e:  # noqa: BLE001 — surface as failed, not crash
            return ActionResult(
                status="failed",
                error_msg=f"{type(e).__name__}: {e}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
