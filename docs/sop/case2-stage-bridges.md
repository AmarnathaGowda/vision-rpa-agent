# SOP — Case 2 Stage 8–13 Bridges (Loan Search, Claim Details, Letter Request, Comm History, Claim Linking, Doc Assignment)

These stages run *only* when a Case 2 doc's claim search returned `empty` and IIM fallback produced a `best_loan_no` for that doc.

Each stage is deterministically dispatched by the loop's per-doc state machine. Because the legacy `cases/case2/stage7..stage10` modules already encapsulate the full multi-click flow per stage, the planner emits a single tool action per stage rather than trying to micro-manage each click.

## Stage 8 — Loan Search (standalone)
- `navigate → /lossdrafts/`
- `type → ld-field-loan-no` value=`<best_loan_no>`
- `click → ld-search-submit`
- `js_eval → loan_link_probe` returns `found` if `ld-loan-link-<loan_no>` exists.
- `navigate → /lossdrafts/claim-details?loan_no=<loan_no>` (mirror of legacy direct goto for reliability).
- `extract → case2_scrape_claim_details` → stores `claim_details_<doc_id>` in working memory.

## Stage 9 — Claim Details
- Already on the page after Stage 8's navigate. No further actions; verification is the successful scrape.

## Stage 10 — Letter Request
- `click → ld-cd-letter-requests-header` (expand sidebar section)
- `click → ld-cd-letter-requests-add` (open Create Letter panel)
- `extract → case2_run_stage7` value=JSON(claim_details) — bridges to legacy `stage7_letter_request.run` for template select, CSR email sync, save + toast verification.

## Stage 11 — Communication History
- `extract → case2_run_stage8` value=JSON(claim_details) — bridges to legacy `stage8_communication_history.run`.

## Stage 12 — Claim Linking
- `extract → case2_run_stage9` value=JSON(claim_details) — bridges to legacy `stage9_claim_linking.run`.

## Stage 13 — Document Assignment
- `extract → case2_run_stage10` value=JSON(claim_details) — bridges to legacy `stage10_document_assignment.run`.

Each bridge returns `{"ok": bool}`. Loop records `case2_stage_<n>_<doc_id> = True` on success; the next stage's guardrail fires.

## Failure handling
- If a bridge returns `ok=false`, the loop records the failure on that doc and continues to the next doc's fallback chain (legacy demo prints a warning and continues — we match that behaviour).
