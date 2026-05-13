# Skill: Extraction Pipeline (PDF / OCR)

pdfplumber → PyMuPDF+Tesseract fallback → VLM fallback → HITL. Never skip a tier without trying the previous one first.

## Confidence Scoring

```python
CONFIDENCE_PDF_TEXT   = 0.95   # pdfplumber found selectable text — most reliable
CONFIDENCE_OCR_CLEAN  = 0.85   # Tesseract on clean scan (>300 DPI, low noise)
CONFIDENCE_OCR_NOISY  = 0.70   # Tesseract on low-res or skewed scan
CONFIDENCE_VLM        = 0.75   # VLM fallback — treat as general threshold
CONFIDENCE_FINANCIAL  = 0.90   # NON-NEGOTIABLE minimum for any financial field
```

Any extraction below its applicable threshold → HITL. No exceptions for financial fields.

## Three-Tier Pipeline

```python
from __future__ import annotations
import io
import time
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class ExtractionResult:
    text: str
    confidence: float
    method: str          # "pdfplumber" | "tesseract" | "vlm" | "hitl"
    ocr_used: bool
    fields: dict[str, str] = field(default_factory=dict)
    duration_ms: int = 0


def extract_pdf(pdf_bytes: bytes, task_id: str,
                financial_fields: list[str] | None = None) -> ExtractionResult:
    """Full pipeline: pdfplumber → Tesseract → VLM → HITL."""
    t0 = time.monotonic()

    # Tier 1: pdfplumber (selectable text)
    result = _try_pdfplumber(pdf_bytes)
    if result and result.confidence >= 0.90:
        result.duration_ms = int((time.monotonic() - t0) * 1000)
        return result

    # Tier 2: Tesseract OCR via PyMuPDF rasterisation
    result = _try_tesseract(pdf_bytes)
    if result and result.confidence >= 0.75:
        result.duration_ms = int((time.monotonic() - t0) * 1000)
        return result

    # Tier 3: VLM (screenshot of page passed to LLM)
    result = _try_vlm(pdf_bytes, task_id)
    if result and result.confidence >= 0.75:
        result.duration_ms = int((time.monotonic() - t0) * 1000)
        return result

    # All tiers below threshold — route to HITL
    return ExtractionResult(
        text="", confidence=0.0, method="hitl",
        ocr_used=True,
        duration_ms=int((time.monotonic() - t0) * 1000),
    )
```

## Tier 1 — pdfplumber

```python
import pdfplumber


def _try_pdfplumber(pdf_bytes: bytes) -> ExtractionResult | None:
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages_text = []
            for page in pdf.pages:
                text = page.extract_text() or ""
                pages_text.append(text)
            full_text = "\n".join(pages_text).strip()

        if not full_text:
            return None   # no selectable text — fall through to OCR

        # Heuristic: sparse text = likely scanned
        char_density = len(full_text) / max(len(pdf_bytes), 1)
        confidence = 0.95 if char_density > 0.005 else 0.80
        return ExtractionResult(
            text=full_text, confidence=confidence,
            method="pdfplumber", ocr_used=False,
        )
    except Exception:
        return None
```

## Tier 2 — PyMuPDF + Tesseract

```python
import fitz          # PyMuPDF
import pytesseract
from PIL import Image
import numpy as np


def _try_tesseract(pdf_bytes: bytes) -> ExtractionResult | None:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        all_text: list[str] = []
        total_conf: list[float] = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            # 300 DPI render — minimum for reliable OCR
            mat = fitz.Matrix(300 / 72, 300 / 72)
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
            img = Image.frombytes("L", [pix.width, pix.height], pix.samples)

            # Preprocessing: threshold for cleaner OCR
            img_array = np.array(img)
            _, thresh = cv2_threshold(img_array)   # see below

            data = pytesseract.image_to_data(
                thresh, output_type=pytesseract.Output.DICT,
                config="--psm 6",   # assume single uniform block
            )
            words = [w for w, c in zip(data["text"], data["conf"]) if int(c) > 0 and w.strip()]
            confs = [int(c) / 100 for c in data["conf"] if int(c) > 0]
            all_text.append(" ".join(words))
            if confs:
                total_conf.append(sum(confs) / len(confs))

        doc.close()
        full_text = "\n".join(all_text).strip()
        if not full_text:
            return None

        avg_conf = sum(total_conf) / len(total_conf) if total_conf else 0.0
        # Map Tesseract 0-1 confidence to pipeline thresholds
        pipeline_conf = 0.85 if avg_conf >= 0.80 else (0.70 if avg_conf >= 0.60 else 0.50)
        return ExtractionResult(
            text=full_text, confidence=pipeline_conf,
            method="tesseract", ocr_used=True,
        )
    except Exception:
        return None


def cv2_threshold(img_array):
    """Simple Otsu threshold — avoids cv2 import; use when Pillow is available."""
    try:
        import cv2
        _, thresh = cv2.threshold(img_array, 0, 255,
                                   cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return None, Image.fromarray(thresh)
    except ImportError:
        # Fallback: PIL point threshold
        img = Image.fromarray(img_array)
        return None, img.point(lambda p: 255 if p > 128 else 0)
```

