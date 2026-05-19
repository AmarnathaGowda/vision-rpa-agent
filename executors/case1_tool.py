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
    """Dispatches ``plan.target`` to legacy bridge functions when
    ``plan.app == "tool"``. Despite the historical name, this executor
    now serves both Case 1 and Case 2 targets.

    Case 2 stage bridges (``case2_run_stage7``..``case2_run_stage10``)
    drive the Playwright page directly via the injected BrowserExecutor;
    they call legacy ``stage7_letter_request.run`` etc. as black boxes.
    """

    def __init__(self, browser=None) -> None:
        # Optional BrowserExecutor — required only for Case 2 tool targets
        # that drive the page (multi-doc PDF capture, stage 7-10 bridges).
        self.browser = browser

    def execute(self, plan: ActionPlan) -> ActionResult:  # noqa: C901
        start = time.monotonic()
        try:
            if plan.target == "case1_evaluate":
                extraction_dict = json.loads(plan.value) if plan.value else {}
                result = evaluate_case1(extraction_dict)
                return self._ok(json.dumps(result), start)
            if plan.target == "case1_extract_pdf":
                pdf_url = (plan.value or "").strip()
                if not pdf_url:
                    return self._fail("case1_extract_pdf: empty pdf_url", start)
                result = extract_pdf_from_url(pdf_url)
                return self._ok(json.dumps(result), start)

            # ── Case 2 tool targets ────────────────────────────────────
            if plan.target == "case2_extract_pdf":
                # Shared extract pipeline — same payload shape as case1.
                pdf_path_or_url = (plan.value or "").strip()
                if not pdf_path_or_url:
                    return self._fail("case2_extract_pdf: empty source", start)
                result = extract_pdf_from_url(pdf_path_or_url)
                return self._ok(json.dumps(result), start)

            if plan.target == "case2_fuzzy_score":
                # plan.value: JSON {"ocr_name", "ocr_address", "ocr_carrier",
                #                   "iim_rows": [...], "iim_carrier"?}
                # Returns: {"scored": [...], "best": {...}, "threshold": 60.0}
                payload = json.loads(plan.value) if plan.value else {}
                result = case2_fuzzy_score(payload)
                return self._ok(json.dumps(result), start)

            if plan.target == "case2_scrape_iim_rows":
                if self.browser is None or self.browser.page is None:
                    return self._fail("case2_scrape_iim_rows: no browser", start)
                rows = case2_scrape_iim_rows(self.browser.page)
                return self._ok(json.dumps(rows), start)

            if plan.target == "case2_scrape_loan_details_carrier":
                if self.browser is None or self.browser.page is None:
                    return self._fail("case2_scrape_carrier: no browser", start)
                carrier = case2_scrape_loan_details_carrier(self.browser.page)
                return self._ok(carrier, start)

            if plan.target == "case2_scrape_claim_details":
                if self.browser is None or self.browser.page is None:
                    return self._fail("case2_scrape_claim_details: no browser", start)
                data = case2_scrape_claim_details(self.browser.page)
                return self._ok(json.dumps(data), start)

            if plan.target == "case2_open_pdf_capture":
                # plan.value: JSON {"link_target": "...", "doc_id": "..."}
                # Opens popup, captures bytes via authenticated context,
                # saves to disk, returns local path + size.
                if self.browser is None or self.browser.page is None:
                    return self._fail("case2_open_pdf_capture: no browser", start)
                payload = json.loads(plan.value) if plan.value else {}
                result = case2_open_pdf_capture(
                    self.browser, payload.get("link_target", ""),
                    payload.get("doc_id", ""),
                )
                return self._ok(json.dumps(result), start)

            if plan.target in ("case2_run_stage7", "case2_run_stage8",
                                "case2_run_stage9", "case2_run_stage10"):
                if self.browser is None or self.browser.page is None:
                    return self._fail(f"{plan.target}: no browser", start)
                claim_data = json.loads(plan.value) if plan.value else {}
                ok = case2_run_legacy_stage(plan.target, self.browser.page,
                                              claim_data)
                return self._ok(json.dumps({"ok": bool(ok)}), start)

            if plan.target == "case2_evaluate":
                # plan.value: JSON of the accumulated Case 2 state ready to
                # be materialized into a Case2FullResult.
                payload = json.loads(plan.value) if plan.value else {}
                result = case2_evaluate(payload)
                return self._ok(json.dumps(result), start)

            return self._fail(f"unknown tool target: {plan.target}", start)
        except Exception as e:  # noqa: BLE001 — surface as failed, not crash
            return self._fail(f"{type(e).__name__}: {e}", start)

    def _ok(self, extracted: str, start: float) -> ActionResult:
        return ActionResult(
            status="ok", extracted_value=extracted,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    def _fail(self, msg: str, start: float) -> ActionResult:
        return ActionResult(
            status="failed", error_msg=msg,
            duration_ms=int((time.monotonic() - start) * 1000),
        )


# ── Case 2 bridge helpers ───────────────────────────────────────────────
# Pure helpers (no Playwright dependency) live alongside the executor so
# the tool-target dispatch can stay terse and testable.

def case2_fuzzy_score(payload: dict) -> dict:
    """Score IIM result rows against OCR-extracted fields.

    Mirrors ``_score_candidates`` + ``_refine_score_with_carrier`` from
    legacy/automation/demo/run_case2_e2e_demo.py.
    Initial pass uses name 50% + address 50%; when ``iim_carrier`` is
    also supplied, the best candidate is re-scored as 40/40/20.
    """
    from rapidfuzz import fuzz
    import re

    def _norm(t: str) -> str:
        t = (t or "").lower()
        t = re.sub(r"[^\w\s]", " ", t)
        return re.sub(r"\s+", " ", t).strip()

    ocr_name = payload.get("ocr_name", "")
    ocr_addr = payload.get("ocr_address", "")
    ocr_carrier = payload.get("ocr_carrier", "")
    iim_rows = payload.get("iim_rows", []) or []
    iim_carrier = payload.get("iim_carrier", "")
    threshold = float(payload.get("threshold", 60.0))

    scored = []
    for row in iim_rows:
        full_addr = " ".join([row.get("address", ""), row.get("city", ""),
                              row.get("state", ""), row.get("zip", "")]).strip()
        n = fuzz.token_sort_ratio(_norm(ocr_name), _norm(row.get("contact", ""))) \
            if ocr_name and row.get("contact") else 0
        a = fuzz.token_sort_ratio(_norm(ocr_addr), _norm(full_addr)) \
            if ocr_addr and full_addr else 0
        overall = n * 0.5 + a * 0.5
        scored.append({**row, "iim_full_addr": full_addr,
                       "name_score": round(float(n), 1),
                       "addr_score": round(float(a), 1),
                       "carrier_score": None,
                       "overall": round(overall, 1),
                       "below_threshold": overall < threshold})
    if not scored:
        return {"scored": [], "best": None, "threshold": threshold}

    best = max(scored, key=lambda x: x["overall"])
    if iim_carrier and ocr_carrier:
        n = fuzz.token_sort_ratio(_norm(ocr_name), _norm(best.get("contact", "")))
        a = fuzz.token_sort_ratio(_norm(ocr_addr), _norm(best["iim_full_addr"]))
        c = fuzz.token_sort_ratio(_norm(ocr_carrier), _norm(iim_carrier))
        overall = n * 0.4 + a * 0.4 + c * 0.2
        best = {**best, "name_score": round(float(n), 1),
                "addr_score": round(float(a), 1),
                "carrier_score": round(float(c), 1),
                "overall": round(overall, 1),
                "below_threshold": overall < threshold}
        for i, c in enumerate(scored):
            if c.get("loan_no") == best.get("loan_no"):
                scored[i] = best
                break
    best["selected"] = True
    return {"scored": scored, "best": best, "threshold": threshold,
            "passed_threshold": best["overall"] >= threshold}


def case2_scrape_iim_rows(page) -> list[dict]:
    """Scrape visible rows from the IIM Loan Search results table."""
    rows = page.locator('[data-testid="pf-results-body"] [data-loan-no]').all()
    out = []
    for row in rows:
        loan_no = row.get_attribute("data-loan-no") or ""
        def _t(tid: str) -> str:
            return (page.locator(f'[data-testid="{tid}"]').text_content() or "").strip()
        out.append({"loan_no": loan_no,
                    "contact": _t(f"pf-result-contact-{loan_no}"),
                    "address": _t(f"pf-result-address-{loan_no}"),
                    "city":    _t(f"pf-result-city-{loan_no}"),
                    "state":   _t(f"pf-result-state-{loan_no}"),
                    "zip":     _t(f"pf-result-zip-{loan_no}")})
    return out


def case2_scrape_loan_details_carrier(page) -> str:
    loc = page.locator('[data-testid="pf-ins-carrier-1"] input')
    if loc.count() > 0:
        return (loc.get_attribute("value") or "").strip()
    return ""


def case2_scrape_claim_details(page) -> dict:
    """Scrape Claim Details fields used by stage7-10."""
    def _text(sel: str) -> str:
        try:
            return page.locator(sel).inner_text(timeout=2000).strip()
        except Exception:  # noqa: BLE001
            return ""
    return {
        "status":       _text('[data-testid="ld-cd-status"]'),
        "contact_name": _text('[data-testid="ld-cd-borrower"]'),
        "ld_id":        _text('[data-testid="ld-cd-ld-id"]'),
        "claim_no":     _text('[data-testid="ld-cd-claim-no"]'),
        "loan_no":      _text('[data-testid="ld-cd-loan-no"]'),
    }


def case2_open_pdf_capture(browser, link_target: str, doc_id: str) -> dict:
    """Pulse the link, click it (opens new tab), capture the PDF bytes via
    authenticated request context, save to disk, close the tab.

    Returns ``{"path": <local file>, "bytes_len": int, "doc_id": str}``.
    """
    from pathlib import Path
    import time as _t

    page = browser.page
    sel = browser.resolver.resolve(page, link_target).selector
    link_loc = page.locator(sel).first
    pdf_href = link_loc.get_attribute("href") or ""
    base_url = "/".join(page.url.split("/")[:3])  # scheme://host
    pdf_url = pdf_href if pdf_href.startswith("http") else f"{base_url}{pdf_href}"

    log.info("case2_open_pdf_capture_start", doc_id=doc_id, sel=sel,
             pdf_url=pdf_url)
    with page.context.expect_page(timeout=12_000) as tab_info:
        link_loc.click()
    pdf_tab = tab_info.value
    try:
        resp = page.context.request.get(pdf_url)
        if not resp.ok:
            raise RuntimeError(f"PDF fetch failed: {resp.status} — {pdf_url}")
        pdf_bytes = resp.body()
    finally:
        try:
            pdf_tab.close()
        except Exception:  # noqa: BLE001
            pass
        page.bring_to_front()

    out_dir = Path("downloads") / "case2"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"case2_{doc_id}_{int(_t.time()*1000)}.pdf"
    path.write_bytes(pdf_bytes)
    log.info("case2_open_pdf_capture_done", doc_id=doc_id,
             path=str(path), bytes_len=len(pdf_bytes))
    return {"path": str(path), "bytes_len": len(pdf_bytes), "doc_id": doc_id}


def case2_run_legacy_stage(target: str, page, claim_data: dict) -> bool:
    """Bridge to legacy cases.case2.stage{7,8,9,10}*.run() as a black box.

    The legacy modules expect callbacks (overlay_fn, log_fn, screenshot_fn).
    We pass no-op shims so the modules don't crash and write nothing to
    the runtime overlay (that channel belongs to the new floating UI).
    """
    import legacy  # noqa: F401 — sys.path side-effect
    import os

    overlay = lambda *a, **kw: None  # noqa: E731
    log_cb = lambda _p, msg: log.info("case2_legacy_stage", target=target, msg=msg)  # noqa: E731
    screenshot = lambda *a, **kw: None  # noqa: E731

    base_url = os.environ.get("LD_BASE_URL", "http://localhost:8000")
    doc_id = claim_data.get("doc_id", "")

    if target == "case2_run_stage7":
        from cases.case2 import stage7_letter_request as m
        return bool(m.run(page, claim_data,
                          overlay_fn=overlay, log_fn=log_cb,
                          screenshot_fn=screenshot, doc_id=doc_id))
    if target == "case2_run_stage8":
        from cases.case2 import stage8_communication_history as m
        return bool(m.run(page, claim_data,
                          overlay_fn=overlay, log_fn=log_cb,
                          screenshot_fn=screenshot, doc_id=doc_id))
    if target == "case2_run_stage9":
        from cases.case2 import stage9_claim_linking as m
        return bool(m.run(page, claim_data, base_url=base_url,
                          overlay_fn=overlay, log_fn=log_cb,
                          screenshot_fn=screenshot, doc_id=doc_id))
    if target == "case2_run_stage10":
        from cases.case2 import stage10_document_assignment as m
        return bool(m.run(page, claim_data, base_url=base_url,
                          overlay_fn=overlay, log_fn=log_cb,
                          screenshot_fn=screenshot, doc_id=doc_id))
    raise ValueError(f"unknown legacy stage target: {target}")


def case2_evaluate(state: dict) -> dict:
    """Synthesize the final Case 2 result from accumulated working memory.

    Expects ``state`` to contain:
      - selected_doc_ids: list[str]
      - pdf_records: list[dict]
      - claim_search_outcomes: list[dict]
    """
    outcomes = state.get("claim_search_outcomes") or []
    matched = sum(1 for o in outcomes if o.get("found"))
    total = len(outcomes)
    if total == 0:
        status = "failed"
    elif matched == total:
        status = "success"
    elif matched > 0:
        status = "partial"
    else:
        status = "failed"
    return {
        "case": "Multi-Record Selection — PDF + OCR + Claim Search + IIM Match",
        "status": status,
        "matched_count": matched,
        "total_count": total,
        "selected_doc_ids": state.get("selected_doc_ids", []),
        "pdf_records": state.get("pdf_records", []),
        "claim_search_outcomes": outcomes,
    }
