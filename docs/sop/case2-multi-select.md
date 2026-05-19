# SOP — Case 2 Multi-Row Selection

## Trigger
- `current_stage == "multi_select"` and `task_type == "case2"`.

## Goal
Select **three** Case 2 rows simultaneously using the OS multi-select modifier (Cmd on macOS / Ctrl on Windows — Playwright accepts `"Meta"` and translates per platform).

## Steps (deterministic — one click per iteration)
For each `doc_id` in `["8184371", "8184373", "8184372"]`:

1. Click the first cell of that row: target `ld-pending-doc-<doc_id>` (the framework's SelectorResolver resolves the row testid).
2. For the **first** row, click WITHOUT modifiers. For rows 2 and 3, click WITH `modifiers=["Meta"]` so the previously-selected rows stay selected.

After all 3 clicks the rows should carry the CSS class `ld-pending-doc-selected`.

## Done-when
- Working memory key `multi_select_done=true` (set by the loop's tracker after the third successful click).
- Working memory key `selected_doc_ids = ["8184371", "8184373", "8184372"]`.

## Next stage
- `pdf_capture`.
