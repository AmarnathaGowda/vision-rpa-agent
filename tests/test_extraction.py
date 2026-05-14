"""Tests for ExtractionPipeline — pdfplumber / OCR / VLM all mocked."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from executors.extraction import (
    ExtractionPipeline, FieldSpec,
    _pick_match, _value_after_alias,
)


def _stub_pdfplumber(pages_text: list[str]):
    pdfplumber = MagicMock()
    ctx = MagicMock()
    pdfplumber.open.return_value = ctx
    ctx.__enter__.return_value = ctx
    ctx.pages = [MagicMock(extract_text=lambda t=t: t) for t in pages_text]
    return pdfplumber


def _real_png_bytes() -> bytes:
    """Generate a real 1x1 PNG so Pillow.Image.open() accepts the OCR-tier input."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1, 1), (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _stub_fitz_with_pages(n: int):
    fitz = MagicMock()
    doc = MagicMock()
    doc.page_count = n
    page = MagicMock()
    pix = MagicMock()
    pix.tobytes.return_value = _real_png_bytes()
    page.get_pixmap.return_value = pix
    doc.load_page.return_value = page
    fitz.open.return_value = doc
    return fitz, doc


def test_pdfplumber_tier_finds_value_after_alias(tmp_path):
    pdf = tmp_path / "claim.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    pages = [
        "Header line\nLoan Number: 0156312522\nStatus: In Review\nAmount: $10,640.58\n"
    ]
    pipe = ExtractionPipeline(_pdfplumber=_stub_pdfplumber(pages))
    result = pipe.extract(pdf, [
        FieldSpec(name="loan_number", aliases=["Loan Number"]),
        FieldSpec(name="status", aliases=["Status"]),
        FieldSpec(name="amount", aliases=["Amount"], is_financial=True),
    ])
    assert result.tiers_used == ["pdfplumber"]
    assert result.fields["loan_number"].value == "0156312522"
    assert result.fields["status"].value == "In Review"
    assert result.fields["amount"].value == "$10,640.58"
    assert all(fx.method == "pdfplumber" for fx in result.fields.values())


def test_pattern_only_field(tmp_path):
    pdf = tmp_path / "claim.pdf"; pdf.write_bytes(b"%PDF-1.4\n")
    pages = ["Some preamble. The number is CLM-99421 somewhere."]
    pipe = ExtractionPipeline(_pdfplumber=_stub_pdfplumber(pages))
    result = pipe.extract(pdf, [FieldSpec(name="claim_id", pattern=r"CLM-\d+")])
    assert result.fields["claim_id"].value == "CLM-99421"


def test_falls_through_to_ocr_when_pdfplumber_returns_empty(tmp_path):
    pdf = tmp_path / "scanned.pdf"; pdf.write_bytes(b"%PDF-1.4\n")
    # pdfplumber returns blank — scanned PDF.
    pdfp = _stub_pdfplumber([""])
    fitz, _ = _stub_fitz_with_pages(1)
    tess = MagicMock()
    tess.image_to_string.return_value = "Loan Number: 0156312522\nStatus: Closed\n"
    pipe = ExtractionPipeline(_pdfplumber=pdfp, _fitz=fitz, _tesseract=tess)
    result = pipe.extract(pdf, [
        FieldSpec(name="loan_number", aliases=["Loan Number"]),
        FieldSpec(name="status", aliases=["Status"]),
    ])
    assert "ocr" in result.tiers_used
    assert result.fields["loan_number"].method == "ocr"
    assert result.fields["status"].value == "Closed"


def test_falls_through_to_vlm_when_ocr_also_empty(tmp_path):
    pdf = tmp_path / "image_only.pdf"; pdf.write_bytes(b"%PDF-1.4\n")
    pdfp = _stub_pdfplumber([""])
    fitz, _ = _stub_fitz_with_pages(1)
    tess = MagicMock()
    tess.image_to_string.return_value = ""   # OCR found nothing

    vlm = MagicMock()
    vlm.chat.completions.create.return_value.choices = [MagicMock()]
    import json
    vlm.chat.completions.create.return_value.choices[0].message.content = json.dumps({
        "amount": {"value": "$1,234.00", "confidence": 0.81, "location_hint": "page 1"},
    })
    pipe = ExtractionPipeline(vlm_client=vlm, _pdfplumber=pdfp, _fitz=fitz, _tesseract=tess)
    result = pipe.extract(pdf, [FieldSpec(name="amount", aliases=["Amount"],
                                          is_financial=True)])
    assert "vlm" in result.tiers_used
    fx = result.fields["amount"]
    assert fx.method == "vlm"
    assert fx.value == "$1,234.00"
    # Financial gate at 0.90; the VLM returned 0.81 → must be flagged for HITL.
    assert fx.hitl_required is True


def test_missing_field_emits_zero_confidence_stub(tmp_path):
    pdf = tmp_path / "claim.pdf"; pdf.write_bytes(b"%PDF-1.4\n")
    pipe = ExtractionPipeline(_pdfplumber=_stub_pdfplumber(["no relevant content here"]),
                              _fitz=_stub_fitz_with_pages(1)[0],
                              _tesseract=MagicMock(image_to_string=lambda *_: ""))
    pipe._vlm_client = MagicMock()
    pipe._vlm_client.chat.completions.create.return_value.choices = [MagicMock()]
    pipe._vlm_client.chat.completions.create.return_value.choices[0].message.content = "{}"
    result = pipe.extract(pdf, [FieldSpec(name="loan_number", aliases=["Loan Number"])])
    fx = result.fields["loan_number"]
    assert fx.value is None
    assert fx.confidence == 0.0
    assert fx.hitl_required is True


def test_missing_file_returns_error(tmp_path):
    pipe = ExtractionPipeline()
    result = pipe.extract(tmp_path / "nope.pdf", [FieldSpec(name="x")])
    assert result.error is not None
    assert "file_not_found" in result.error


def test_normalise_accepts_strings_and_dicts():
    specs = ExtractionPipeline._normalise_fields([
        "loan_number",
        {"name": "amount", "aliases": ["Amount"], "is_financial": True},
        FieldSpec(name="claim", aliases=["Claim"]),
    ])
    assert specs[0].name == "loan_number"
    assert specs[0].aliases == ["loan_number"]
    assert specs[1].is_financial is True
    assert specs[2].name == "claim"


def test_value_after_alias_falls_back_to_next_line():
    lines = ["Loan Number:", "0156312522", "Status: Open"]
    val = _value_after_alias(lines[0], "Loan Number", lines, 0)
    assert val == "0156312522"


def test_pick_match_returns_first_captured_group():
    import re
    m = re.search(r"(\d{4})-(\d{2})", "Issued 2026-05")
    assert _pick_match(m) == "2026"
