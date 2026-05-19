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
from config.logging_config import get_logger

log = get_logger(__name__)


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


def extract_pdf_from_url(pdf_url_or_path: str, *,
                          ocr_max_pages: int = 2) -> dict:
    """Run the legacy extract_from_pdf pipeline on a PDF identified by a
    URL or a local file path. Returns a JSON-serialisable dict matching
    the ExtractionResult shape Case 1's evaluator expects.

    - If ``pdf_url_or_path`` looks like a local file path (starts with
      "/" or exists on disk), read the bytes directly. This is the path
      taken when ``click_open_popup`` has already downloaded the bytes
      via the authenticated browser context.
    - Otherwise, treat it as a URL and try a plain HTTP GET. (Will only
      work for unauthenticated endpoints; authenticated ones return a
      login redirect and we'll get HTML instead of a PDF.)

    The legacy pipeline is the only one that produces ``candidates`` with
    ``role: header|body`` annotations — the new framework's field-spec
    pipeline returns a different shape and isn't suitable for Case 1.
    """
    from extraction import extract_from_pdf
    import os

    log.info("case1_extract_pdf_start", source=pdf_url_or_path)

    looks_like_path = (
        pdf_url_or_path.startswith("/")
        or pdf_url_or_path.startswith("file://")
        or os.path.exists(pdf_url_or_path)
    )

    if looks_like_path:
        local_path = pdf_url_or_path.removeprefix("file://")
        with open(local_path, "rb") as f:
            pdf_bytes = f.read()
        log.info("case1_extract_pdf_loaded_from_disk",
                 path=local_path, size_bytes=len(pdf_bytes))
    else:
        import httpx
        with httpx.Client(follow_redirects=True, timeout=30.0) as c:
            resp = c.get(pdf_url_or_path)
            resp.raise_for_status()
            pdf_bytes = resp.content
        log.info("case1_extract_pdf_url_downloaded",
                 url=pdf_url_or_path, size_bytes=len(pdf_bytes))
    extraction = extract_from_pdf(pdf_bytes, ocr_max_pages=ocr_max_pages)
    return {
        "candidates": [
            {
                "value": c.value,
                "role": c.role,
                "line": c.line,
                "line_index": c.line_index,
                "confidence": c.confidence,
            }
            for c in extraction.candidates
        ],
        "cleaned_lines": list(extraction.cleaned_lines),
        "raw_text": extraction.raw_text,
        "ocr_used": bool(extraction.ocr_used),
        "duration_ms": int(extraction.duration_ms),
    }


class Case1ToolExecutor:
    """Thin executor that the ActionRouter dispatches to when
    ``plan.target == "case1_evaluate"``. The router selects this via
    ``plan.app == "tool"`` (see agent/router.py)."""

    def execute(self, plan: ActionPlan) -> ActionResult:
        start = time.monotonic()
        try:
            if plan.target == "case1_evaluate":
                extraction_dict = json.loads(plan.value) if plan.value else {}
                result = evaluate_case1(extraction_dict)
                return ActionResult(
                    status="ok",
                    extracted_value=json.dumps(result),
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            if plan.target == "case1_extract_pdf":
                # plan.value is the PDF URL (the captured pdf_url).
                pdf_url = (plan.value or "").strip()
                if not pdf_url:
                    return ActionResult(
                        status="failed",
                        error_msg="case1_extract_pdf: empty pdf_url",
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )
                result = extract_pdf_from_url(pdf_url)
                return ActionResult(
                    status="ok",
                    extracted_value=json.dumps(result),
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            return ActionResult(
                status="failed",
                error_msg=f"unknown tool target: {plan.target}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as e:  # noqa: BLE001 — surface as failed, not crash
            return ActionResult(
                status="failed",
                error_msg=f"{type(e).__name__}: {e}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
