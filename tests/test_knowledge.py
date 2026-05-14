"""Tests for KnowledgeStore — Null fallback always works, Chroma path uses
in-memory fakes to avoid the optional dependency.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from memory.knowledge import (
    ChromaKnowledgeStore, KnowledgeHit, NullKnowledgeStore,
    get_knowledge_store, _stable_id,
)


# ── NullKnowledgeStore ──────────────────────────────────────────────────────
def test_null_store_returns_empty_lists():
    store = NullKnowledgeStore()
    assert store.query_ui_pattern("ld", "Sign in") == []
    assert store.query_error_recovery("session expired", "ld") == []


def test_null_store_writes_are_silent():
    store = NullKnowledgeStore()
    store.store_ui_pattern("ld", "Sign in", "[data-testid='submit-btn']", "click")
    store.store_error_recovery("session expired", "ld", "click_dismiss", True)
    assert store.flush() == 0


# ── ChromaKnowledgeStore (in-memory fake client) ────────────────────────────
def _fake_chroma_client():
    """Return a MagicMock that mimics the ``chromadb`` client surface."""
    client = MagicMock()
    collections: dict[str, MagicMock] = {}

    def get_or_create_collection(name):
        if name not in collections:
            coll = MagicMock()
            coll.name = name
            coll._docs = []          # list of (id, text, meta)
            coll.count = lambda c=coll: len(c._docs)

            def upsert(ids, documents, metadatas, c=coll):
                # Replace any existing rows by id, then append the rest.
                kept = [(i, d, m) for (i, d, m) in c._docs if i not in ids]
                kept.extend(zip(ids, documents, metadatas))
                c._docs = kept

            def query(query_texts, n_results, where=None, c=coll):
                # Substring search — close enough for unit tests.
                q = (query_texts or [""])[0].lower()
                matches = [(i, d, m) for (i, d, m) in c._docs
                           if q in d.lower()
                           and (not where or all(m.get(k) == v for k, v in where.items()))]
                matches = matches[:n_results]
                return {
                    "ids": [[i for (i, _, _) in matches]],
                    "documents": [[d for (_, d, _) in matches]],
                    "metadatas": [[m for (_, _, m) in matches]],
                    "distances": [[0.1 for _ in matches]],
                }

            coll.upsert = MagicMock(side_effect=upsert)
            coll.query = MagicMock(side_effect=query)
            coll.delete_collection = MagicMock()
            collections[name] = coll
        return collections[name]

    def delete_collection(name):
        collections.pop(name, None)

    client.get_or_create_collection = get_or_create_collection
    client.delete_collection = delete_collection
    return client, collections


def test_chroma_store_writes_only_on_flush():
    client, _ = _fake_chroma_client()
    store = ChromaKnowledgeStore(client=client)
    store.store_ui_pattern("ld", "Sign in", "[data-testid='submit-btn']", "click")
    store.store_ui_pattern("ld", "Username", "[data-testid='user-input']", "type")
    # Nothing persisted yet (Phase-4 mid-task contract).
    assert store.ui.count() == 0
    n = store.flush()
    assert n == 2
    assert store.ui.count() == 2


def test_chroma_store_round_trip_query():
    client, _ = _fake_chroma_client()
    store = ChromaKnowledgeStore(client=client)
    store.store_ui_pattern("ld", "Sign in button", "[data-testid='submit-btn']", "click")
    store.flush()
    hits = store.query_ui_pattern("ld", "Sign in button")
    assert len(hits) == 1
    assert isinstance(hits[0], KnowledgeHit)
    assert hits[0].metadata["selector"] == "[data-testid='submit-btn']"


def test_chroma_where_clause_filters_by_app():
    client, _ = _fake_chroma_client()
    store = ChromaKnowledgeStore(client=client)
    store.store_ui_pattern("ld", "Submit", "[testid=ld-submit]", "click")
    store.store_ui_pattern("iim", "Submit", "[testid=iim-submit]", "click")
    store.flush()
    hits = store.query_ui_pattern("iim", "Submit")
    assert len(hits) == 1
    assert hits[0].metadata["app"] == "iim"


def test_chroma_query_returns_empty_on_zero_count():
    client, _ = _fake_chroma_client()
    store = ChromaKnowledgeStore(client=client)
    assert store.query_ui_pattern("ld", "anything") == []


def test_chroma_query_failure_returns_empty_not_raise():
    client, _ = _fake_chroma_client()
    store = ChromaKnowledgeStore(client=client)
    store.store_ui_pattern("ld", "X", "[testid=x]", "click"); store.flush()
    store.ui.query = MagicMock(side_effect=RuntimeError("boom"))
    assert store.query_ui_pattern("ld", "X") == []


def test_chroma_reset_clears_everything():
    client, _ = _fake_chroma_client()
    store = ChromaKnowledgeStore(client=client)
    store.store_ui_pattern("ld", "X", "[testid=x]", "click")
    store.flush()
    store.reset()
    assert store.ui.count() == 0


def test_stable_id_is_deterministic():
    a = _stable_id("ui", "ld", "sign in", "[testid=submit]")
    b = _stable_id("ui", "ld", "sign in", "[testid=submit]")
    c = _stable_id("ui", "ld", "sign in", "[testid=other]")
    assert a == b
    assert a != c


# ── selector ────────────────────────────────────────────────────────────────
def test_get_knowledge_store_falls_back_to_null_without_chromadb(monkeypatch):
    import builtins
    original = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "chromadb":
            raise ImportError("simulated absence")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    store = get_knowledge_store()
    assert isinstance(store, NullKnowledgeStore)
