"""Document extraction pipeline — text PDFs → scanned PDFs → VLM.

Three tiers, tried in order. Each tier returns ``ExtractionResult`` with a
per-field confidence. If a field is still below
``settings.confidence_threshold`` after tier 3, it stays in the result with
its best-effort value plus ``hitl_required=True`` so the caller can decide
to route to a human reviewer (CLAUDE.md non-negotiable: financial fields
need ``settings.financial_confidence_threshold`` instead).

```
tier 1: pdfplumber.extract_text + regex/keyword anchor → high confidence
tier 2: PyMuPDF render + pytesseract OCR + regex     → medium confidence
tier 3: local VLM with image of the page             → lower confidence
```

Tier 1 succeeds → tiers 2 and 3 are skipped. Per-field success is independent
so a partially extractable document still gets its high-confidence fields
from tier 1.
"""
from __future__ import annotations

import io
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from agent.llm_client import get_client, strip_json_fence
from config.logging_config import get_logger
from config.settings import settings

log = get_logger(__name__)


# ── result schema ───────────────────────────────────────────────────────────
class FieldExtraction(BaseModel):
    """One field's extracted value with provenance."""
    value: str | None = None
    confidence: float = 0.0
    method: str = ""          # "pdfplumber" | "ocr" | "vlm" | "none"
    location_hint: str = ""   # page number / region descriptor
    hitl_required: bool = False
    is_financial: bool = False

    @field_validator("confidence")
    @classmethod
    def _conf_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"confidence must be in [0,1], got {v}")
        return v


class ExtractionResult(BaseModel):
    document: str
    fields: dict[str, FieldExtraction] = Field(default_factory=dict)
    pages: int = 0
    tiers_used: list[str] = Field(default_factory=list)   # ["pdfplumber"], or ["pdfplumber","ocr"]
    duration_ms: int = 0
    error: str | None = None


# ── field spec ──────────────────────────────────────────────────────────────
@dataclass
class FieldSpec:
    """How to find one logical field across the three tiers.

    * ``name`` is the canonical key in the result (e.g. ``"loan_number"``).
    * ``aliases`` are keywords / labels the field appears next to in text
      (e.g. ``["Loan #", "Loan Number"]``).
    * ``pattern`` is an optional regex applied to the *whole page* — used when
      the value has a stable shape (e.g. amounts, dates, claim ids). If both
      ``aliases`` and ``pattern`` are present, the matcher looks for the
      pattern *near* an alias (line-windowed).
    * ``is_financial`` raises the confidence bar for HITL gating.
    """
    name: str
    aliases: list[str] = field(default_factory=list)
    pattern: str | None = None
    is_financial: bool = False
    min_confidence: float | None = None  # override; defaults to settings.confidence_threshold


