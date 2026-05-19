# SOP — Case 2 Final Evaluation

## Trigger
- `current_stage == "case2_evaluate"`.

## Action
- Emit `extract → case2_evaluate` with `value = JSON({selected_doc_ids, pdf_records, claim_search_outcomes})`.
- Tool returns the final `Case2FullResult`-shaped dict; loop stores it as `case2_result` and emits `task_complete`.

## Status mapping
- `success` — all 3 outcomes `found=True`.
- `partial` — 1 or 2 outcomes `found=True`.
- `failed`  — 0 outcomes found (including all-empty even after IIM fallback).
