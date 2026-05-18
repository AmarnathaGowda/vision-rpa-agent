# Case 1 — "Already Closed" claim evaluation

Version: 1.0 (ported from `legacy/automation/cases/case1/decision.py`)

## Purpose

Decide whether an insurance claim referenced in a carrier letter is **already
closed** in our loan database. The carrier letter contains two claim
identifiers — one in the header (where the letter is filed against) and one
in the body (the carrier's own reference). They may or may not match. This
SOP describes how to choose between them and how to interpret the result.

## Preconditions

- Working memory contains an `extraction` dict with:
  - `candidates`: list of `{value, role}` where `role ∈ {"header", "body"}`.
  - `raw_text`: full text of the letter (used for closure-phrase detection).
  - `ocr_used`: whether the extractor used OCR (audit only).

## Steps (intent, not selectors)

1. **Look up every claim ID in the loan database.** Call the
   `loan_db_lookup` tool with all candidate values; receive `{claim_id ->
   loan_record | None}`.
2. **Apply the header-priority rule** to pick the winner:
   - If both header and body resolve to a real loan record, **the header wins**.
   - If only the header resolves, the header wins.
   - If only the body resolves, the body wins.
   - If neither resolves, the result is `status="failed"` with reason
     `no_valid_claim`.
3. **Detect closure phrases** in `raw_text`. The presence of any of
   `close this claim`, `closed without payment`, `claim is closed`,
   `duplicate claim`, `respectfully close`, `respectfully closing`,
   `we are closing`, `no further action` is a signal.
4. **Compose the result**:
   - `case = "Already Closed"` if the winner's `loan_record.status == "Closed"`.
   - `case = "Not Already Closed"` otherwise.

## Decision points

- **Ambiguous claims**: if header and body BOTH resolve, but to DIFFERENT
  `loan_id`s, the status is `ambiguous` (not `success`). Reason code:
  `CLAIMS_RESOLVE_TO_DIFFERENT_LOANS`. The same loan_id reached from two
  different claim numbers is *not* ambiguous — that's the normal
  duplicate-claim pattern and counts as success.
- **DB says closed but no closure phrase**: emit reason code
  `MISSING_CLOSURE_PHRASE`. Still treated as Already Closed (DB is
  authoritative for the `case` field).
- **DB says open but the letter contains a closure phrase**: emit reason
  code `CLOSURE_PHRASE_BUT_DB_OPEN`. Treated as Not Already Closed (again,
  DB is authoritative).
- **Loan record absent**: emit `LOAN_NOT_FOUND`; combined with no winner,
  this is the `failed` path.

## Reason codes (audit trail)

Every result carries a `reason_codes` list. Reason codes from steps 2 and 4
are appended in order encountered:

- `header_wins_priority` | `only_header_valid` | `only_body_valid` | `no_valid_claim`
- `LOAN_NOT_FOUND`
- `MISSING_CLOSURE_PHRASE`
- `CLOSURE_PHRASE_BUT_DB_OPEN`
- `CLOSURE_PHRASE_FOUND` (when `winner is None` AND closure phrase present —
  evidence-without-record case)
- `CLAIMS_RESOLVE_TO_DIFFERENT_LOANS`

## Done when

Working memory contains every field of the legacy `Case1Result` schema:

- `header_claim_id`, `body_claim_id` (from input candidates)
- `selected_claim_id`, `loan_id`, `loan_status`
- `case`, `status`, `reason_codes`
- `candidates`, `duration_ms`, `ocr_used`, `handler_version`

## Notes for the planner

This case is **evaluation-only** — no Playwright, no DOM, no screen
perception. The "tools" are pure functions exposed via the new framework's
`read` action with these targets:

- `loan_db_lookup` → calls `legacy.cases.case1.loan_db.lookup_many(ids)`
- `detect_closure_phrase` → calls `legacy.cases.case1.decision.detect_closure_phrase(text)`
- `case1_evaluate` → invokes the full legacy handler as a single tool call
  (parity baseline; the SOP-driven path above replaces it once the planner
  reliably reproduces the same output).

When `LIGHTWEIGHT_MODE=true`, the planner may call `case1_evaluate` directly
instead of orchestrating steps 1-4 individually. Both paths must produce
identical `Case1Result` output — verified by `tests/test_parity_case1.py`.
