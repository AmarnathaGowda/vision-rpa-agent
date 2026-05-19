# SOP — Case 2 IIM (Proctor) Fallback

## Trigger
- Inside `claim_search`, when a doc's claim search returns `empty`.
- Activated by `claim_search_state.fallback = "iim"` set by the loop.

## Sub-state machine
State: `iim_state = {"step", "doc_id", "borrower", "ocr_address", "ocr_carrier", "first_name"}`.

| step | Action |
|------|--------|
| `extract_fields` | Tool call: pull `borrower`/`address`/`carrier` from the doc's OCR cleaned_lines via a tiny regex helper inside the planner (no LLM). |
| `navigate` | `navigate → http://localhost:8000/proctor/loan-search` |
| `type_first_name` | `type → pf-input-contact-name` value=first token of borrower. |
| `submit` | `click → pf-btn-search` |
| `scrape_rows` | `extract → case2_scrape_iim_rows` |
| `score` | `extract → case2_fuzzy_score` with JSON payload |
| `navigate_details` | `navigate → http://localhost:8000/proctor/loan-details?loan_no=<best>` |
| `scrape_carrier` | `extract → case2_scrape_loan_details_carrier` |
| `rescore` | `extract → case2_fuzzy_score` with `iim_carrier` populated |
| `done` | Record `iim_match_result` in working memory; emit `stage_complete → loan_search`. |

## Done-when
- `iim_match_result` set with `best_loan_no`.

## Next sub-stage
- `loan_search`.
