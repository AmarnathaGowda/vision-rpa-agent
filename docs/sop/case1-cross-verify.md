# Stage 8 — Cross-verify identity (PDF vs system row)

## Purpose
For the body claim ID that resolved to a real loan row, fetch the
borrower record from the loan database and compare its identity fields
against the names/addresses extracted from the PDF. Surfaces visible
mismatches the human can inspect before the evaluation runs.

## Preconditions
- `current_stage == "cross_verify"`.
- `extracted_values["validations"]` contains at least one entry where
  `role=="body"` and `found==True` (else this stage is a no-op).

## Steps (intent — not selectors)
1. Find the body validation entry. Read its `claim_id` (the actual
   claim number) — this is the lookup key.
2. Invoke `loan_db_lookup` (action_type=`read`, target=`loan_db_lookup`,
   value=<JSON-encoded {"ids": ["<claim_id>"]}>).
3. Receive the loan record (`borrower`, `address`, `city`, `state`, `zip`).
4. Pull the PDF identity from `cleaned_lines` using simple heuristics:
   - "Insured: <name>" or "Submitted by <name>" → name
   - First street-like line containing one of (DRIVE/STREET/ROAD/AVE/
     LANE/BLVD/COURT/WAY/PARKWAY) → address
5. Write both into `extracted_values["pdf_identity"]` and
   `extracted_values["system_identity"]`.

## Done when
- Both `pdf_identity` and `system_identity` are set in
  `extracted_values`, even if some fields are `"—"` (no match).
- `current_stage` advances to `case1_evaluate`.

## Decision points
- A name-or-address mismatch does NOT block Stage 9; the comparison is
  for the operator's awareness. The evaluator uses the loan record's
  `status` field as authoritative.
- If the body validation didn't find anything, set both identities to
  empty dicts and advance — the evaluator will route this to a `failed`
  result (no valid claim).

## Recovery rules
- **`loan_db_lookup` raises**: HITL — the loan DB module may not be
  importable. Likely an environment issue, not a workflow problem.
- **PDF identity heuristics return nothing**: log and proceed with
  empty fields. The operator sees the empty state in the cross-verify
  display.
