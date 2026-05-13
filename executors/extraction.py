"""PDF and document extraction pipeline: pdfplumber → Tesseract → VLM → HITL."""
from __future__ import annotations
from pathlib import Path


class ExtractionPipeline:
    def extract(self, document: Path | bytes, fields: list[str]) -> dict:
        raise NotImplementedError
