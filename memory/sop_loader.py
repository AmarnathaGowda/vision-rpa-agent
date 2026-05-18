"""SOP loader — read text / markdown / PDF / DOCX files into chunked records.

Public API:
    load_directory(path: str | Path) -> list[SOPChunk]
    load_file(path: Path) -> list[SOPChunk]

Each ``SOPChunk`` has ``text``, ``metadata`` (source, section, mtime, etc.)
and a stable ``id`` (sha256 of source+offset) so re-ingesting an unchanged
file is a no-op via upsert.

Format support:
- .txt / .md             — read as UTF-8
- .pdf                   — pdfplumber (already a project dep)
- .docx                  — python-docx (optional; warned and skipped if absent)
- everything else        — skipped with a log warning

Chunking is character-based at ~3 200 chars (~800 tokens) with ~400 char
overlap, splitting on paragraph boundaries when possible. This is a
deliberate simplification — tiktoken-accurate token counting is unnecessary
for a retrieval-only use case where every chunk gets re-ranked by cosine
distance anyway.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from config.logging_config import get_logger

log = get_logger(__name__)

# ── tunables ─────────────────────────────────────────────────────────────
CHUNK_CHARS = 3200       # ≈ 800 tokens
CHUNK_OVERLAP = 400      # ≈ 100 tokens
SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf", ".docx"}


@dataclass
class SOPChunk:
    id: str
    text: str
    metadata: dict


# ── public API ───────────────────────────────────────────────────────────
def load_directory(path: str | Path) -> list[SOPChunk]:
    """Walk ``path`` recursively and return every chunk from every supported file."""
    root = Path(path)
    if not root.exists():
        raise FileNotFoundError(f"SOP directory not found: {root}")
    out: list[SOPChunk] = []
    for file_path in sorted(root.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        try:
            out.extend(load_file(file_path))
        except Exception as e:  # noqa: BLE001 — one bad file shouldn't kill ingest
            log.warning("sop_load_failed", file=str(file_path), error=str(e))
    return out


def load_file(path: Path) -> list[SOPChunk]:
    """Dispatch to the right reader based on suffix."""
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md"):
        text = path.read_text(encoding="utf-8")
    elif suffix == ".pdf":
        text = _read_pdf(path)
    elif suffix == ".docx":
        text = _read_docx(path)
    else:
        log.warning("sop_unsupported_suffix", file=str(path), suffix=suffix)
        return []

    if not text.strip():
        log.info("sop_empty_file", file=str(path))
        return []

    mtime = path.stat().st_mtime
    return list(_chunk(text, source=str(path), mtime=mtime))


# ── format readers ───────────────────────────────────────────────────────
def _read_pdf(path: Path) -> str:
    import pdfplumber
    parts: list[str] = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            txt = page.extract_text() or ""
            if txt:
                parts.append(f"[page {i}]\n{txt}")
    return "\n\n".join(parts)


def _read_docx(path: Path) -> str:
    try:
        import docx  # python-docx
    except ImportError:
        log.warning("docx_not_installed",
                    file=str(path),
                    hint="poetry add python-docx (optional dep)")
        return ""
    doc = docx.Document(path)
    return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())


# ── chunker ──────────────────────────────────────────────────────────────
def _chunk(text: str, *, source: str, mtime: float):
    """Yield SOPChunks of ~CHUNK_CHARS with CHUNK_OVERLAP overlap.

    Preference order for split boundaries:
      1. Double newline (paragraph)
      2. Single newline
      3. Hard cut at CHUNK_CHARS (last resort)
    """
    text = text.strip()
    if not text:
        return
    if len(text) <= CHUNK_CHARS:
        yield _make_chunk(text, source=source, offset=0, mtime=mtime)
        return

    start = 0
    while start < len(text):
        end = min(start + CHUNK_CHARS, len(text))
        if end < len(text):
            # Walk back to nearest paragraph or line break inside the window.
            window = text[start:end]
            cut = window.rfind("\n\n")
            if cut == -1 or cut < CHUNK_CHARS // 2:
                cut = window.rfind("\n")
            if cut > CHUNK_CHARS // 2:
                end = start + cut

        chunk_text = text[start:end].strip()
        if chunk_text:
            yield _make_chunk(chunk_text, source=source, offset=start, mtime=mtime)

        if end >= len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)


def _make_chunk(text: str, *, source: str, offset: int, mtime: float) -> SOPChunk:
    raw_id = f"{source}:{offset}:{hashlib.sha256(text.encode()).hexdigest()[:16]}"
    return SOPChunk(
        id=hashlib.sha256(raw_id.encode()).hexdigest()[:32],
        text=text,
        metadata={
            "source": source,
            "offset": offset,
            "mtime": mtime,
            "length": len(text),
        },
    )
