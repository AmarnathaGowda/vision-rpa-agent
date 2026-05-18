"""Case 1 parity harness — legacy Case1Handler.evaluate() vs new agent run.

Asserts that invoking the legacy handler directly produces identical output
to running it through the new framework's `case1_evaluate` tool action. This
is the migration parity gate: until this passes, the SOP-driven path cannot
ship as a replacement.

Fixtures cover the three branches of the SOP decision tree:
  - happy "Already Closed" (DB says closed, closure phrase present)
  - "Not Already Closed" (DB says open)
  - "ambiguous" (header + body resolve to different loans)
  - "failed" (no valid claim)

Tests do NOT touch Playwright or external services. The new-framework path
runs through `Case1ToolExecutor` only — full AgentLoop integration is in a
follow-up.
"""
from __future__ import annotations

import json

import pytest

import legacy  # noqa: F401 — adds legacy/automation to sys.path
from agent.schemas import ActionPlan
from executors.case1_tool import Case1ToolExecutor, evaluate_case1


# ── fixtures (mirror legacy test inputs but constructed directly) ────────
def _ext(candidates: list[dict], raw_text: str = "", ocr: bool = False) -> dict:
    return {"candidates": candidates, "raw_text": raw_text, "ocr_used": ocr}


CASE_AMBIGUOUS_DIFFERENT_LOANS = _ext(
    candidates=[
        {"value": "0819963926", "role": "header", "line": "stub line", "line_index": 0, "confidence": 0.99},
        {"value": "1027388298", "role": "body", "line": "stub line", "line_index": 0, "confidence": 0.99},
    ],
    raw_text="Header references 0819963926; body discusses 1027388298. respectfully closing.",
)

CASE_ALREADY_CLOSED_HEADER_WINS = _ext(
    candidates=[
        {"value": "0823814694", "role": "header", "line": "stub line", "line_index": 0, "confidence": 0.99},
        {"value": "0819963926", "role": "body", "line": "stub line", "line_index": 0, "confidence": 0.99},
    ],
    raw_text="Header is the duplicate filed number; body is the original. respectfully closing.",
)

CASE_NOT_CLOSED = _ext(
    candidates=[
        {"value": "1027388298", "role": "body", "line": "stub line", "line_index": 0, "confidence": 0.99},
    ],
    raw_text="Claim 1027388298 remains under review.",
)

CASE_NO_VALID_CLAIM = _ext(
    candidates=[
        {"value": "9999999999", "role": "header", "line": "stub line", "line_index": 0, "confidence": 0.99},
    ],
    raw_text="Some text with no matching claim. close this claim please.",
)


# ── parity assertions ───────────────────────────────────────────────────
def _legacy_direct(extraction_dict: dict) -> dict:
    """Reference path: call legacy handler in-process."""
    from cases.case1.handler import Case1Handler
    from extraction import ExtractionResult
    from extraction.schema import ClaimCandidate

    candidates = [ClaimCandidate(**c) for c in extraction_dict["candidates"]]
    res = Case1Handler().evaluate(ExtractionResult(
        raw_text=extraction_dict["raw_text"],
        candidates=candidates,
        cleaned_lines=extraction_dict.get("cleaned_lines", []),
        ocr_used=extraction_dict["ocr_used"],
        duration_ms=0,
    ))
    return res.model_dump()


def _new_framework(extraction_dict: dict) -> dict:
    """Migration path: invoke through Case1ToolExecutor."""
    plan = ActionPlan(
        action_type="extract",
        target="case1_evaluate",
        value=json.dumps(extraction_dict),
        app="tool",
    )
    result = Case1ToolExecutor().execute(plan)
    assert result.status == "ok", result.error_msg
    return json.loads(result.extracted_value)


def _drop_volatile(d: dict) -> dict:
    """Strip fields that legitimately differ run-to-run (timings)."""
    out = {**d}
    out.pop("duration_ms", None)
    return out


@pytest.mark.parametrize("fixture,name", [
    (CASE_AMBIGUOUS_DIFFERENT_LOANS, "ambiguous_different_loans"),
    (CASE_ALREADY_CLOSED_HEADER_WINS, "already_closed_header_wins"),
    (CASE_NOT_CLOSED, "not_closed"),
    (CASE_NO_VALID_CLAIM, "no_valid_claim"),
])
def test_case1_parity(fixture, name):
    """Legacy handler and new framework must produce identical output."""
    legacy_out = _drop_volatile(_legacy_direct(fixture))
    new_out = _drop_volatile(_new_framework(fixture))
    assert new_out == legacy_out, (
        f"parity broken for {name}:\n"
        f"  legacy={legacy_out}\n"
        f"  new   ={new_out}"
    )


def test_case1_tool_handles_malformed_value():
    """Tool must surface a malformed extraction_dict as failed, not crash."""
    plan = ActionPlan(action_type="extract", target="case1_evaluate",
                      value="{not-json", app="tool")
    result = Case1ToolExecutor().execute(plan)
    assert result.status == "failed"
    assert "JSONDecodeError" in result.error_msg or "Expecting" in result.error_msg


def test_case1_tool_rejects_unknown_target():
    """Wrong target must fail-fast with a clear error."""
    plan = ActionPlan(action_type="extract", target="case99_evaluate",
                      value="{}", app="tool")
    result = Case1ToolExecutor().execute(plan)
    assert result.status == "failed"
    assert "case99_evaluate" in result.error_msg


def test_evaluate_case1_direct_function_matches_handler():
    """The `evaluate_case1` convenience function must equal the legacy handler."""
    direct = evaluate_case1(CASE_ALREADY_CLOSED_HEADER_WINS)
    legacy = _legacy_direct(CASE_ALREADY_CLOSED_HEADER_WINS)
    assert _drop_volatile(direct) == _drop_volatile(legacy)