## Tier 3 — VLM Fallback

```python
from openai import OpenAI
import base64
from config.settings import settings


def _try_vlm(pdf_bytes: bytes, task_id: str) -> ExtractionResult | None:
    """Render first page → base64 → VLM prompt."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[0]
        mat = fitz.Matrix(150 / 72, 150 / 72)   # 150 DPI for VLM (lower = faster)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")
        doc.close()

        b64 = base64.b64encode(img_bytes).decode()
        client = OpenAI(base_url=settings.inference_url, api_key="ignored")

        resp = client.chat.completions.create(
            model=settings.model_name,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text",
                     "text": (
                         "Extract all text from this document image. "
                         "Return JSON: {\"text\": \"<full text>\", \"confidence\": 0.0-1.0}. "
                         "confidence = your certainty the text is complete and accurate."
                     )},
                ],
            }],
            max_tokens=2048,
        )

        import json
        data = json.loads(resp.choices[0].message.content)
        return ExtractionResult(
            text=data.get("text", ""),
            confidence=float(data.get("confidence", 0.70)),
            method="vlm", ocr_used=True,
        )
    except Exception:
        return None
```

## Field Extraction (Structured)

After raw text extraction, parse structured fields using regex + VLM:

```python
import re


FIELD_PATTERNS: dict[str, list[str]] = {
    "claim_id":    [r"Claim\s*(?:ID|#|Number)[:\s]+([A-Z0-9\-]+)", r"\bCLM-\d+\b"],
    "policy_no":   [r"Policy\s*(?:No|Number|#)[:\s]+([A-Z0-9\-]+)"],
    "name":        [r"(?:Insured|Claimant|Name)[:\s]+([A-Z][a-z]+(?: [A-Z][a-z]+)+)"],
    "dob":         [r"(?:DOB|Date of Birth)[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})"],
    "amount":      [r"\$\s*([\d,]+\.?\d*)", r"(?:Amount|Total)[:\s]+\$?([\d,]+\.?\d*)"],
    "address":     [r"(?:Address)[:\s]+(.+?)(?:\n|$)"],
}


def parse_fields(text: str, required_fields: list[str]) -> dict[str, dict]:
    """
    Returns {field_name: {"value": str, "confidence": float}}.
    Financial fields (amount) require 0.90 confidence minimum.
    """
    results: dict[str, dict] = {}
    for field_name in required_fields:
        patterns = FIELD_PATTERNS.get(field_name, [])
        matched = None
        for pattern in patterns:
            m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if m:
                matched = m.group(1).strip()
                break

        is_financial = field_name in ("amount", "premium", "deductible", "payment")
        confidence = 0.90 if matched else 0.0
        min_required = 0.90 if is_financial else 0.75

        results[field_name] = {
            "value": matched or "",
            "confidence": confidence,
            "requires_hitl": confidence < min_required,
            "is_financial": is_financial,
        }
    return results
```

## Preprocessing Checklist

Before passing image to Tesseract:

1. **Deskew** — detect and correct rotation >0.5°
2. **Denoise** — median blur for scanned fax/copies
3. **Threshold** — Otsu binarisation (black/white only)
4. **DPI normalise** — resample to 300 DPI if below
5. **Despeckle** — remove isolated single-pixel noise

```python
def preprocess_for_ocr(img: Image.Image) -> Image.Image:
    """Minimal preprocessing pipeline — add steps as needed for specific doc types."""
    # Convert to grayscale
    if img.mode != "L":
        img = img.convert("L")
    # Resize to 300 DPI equivalent if smaller
    if img.width < 2000:
        scale = 2000 / img.width
        img = img.resize((int(img.width * scale), int(img.height * scale)),
                         Image.LANCZOS)
    return img
```

## Audit Log Entry

Every extraction writes an audit record:

```python
def audit_extraction(session, task_id: str, result: ExtractionResult,
                     fields: dict) -> None:
    session.execute(
        """INSERT INTO extractions
           (task_id, method, confidence, ocr_used, field_count,
            requires_hitl, duration_ms, ts)
           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (
            task_id, result.method, result.confidence,
            int(result.ocr_used), len(fields),
            int(any(f["requires_hitl"] for f in fields.values())),
            result.duration_ms,
        ),
    )
    session.commit()
```

## What ExtractionPipeline Does NOT Do

- Download PDFs — browser.py fetches via `page.request.get()` and passes bytes here
- Display results in the UI — caller writes to WorkingMemory and overlay
- Validate field values against IIM — that's the cross-verify stage (browser.py)
