# Stage 3-5 — Document Management + Case 1 row + PDF capture

## Purpose
Inside the Loss Drafts module, open the Document Management tab, select
the Case 1 row, click its Link icon to open the PDF in a new tab, and
capture the PDF bytes into working memory.

## Preconditions
- `current_stage == "document_management"`.
- URL contains `/lossdrafts/` and the LD shell is rendered.

## Steps (intent — not selectors)
1. Click the "Document Management" tab (locator key: `tab_document_mgmt`).
2. Wait for the document grid to render. There should be ≥ 1 row visible.
3. Click the Case 1 row (locator key: `case1_row`). The row should
   highlight; downstream actions require a row to be selected.
4. Click the Link icon on the Case 1 row (locator key: `case1_link`).
   This opens a NEW tab with the PDF viewer.
5. Capture the PDF bytes. The simplest agent-native approach:
   - Read the `href` attribute of the Link icon (action: `extract` with
     target=`case1_link` and value=`get_attribute:href`).
   - Use `extract_pdf` action with that URL — the FileExecutor's
     pipeline can fetch + extract in one call.
6. Close the popup tab (action: `click` with target=`close_pdf_tab`).

## Done when
- `working.extracted_values["pdf_bytes_captured"] = True` OR
- `working.extracted_values["pdf_url"]` is set to a non-empty string.
- `current_stage` advances to `pdf_extraction`.

## Recovery rules
- **Tab click missed**: retry once. If still wrong, HITL — page layout
  may have changed; operator can teach a new selector for `tab_document_mgmt`.
- **Row not visible**: HITL — the simulation may have lost the Case 1
  seed data. Operator restart needed.
- **Link icon click does not open a new tab**: the row probably wasn't
  selected first. Re-click the row, then re-click the link.
- **PDF tab opens but bytes fetch fails**: HITL with the URL — operator
  may need to authenticate again.

## Notes
The legacy flow uses a `context.expect_page` listener to capture the new
tab. The agent runtime doesn't have native popup handling yet — the SOP
falls back to "read the href, fetch the URL directly". This is the same
fallback the legacy flow uses (`context.request.get(pdf_full_url)`).
