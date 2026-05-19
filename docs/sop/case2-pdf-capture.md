# SOP — Case 2 PDF Capture (multi-doc)

## Trigger
- `current_stage == "pdf_capture"` and `task_type == "case2"`.

## Goal
For each of the 3 selected docs, open the PDF link in a new tab, capture the bytes via the authenticated browser context, save to disk, and remember the local path.

## Steps (deterministic, per `pdf_capture_index`)
For each `doc_id` in `selected_doc_ids` (or the fixed Case 2 set):

1. Emit `extract` action with `app=tool`, `target=case2_open_pdf_capture`, `value=JSON({"link_target":"case2-link-<doc_id>", "doc_id":"<doc_id>"})`.
2. Tool clicks the link, captures bytes from the authenticated `context.request.get(href)`, writes to `downloads/case2/case2_<doc_id>_<ts>.pdf`, closes the popup.
3. Tool returns `{"path", "bytes_len", "doc_id"}` — loop appends to `pdf_records` and increments `pdf_capture_index`.

## Done-when
- `pdf_records.length == 3`.
- Loop sets `pdf_capture_done=true`.

## Next stage
- `ocr_extract`.
