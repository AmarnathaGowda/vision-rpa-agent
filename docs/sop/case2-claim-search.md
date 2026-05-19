# SOP — Case 2 Claim Search (per-doc loop)

## Trigger
- `current_stage == "claim_search"` and `task_type == "case2"`.

## Per-doc state machine
State: `claim_search_state = {"index", "step"}`. For each doc in order:

| step | Action |
|------|--------|
| `ensure_doc_mgmt` | If URL not on `/document-management`, navigate back; ensure Case 2 rows visible. |
| `type` | `type → ld-doc-search-claim` with value = best candidate's `value` (prefer `role=="header"`, else first). |
| `submit` | `click → ld-doc-search-submit` |
| `probe` | `js_eval → claim_search_result_probe` returning `empty` \| `found:<loan_no>` |
| `select` (only if `found`) | `click → ld-doc-claim-row-<loan_no>` |
| `record` | Append `ClaimSearchOutcome` entry to `claim_search_outcomes`; index++. If `not found`, hand off to `iim_fallback` sub-stage for this doc before advancing. |

## Done-when
- `len(claim_search_outcomes) == 3`.
- Loop sets `claim_search_done=true`.

## Next stage
- `case2_evaluate` (or, if any doc went through IIM fallback, the per-doc fallback chain completes first).
