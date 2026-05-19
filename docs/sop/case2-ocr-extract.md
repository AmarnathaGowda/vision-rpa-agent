# SOP — Case 2 OCR Extraction (multi-doc)

## Trigger
- `current_stage == "ocr_extract"` and `task_type == "case2"`.

## Steps (deterministic, per `ocr_index`)
For each entry in `pdf_records`:

1. Emit `extract` action with `app=tool`, `target=case2_extract_pdf`, `value=<local_path>` (from `pdf_records[ocr_index].path`).
2. Tool runs the same legacy `extract_from_pdf` pipeline used by Case 1 (pdfplumber → Tesseract → VLM). Returns `{"candidates","raw_text","cleaned_lines","ocr_used","duration_ms"}`.
3. Loop pushes the extraction dict into `extractions_by_doc[<doc_id>]` and increments `ocr_index`.

## Done-when
- `extractions_by_doc` has 3 entries.
- Loop sets `ocr_extract_done=true`.

## Next stage
- `claim_search`.
