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


# ── multi-page VLM tier ────────────────────────────────────────────────────
def _multi_page_vlm_client(page_responses: dict[int, dict]):
    """Build a mock VLM client that returns different JSON for each page.

    ``page_responses`` maps 1-based page number → JSON dict to return.
    Page detection is via the base64 length suffix on the data URL (unique
    per page render in our stubs).
    """
    import json
    client = MagicMock()
    call_counter = {"i": 0}
    # Yield one response per call in insertion order.
    ordered = list(page_responses.values())

    def create(**kwargs):
        idx = call_counter["i"]
        call_counter["i"] += 1
        payload = ordered[idx] if idx < len(ordered) else {}
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = json.dumps(payload)
        return resp

    client.chat.completions.create = MagicMock(side_effect=create)
    return client


def _stub_fitz_multi(n_pages: int):
    """fitz mock for ``n_pages`` pages — every load_page returns a real PNG."""
    fitz = MagicMock()
    doc = MagicMock()
    doc.page_count = n_pages

    def load_page(idx):
        page = MagicMock()
        pix = MagicMock()
        pix.tobytes.return_value = _real_png_bytes()
        page.get_pixmap.return_value = pix
        return page

    doc.load_page.side_effect = load_page
    fitz.open.return_value = doc
    return fitz


def test_vlm_finds_field_on_page_two(tmp_path):
    """Field absent on page 1 must be located on page 2 when budget allows."""
    pdf = tmp_path / "multi.pdf"; pdf.write_bytes(b"%PDF-1.4\n")
    pdfp = _stub_pdfplumber(["", ""])     # tier 1 returns blanks for both pages
    fitz = _stub_fitz_multi(2)
    tess = MagicMock(image_to_string=MagicMock(return_value=""))   # tier 2 also empty
    vlm = _multi_page_vlm_client({
        1: {"loan_number": {"value": None, "confidence": 0.0, "location_hint": ""}},
        2: {"loan_number": {"value": "0156312522", "confidence": 0.85,
                            "location_hint": "page 2 footer"}},
    })
    pipe = ExtractionPipeline(vlm_client=vlm, _pdfplumber=pdfp,
                              _fitz=fitz, _tesseract=tess)
    result = pipe.extract(pdf, [FieldSpec(name="loan_number", aliases=["Loan Number"])])

    fx = result.fields["loan_number"]
    assert fx.value == "0156312522"
    assert fx.method == "vlm"
    assert "page 2" in fx.location_hint
    # Two VLM calls — one per page.
    assert vlm.chat.completions.create.call_count == 2


def test_vlm_respects_max_pages_budget(tmp_path, monkeypatch):
    """A 10-page PDF with budget=3 must NOT scan past page 3 — field stays in HITL."""
    from config.settings import settings as s
    monkeypatch.setattr(s, "vlm_max_pages", 3)

    pdf = tmp_path / "long.pdf"; pdf.write_bytes(b"%PDF-1.4\n")
    pdfp = _stub_pdfplumber([""] * 10)
    fitz = _stub_fitz_multi(10)
    tess = MagicMock(image_to_string=MagicMock(return_value=""))
    # Field doesn't appear in any of the first 3 pages; it's on page 8 but
    # the budget cuts us off before we get there.
    vlm = _multi_page_vlm_client({
        i: {"loan_number": {"value": None, "confidence": 0.0}}
        for i in (1, 2, 3)
    })
    pipe = ExtractionPipeline(vlm_client=vlm, _pdfplumber=pdfp,
                              _fitz=fitz, _tesseract=tess)
    result = pipe.extract(pdf, [FieldSpec(name="loan_number", aliases=["Loan Number"])])

    fx = result.fields["loan_number"]
    assert fx.value is None
    assert fx.confidence == 0.0
    assert fx.hitl_required is True
    # Exactly 3 VLM calls — the budget.
    assert vlm.chat.completions.create.call_count == 3


def test_vlm_per_spec_pages_hint_skips_other_pages(tmp_path):
    """FieldSpec(pages=[3]) must scan ONLY page 3, not pages 1 or 2."""
    pdf = tmp_path / "doc.pdf"; pdf.write_bytes(b"%PDF-1.4\n")
    pdfp = _stub_pdfplumber([""] * 5)
    fitz = _stub_fitz_multi(5)
    tess = MagicMock(image_to_string=MagicMock(return_value=""))
    vlm = _multi_page_vlm_client({
        3: {"amount": {"value": "$10,640.58", "confidence": 0.95,
                       "location_hint": "page 3"}},
    })
    pipe = ExtractionPipeline(vlm_client=vlm, _pdfplumber=pdfp,
                              _fitz=fitz, _tesseract=tess)
    result = pipe.extract(pdf, [
        FieldSpec(name="amount", aliases=["Amount"], pages=[3], is_financial=True),
    ])

    fx = result.fields["amount"]
    assert fx.value == "$10,640.58"
    # Exactly ONE VLM call — only page 3 was sent.
    assert vlm.chat.completions.create.call_count == 1


def test_vlm_stops_calling_once_all_specs_found(tmp_path):
    """If page 1 satisfies every spec at sufficient confidence, page 2 is never sent."""
    pdf = tmp_path / "doc.pdf"; pdf.write_bytes(b"%PDF-1.4\n")
    pdfp = _stub_pdfplumber([""] * 3)
    fitz = _stub_fitz_multi(3)
    tess = MagicMock(image_to_string=MagicMock(return_value=""))
    vlm = _multi_page_vlm_client({
        1: {"status": {"value": "Open", "confidence": 0.95, "location_hint": "page 1"}},
    })
    pipe = ExtractionPipeline(vlm_client=vlm, _pdfplumber=pdfp,
                              _fitz=fitz, _tesseract=tess)
    result = pipe.extract(pdf, [FieldSpec(name="status", aliases=["Status"])])

    assert result.fields["status"].value == "Open"
    # Only one VLM call — page 1 satisfied everyone, so pages 2 and 3 skipped.
    assert vlm.chat.completions.create.call_count == 1


def test_vlm_page_order_prioritises_hinted_pages():
    """Page hints from any spec come first in the iteration order."""
    specs = [
        FieldSpec(name="a", pages=[4]),
        FieldSpec(name="b"),          # no hint
        FieldSpec(name="c", pages=[2]),
    ]
    order = ExtractionPipeline._vlm_page_order(specs, total_pages=6)
    # Hinted pages first (4 then 2), then 1, 3, 5 (filling budget of 5).
    assert order[:2] == [4, 2]
    # All hinted pages present in the head of the order.
    assert 4 in order and 2 in order
