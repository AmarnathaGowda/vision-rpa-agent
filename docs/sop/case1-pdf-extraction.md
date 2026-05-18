# Stage 6 — PDF extraction (pdfplumber → Tesseract → VLM)

## Purpose
Run the captured PDF through the existing three-tier extraction pipeline
to recover (a) the cleaned text lines, (b) the structured claim ID
candidates with their roles (header/body), and (c) `ocr_used` flag.

## Preconditions
- `current_stage == "pdf_extraction"`.
- `working_memory.extracted_values["pdf_url"]` (or pdf_bytes) is set
  from stage 3-5.

## Steps (intent — not selectors)
1. Invoke the `extract_pdf` action (routed to FileExecutor) with the
   captured `pdf_url`. The pipeline returns an ExtractionResult:
   `candidates`, `cleaned_lines`, `raw_text`, `ocr_used`, `duration_ms`.
2. Persist the extraction in working memory:
   - `extracted_values["candidates"] = <list>`
   - `extracted_values["raw_text"] = <string>`
   - `extracted_values["cleaned_lines"] = <list>`
   - `extracted_values["ocr_used"] = <bool>`

## Done when
- `len(working.extracted_values["candidates"]) > 0`.
- `current_stage` advances to `claim_validation`.

## Recovery rules
- **Zero candidates extracted**: HITL — the PDF may be unreadable or
  poorly OCR'd. Operator can either retry (skip OCR cache) or supply
  candidate values manually via the guidance credential-input.
- **pdfplumber confidence < threshold AND tesseract missing**: the
  pipeline falls through to the VLM tier. This is normal in dev
  environments where `tesseract` isn't installed (A-13).
- **Exception in pipeline**: HITL with the stack trace.

## Notes
- The extraction tier and confidence thresholds are configured in
  [config/settings.py](config/settings.py) via `vlm_max_pages`, `vlm_dpi`,
  `ocr_dpi`. Don't change them at runtime.
- ExtractionResult fields are case-agnostic. Case 1's interpretation of
  "header vs body claim" comes from `extraction.candidates[i].role`,
  which the extractor produces.