# ── pipeline ────────────────────────────────────────────────────────────────
class ExtractionPipeline:
    """Three-tier extractor: pdfplumber → OCR → VLM."""

    OCR_DPI = 300

    def __init__(self,
                 vlm_client: Any | None = None,
                 _pdfplumber: Any | None = None,
                 _fitz: Any | None = None,
                 _tesseract: Any | None = None) -> None:
        self._vlm_client = vlm_client
        # Injectable for tests; real deps imported lazily.
        self._pdfplumber = _pdfplumber
        self._fitz = _fitz
        self._tesseract = _tesseract

    @property
    def vlm_client(self) -> Any:
        if self._vlm_client is None:
            self._vlm_client = get_client()
        return self._vlm_client

    # ── public entry point ──────────────────────────────────────────────────
    def extract(self, document: str | Path,
                fields: list[FieldSpec] | list[dict] | list[str]) -> ExtractionResult:
        start = time.monotonic()
        path = Path(document)
        if not path.exists():
            return ExtractionResult(
                document=str(document), error=f"file_not_found: {document}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        specs = self._normalise_fields(fields)
        result = ExtractionResult(document=str(path))

        # Tier 1 — pdfplumber native text.
        try:
            self._tier_pdfplumber(path, specs, result)
        except Exception as e:  # noqa: BLE001 — log and continue to next tier
            log.warning("extraction_tier_failed", tier="pdfplumber", error=str(e))

        remaining = self._remaining(specs, result)
        # Tier 2 — OCR.
        if remaining:
            try:
                self._tier_ocr(path, remaining, result)
            except Exception as e:  # noqa: BLE001
                log.warning("extraction_tier_failed", tier="ocr", error=str(e))
            remaining = self._remaining(specs, result)

        # Tier 3 — VLM.
        if remaining:
            try:
                self._tier_vlm(path, remaining, result)
            except Exception as e:  # noqa: BLE001
                log.warning("extraction_tier_failed", tier="vlm", error=str(e))

        # Final pass — apply confidence-threshold and financial-gate rules.
        self._apply_hitl_gates(specs, result)

        result.duration_ms = int((time.monotonic() - start) * 1000)
        log.info("extraction_complete",
                 document=str(path),
                 tiers=result.tiers_used,
                 fields={k: v.confidence for k, v in result.fields.items()},
                 duration_ms=result.duration_ms)
        return result

    # ── tier 1: pdfplumber ──────────────────────────────────────────────────
    def _tier_pdfplumber(self, path: Path, specs: list[FieldSpec],
                         result: ExtractionResult) -> None:
        pdfplumber = self._pdfplumber or self._import_pdfplumber()
        with pdfplumber.open(str(path)) as pdf:
            result.pages = len(pdf.pages)
            full_text_by_page = []
            for i, page in enumerate(pdf.pages):
                full_text_by_page.append((i + 1, page.extract_text() or ""))

        if any(text.strip() for _, text in full_text_by_page):
            result.tiers_used.append("pdfplumber")

        for spec in specs:
            value, page_no = self._scan_text_pages(full_text_by_page, spec)
            if value is not None:
                result.fields[spec.name] = FieldExtraction(
                    value=value, confidence=0.92, method="pdfplumber",
                    location_hint=f"page {page_no}",
                    is_financial=spec.is_financial,
                )

    # ── tier 2: OCR ─────────────────────────────────────────────────────────
    def _tier_ocr(self, path: Path, specs: list[FieldSpec],
                  result: ExtractionResult) -> None:
        fitz = self._fitz or self._import_fitz()
        pytesseract = self._tesseract or self._import_tesseract()
        from PIL import Image

        doc = fitz.open(str(path))
        result.pages = max(result.pages, doc.page_count)
        result.tiers_used.append("ocr")
        text_by_page: list[tuple[int, str]] = []
        try:
            for i in range(doc.page_count):
                page = doc.load_page(i)
                pix = page.get_pixmap(dpi=self.OCR_DPI)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                text = pytesseract.image_to_string(img) or ""
                text_by_page.append((i + 1, text))
        finally:
            doc.close()

        for spec in specs:
            value, page_no = self._scan_text_pages(text_by_page, spec)
            if value is not None:
                result.fields[spec.name] = FieldExtraction(
                    value=value, confidence=0.78, method="ocr",
                    location_hint=f"page {page_no} (OCR)",
                    is_financial=spec.is_financial,
                )

    # ── tier 3: VLM ─────────────────────────────────────────────────────────
    def _tier_vlm(self, path: Path, specs: list[FieldSpec],
                  result: ExtractionResult) -> None:
        import base64
        fitz = self._fitz or self._import_fitz()
        result.tiers_used.append("vlm")
        doc = fitz.open(str(path))
        try:
            # VLMs are token-expensive — only send page 1 unless a spec asks
            # for "page_2" / "page_3" in its alias hints (rarely needed).
            page = doc.load_page(0)
            pix = page.get_pixmap(dpi=200)
            b64 = base64.b64encode(pix.tobytes("png")).decode()
        finally:
            doc.close()

        field_list = ", ".join(s.name for s in specs)
        prompt = _VLM_PROMPT.format(fields=field_list)
        resp = self.vlm_client.chat.completions.create(
            model=settings.model_name,
            messages=[{"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": prompt},
            ]}],
            max_tokens=1024,
            temperature=0.0,
        )
        import json
        raw = strip_json_fence(resp.choices[0].message.content or "")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            log.warning("vlm_extraction_parse_failed", error=str(e), raw=raw[:200])
            return
        for spec in specs:
            entry = data.get(spec.name) or {}
            val = entry.get("value")
            if val in (None, "", "null"):
                continue
            conf = float(entry.get("confidence", 0.6))
            result.fields[spec.name] = FieldExtraction(
                value=str(val),
                confidence=max(0.0, min(1.0, conf)),
                method="vlm",
                location_hint=str(entry.get("location_hint", "page 1")),
                is_financial=spec.is_financial,
            )

    # ── helpers ─────────────────────────────────────────────────────────────
    @staticmethod
    def _normalise_fields(fields) -> list[FieldSpec]:
        out: list[FieldSpec] = []
        for f in fields:
            if isinstance(f, FieldSpec):
                out.append(f)
            elif isinstance(f, str):
                out.append(FieldSpec(name=f, aliases=[f]))
            elif isinstance(f, dict):
                out.append(FieldSpec(**f))
            else:
                raise TypeError(f"unsupported field spec: {type(f).__name__}")
        return out

    @staticmethod
    def _remaining(specs: list[FieldSpec], result: ExtractionResult) -> list[FieldSpec]:
        return [s for s in specs
                if s.name not in result.fields
                or result.fields[s.name].confidence < (s.min_confidence
                                                       or settings.confidence_threshold)]

    @staticmethod
    def _scan_text_pages(pages: list[tuple[int, str]],
                         spec: FieldSpec) -> tuple[str | None, int | None]:
        """Find a value for ``spec`` in ``pages``. Returns (value, page_no)."""
        compiled = re.compile(spec.pattern) if spec.pattern else None

        for page_no, text in pages:
            if compiled and not spec.aliases:
                # Pattern-only: first match anywhere wins.
                m = compiled.search(text)
                if m:
                    return _pick_match(m), page_no
                continue

            for alias in spec.aliases or []:
                # Match line containing alias, then take the rest of the line
                # after the alias as candidate value (or the next non-blank line).
                for i, line in enumerate(text.splitlines()):
                    if alias.lower() not in line.lower():
                        continue
                    candidate = _value_after_alias(line, alias, text.splitlines(), i)
                    if not candidate:
                        continue
                    if compiled:
                        m = compiled.search(candidate)
                        if m:
                            return _pick_match(m), page_no
                    else:
                        return candidate.strip(), page_no
        return None, None

    def _apply_hitl_gates(self, specs: list[FieldSpec],
                          result: ExtractionResult) -> None:
        for spec in specs:
            fx = result.fields.get(spec.name)
            if fx is None:
                # Field never found — emit a 0-confidence stub so the caller
                # has something concrete to route to HITL.
                result.fields[spec.name] = FieldExtraction(
                    value=None, confidence=0.0, method="none",
                    is_financial=spec.is_financial, hitl_required=True,
                )
                continue
            threshold = (settings.financial_confidence_threshold
                         if spec.is_financial
                         else (spec.min_confidence or settings.confidence_threshold))
            if fx.confidence < threshold:
                fx.hitl_required = True

    # ── lazy imports ────────────────────────────────────────────────────────
    @staticmethod
    def _import_pdfplumber():
        try:
            import pdfplumber
        except ImportError as e:
            raise RuntimeError(
                "pdfplumber is required for Phase 4 extraction. "
                "Install with: poetry install"
            ) from e
        return pdfplumber

    @staticmethod
    def _import_fitz():
        try:
            import fitz  # PyMuPDF
        except ImportError as e:
            raise RuntimeError(
                "PyMuPDF (fitz) is required for OCR/VLM tiers. "
                "Install with: poetry install"
            ) from e
        return fitz

    @staticmethod
    def _import_tesseract():
        try:
            import pytesseract
        except ImportError as e:
            raise RuntimeError(
                "pytesseract is required for OCR. "
                "Install the binary too: brew install tesseract  /  apt-get install tesseract-ocr"
            ) from e
        return pytesseract


