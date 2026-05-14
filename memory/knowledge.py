"""Long-term knowledge store — UI patterns, error recoveries, task templates.

Two implementations behind one Protocol:

* ``ChromaKnowledgeStore`` — real vector DB; activated when ``chromadb`` is
  installed (poetry install --with phase4).
* ``NullKnowledgeStore`` — no-op fallback. Returns no cache hits and silently
  accepts writes. Lets every higher layer call ``knowledge.query_*`` and
  ``knowledge.store_*`` without checking whether the dep is present.

``get_knowledge_store()`` picks the right one at runtime. Test code can
inject either explicitly.

Boundary (CLAUDE.md):
* ``knowledge.py`` reads ChromaDB during a task.
* It writes ONLY after a task ends — never mid-task. ``store_*`` is
  buffered into a small in-memory list and flushed via ``flush()``.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from config.logging_config import get_logger
from config.settings import settings

log = get_logger(__name__)


# ── public types ────────────────────────────────────────────────────────────
@dataclass
class KnowledgeHit:
    id: str
    text: str
    metadata: dict
    distance: float


@dataclass
class PendingWrite:
    collection: str
    text: str
    metadata: dict
    id: str


# ── protocol ────────────────────────────────────────────────────────────────
@runtime_checkable
class KnowledgeStore(Protocol):
    def query_ui_pattern(self, app: str, element_desc: str,
                         k: int = 1) -> list[KnowledgeHit]: ...
    def query_error_recovery(self, error_text: str, app: str,
                             k: int = 1) -> list[KnowledgeHit]: ...
    def store_ui_pattern(self, app: str, element_desc: str,
                         selector: str, action_type: str) -> None: ...
    def store_error_recovery(self, error_pattern: str, app: str,
                             recovery_action: str, succeeded: bool) -> None: ...
    def flush(self) -> int: ...
    def reset(self) -> None: ...


# ── null fallback ───────────────────────────────────────────────────────────
class NullKnowledgeStore:
    """Used when chromadb is not installed — every call is a safe no-op."""

    def query_ui_pattern(self, app: str, element_desc: str,
                         k: int = 1) -> list[KnowledgeHit]:
        return []

    def query_error_recovery(self, error_text: str, app: str,
                             k: int = 1) -> list[KnowledgeHit]:
        return []

    def store_ui_pattern(self, app: str, element_desc: str,
                         selector: str, action_type: str) -> None:
        log.debug("knowledge_null_store_ui_pattern", app=app, desc=element_desc)

    def store_error_recovery(self, error_pattern: str, app: str,
                             recovery_action: str, succeeded: bool) -> None:
        log.debug("knowledge_null_store_error", app=app, succeeded=succeeded)

    def flush(self) -> int:
        return 0

    def reset(self) -> None:
        return


# ── chroma backend ──────────────────────────────────────────────────────────
class ChromaKnowledgeStore:
    """Persistent ChromaDB-backed implementation.

    Writes are buffered and flushed only at ``flush()``. Reads hit the DB
    directly — every task starts with the latest snapshot.
    """

    UI_COLLECTION = "ui_patterns"
    ERR_COLLECTION = "error_recoveries"
    TASK_COLLECTION = "task_templates"

    def __init__(self, path: str | None = None,
                 telemetry: bool = False,
                 client: Any | None = None) -> None:
        if client is not None:
            self._client = client
        else:
            import chromadb
            from chromadb.config import Settings as ChromaSettings
            self._client = chromadb.PersistentClient(
                path=path or settings.chroma_path,
                settings=ChromaSettings(anonymized_telemetry=telemetry),
            )
        self.ui = self._client.get_or_create_collection(self.UI_COLLECTION)
        self.err = self._client.get_or_create_collection(self.ERR_COLLECTION)
        self.task = self._client.get_or_create_collection(self.TASK_COLLECTION)
        self._pending: list[PendingWrite] = []

    # ── queries (mid-task) ──────────────────────────────────────────────────
    def query_ui_pattern(self, app: str, element_desc: str,
                         k: int = 1) -> list[KnowledgeHit]:
        return self._query(self.ui, query=element_desc, where={"app": app}, k=k)

    def query_error_recovery(self, error_text: str, app: str,
                             k: int = 1) -> list[KnowledgeHit]:
        return self._query(self.err, query=error_text, where={"app": app}, k=k)

    def _query(self, collection, query: str, where: dict | None, k: int) -> list[KnowledgeHit]:
        if collection.count() == 0:
            return []
        try:
            raw = collection.query(query_texts=[query], n_results=k, where=where or None)
        except Exception as e:  # noqa: BLE001 — never crash callers on a query failure
            log.warning("knowledge_query_failed", error=str(e), query=query[:80])
            return []
        ids = (raw.get("ids") or [[]])[0]
        docs = (raw.get("documents") or [[]])[0]
        metas = (raw.get("metadatas") or [[]])[0]
        dists = (raw.get("distances") or [[None] * len(ids)])[0]
        hits: list[KnowledgeHit] = []
        for i, doc_id in enumerate(ids):
            hits.append(KnowledgeHit(
                id=doc_id,
                text=docs[i] if i < len(docs) else "",
                metadata=metas[i] if i < len(metas) else {},
                distance=float(dists[i]) if i < len(dists) and dists[i] is not None else 0.0,
            ))
        return hits

    # ── stores (buffered) ───────────────────────────────────────────────────
    def store_ui_pattern(self, app: str, element_desc: str,
                         selector: str, action_type: str) -> None:
        write = PendingWrite(
            collection=self.UI_COLLECTION,
            text=element_desc,
            metadata={
                "app": app,
                "selector": selector,
                "action_type": action_type,
                "created_at": time.time(),
            },
            id=_stable_id(self.UI_COLLECTION, app, element_desc, selector),
        )
        self._pending.append(write)

    def store_error_recovery(self, error_pattern: str, app: str,
                             recovery_action: str, succeeded: bool) -> None:
        write = PendingWrite(
            collection=self.ERR_COLLECTION,
            text=error_pattern,
            metadata={
                "app": app,
                "recovery_action": recovery_action,
                "succeeded": int(bool(succeeded)),
                "created_at": time.time(),
            },
            id=_stable_id(self.ERR_COLLECTION, app, error_pattern, recovery_action),
        )
        self._pending.append(write)

    # ── lifecycle ───────────────────────────────────────────────────────────
    def flush(self) -> int:
        """Persist pending writes — called once per successful task."""
        if not self._pending:
            return 0
        # Group by collection so we issue one upsert per collection.
        bucket: dict[str, list[PendingWrite]] = {}
        for w in self._pending:
            bucket.setdefault(w.collection, []).append(w)

        written = 0
        for coll_name, writes in bucket.items():
            target = {
                self.UI_COLLECTION: self.ui,
                self.ERR_COLLECTION: self.err,
                self.TASK_COLLECTION: self.task,
            }[coll_name]
            try:
                target.upsert(
                    ids=[w.id for w in writes],
                    documents=[w.text for w in writes],
                    metadatas=[w.metadata for w in writes],
                )
                written += len(writes)
            except Exception as e:  # noqa: BLE001 — surface but don't crash flush()
                log.warning("knowledge_flush_failed", collection=coll_name,
                            count=len(writes), error=str(e))
        self._pending.clear()
        log.info("knowledge_flushed", count=written)
        return written

    def reset(self) -> None:
        """Forget everything — buffered + persisted. Used by tests."""
        self._pending.clear()
        for coll_name in (self.UI_COLLECTION, self.ERR_COLLECTION, self.TASK_COLLECTION):
            try:
                self._client.delete_collection(coll_name)
            except Exception:  # noqa: BLE001
                pass
        self.ui = self._client.get_or_create_collection(self.UI_COLLECTION)
        self.err = self._client.get_or_create_collection(self.ERR_COLLECTION)
        self.task = self._client.get_or_create_collection(self.TASK_COLLECTION)


# ── selection ──────────────────────────────────────────────────────────────
def get_knowledge_store(path: str | None = None) -> KnowledgeStore:
    """Return ChromaKnowledgeStore if chromadb installs cleanly, else Null."""
    try:
        import chromadb  # noqa: F401
    except ImportError:
        log.info("knowledge_null_store_selected",
                 reason="chromadb not installed (poetry install --with phase4)")
        return NullKnowledgeStore()
    try:
        return ChromaKnowledgeStore(path=path)
    except Exception as e:  # noqa: BLE001 — never block startup on a knowledge-store error
        log.warning("knowledge_chroma_init_failed", error=str(e))
        return NullKnowledgeStore()


def _stable_id(*parts: str) -> str:
    """SHA1 of the joined parts — deterministic + lets upsert deduplicate."""
    h = hashlib.sha1()
    h.update("||".join(parts).encode("utf-8"))
    return h.hexdigest()
