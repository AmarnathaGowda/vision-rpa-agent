# Stage 7 — Claim Search validation (per candidate)

## Purpose
For each extracted claim ID, search the simulation's Document Management
Claim Search panel to confirm whether the system actually has a row for
it. Records `found / not_found` per candidate in working memory.

## Preconditions
- `current_stage == "claim_validation"`.
- `working_memory.extracted_values["candidates"]` is a non-empty list.
- URL contains `/lossdrafts/document-management`. (If not — navigate.)

## Steps (intent — not selectors)
For each candidate in `extracted_values["candidates"]`:

1. Type `candidate.value` into the search input (locator key:
   `doc_search_claim`).
2. Click the search submit button (locator key: `doc_search_submit`).
3. Wait for the results region to update.
4. Inspect the result rows. If the result region shows the
   "no records found" message (locator key: `doc_claim_results_empty`),
   record `found=False`. Otherwise read the first row's
   `data-testid` (format: `ld-doc-claim-row-<loan_no>`) and record
   `found=True, loan_no=<extracted>`.
5. Append the validation entry to
   `extracted_values["validations"]` (a list of dicts with
   `claim_id, role, found, note, loan_no`).

## Done when
- `len(extracted_values["validations"]) == len(extracted_values["candidates"])`.
- `current_stage` advances to `cross_verify`.

## Decision points
- The header claim ID is typically an Allstate-filed duplicate that was
  rejected — expect `found=False` with note "rejected duplicate".
- The body claim ID is the real claim — expect `found=True` with a
  `loan_no` recoverable from the row.

## Recovery rules
- **Search input cannot be located**: HITL — likely a page-state issue
  (a popup closed the doc-management tab). Operator can re-navigate.
- **Both candidates return empty**: HITL — possible system data issue;
  the agent shouldn't continue to evaluation with no valid claim.
- **Single candidate returns empty when expected found**: log and proceed;
  Stage 9 will classify this through the `no_valid_claim` reason code.