# ── helpers used by the scanner ─────────────────────────────────────────────
_AMOUNT_RE = re.compile(r"\$?\s*-?[\d,]+\.\d{2}")
_DATE_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}")


def _pick_match(m: re.Match) -> str:
    """Return the first non-empty captured group, else the whole match."""
    for g in m.groups():
        if g:
            return g.strip()
    return m.group(0).strip()


def _value_after_alias(line: str, alias: str,
                       all_lines: list[str], i: int) -> str:
    """Extract the text following an alias on the same line; fall back to next line."""
    lower = line.lower()
    idx = lower.find(alias.lower())
    after = line[idx + len(alias):].lstrip(" :\t-")
    if after.strip():
        return after.strip()
    # Try the next non-blank line.
    for j in range(i + 1, min(i + 3, len(all_lines))):
        nxt = all_lines[j].strip()
        if nxt:
            return nxt
    return ""


_VLM_PROMPT = """Extract the following fields from this document image.
Return ONLY a valid JSON object.

Fields to extract: {fields}

For each field return an object:
{{
  "<field_name>": {{
    "value": "<exact text from the document or null if not visible>",
    "confidence": <float 0..1>,
    "location_hint": "<where on the page you saw it>"
  }}
}}

Rules:
- If a field is not visible, return value=null, confidence=0.0.
- For amounts: include currency symbol and commas as shown (e.g. "$10,640.58").
- For dates: return as shown (e.g. "04/17/2026").
- For loan/claim numbers: include every digit exactly as shown.
- NEVER invent or guess values — extract what is clearly visible.
"""
