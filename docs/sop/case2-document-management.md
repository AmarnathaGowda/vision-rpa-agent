# SOP — Case 2 Document Management Tab

## Trigger
- `current_stage == "document_management"` and `task_type == "case2"`.

## Steps
1. If page URL does not contain `/document-management`, click the tab `ld-tab-document-management`.
2. Wait until `data-testid="ld-document-management-page"` is present.
3. If the visible rows for any of the 3 Case 2 doc IDs (`8184371`, `8184373`, `8184372`) are missing, set the `Show All` dropdown (`ld-show-all`) to `all` so all pending rows render.

## Done-when
- All three rows `[data-testid~="ld-pending-doc-<doc_id>"]` are visible.
- Working memory key `document_management_open=true`.

## Next stage
- `multi_select`.
