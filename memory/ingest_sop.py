"""SOP ingest CLI — populates the ``sop_chunks`` Chroma collection.

Usage:
    poetry run python -m memory.ingest_sop docs/sop/
    poetry run python -m memory.ingest_sop /path/to/file.pdf
    poetry run python -m memory.ingest_sop docs/sop/ --reset

Idempotent: chunk IDs are content-hashed, so re-running on the same files
upserts identical records. ``--reset`` drops the collection first.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config.logging_config import configure_logging, get_logger
from memory.knowledge import get_knowledge_store
from memory.sop_loader import load_directory, load_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest SOPs into ChromaDB")
    parser.add_argument("path", help="File or directory containing SOPs")
    parser.add_argument("--reset", action="store_true",
                        help="Drop the SOP collection before ingesting")
    parser.add_argument("--chroma-path", default=None,
                        help="Override settings.chroma_path")
    args = parser.parse_args(argv)

    configure_logging("ingest_sop")
    log = get_logger(__name__)

    target = Path(args.path)
    if not target.exists():
        log.error("path_not_found", path=str(target))
        return 1

    chunks = load_file(target) if target.is_file() else load_directory(target)
    if not chunks:
        log.warning("no_chunks_loaded", path=str(target))
        return 0

    store = get_knowledge_store(path=args.chroma_path)

    if args.reset and hasattr(store, "sop"):
        # Drop and recreate so re-ingest is clean.
        log.warning("sop_collection_reset")
        store._client.delete_collection(store.SOP_COLLECTION)  # type: ignore[attr-defined]
        store.sop = store._client.get_or_create_collection(  # type: ignore[attr-defined]
            store.SOP_COLLECTION)

    written = store.upsert_sop_chunks(chunks)
    log.info("sop_ingest_complete",
             chunks=len(chunks),
             written=written,
             store=type(store).__name__)
    return 0 if written or not chunks else 2


if __name__ == "__main__":
    sys.exit(main())
