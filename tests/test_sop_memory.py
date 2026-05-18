"""SOP memory: loader → ingest → retrieval → planner injection."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from memory.knowledge import KnowledgeHit, NullKnowledgeStore
from memory.sop_loader import (
    CHUNK_CHARS,
    SOPChunk,
    load_directory,
    load_file,
)


# ── loader ───────────────────────────────────────────────────────────────
def test_loader_reads_text_file(tmp_path):
    f = tmp_path / "claim.md"
    f.write_text("# Loss Draft\n\nApprove < $5000 silently.", encoding="utf-8")
    chunks = load_file(f)
    assert len(chunks) == 1
    assert "Loss Draft" in chunks[0].text
    assert chunks[0].metadata["source"].endswith("claim.md")
    assert isinstance(chunks[0].id, str) and len(chunks[0].id) == 32


def test_loader_chunks_large_file_with_overlap(tmp_path):
    f = tmp_path / "big.md"
    body = ("paragraph one.\n\n" + ("x " * 50) + "\n\n") * 200  # ~22 000 chars
    f.write_text(body, encoding="utf-8")
    chunks = load_file(f)
    assert len(chunks) >= 5
    # Each chunk ≤ CHUNK_CHARS.
    assert all(len(c.text) <= CHUNK_CHARS for c in chunks)
    # IDs are unique.
    assert len({c.id for c in chunks}) == len(chunks)


def test_loader_skips_unsupported_files(tmp_path):
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    (tmp_path / "note.md").write_text("hello", encoding="utf-8")
    chunks = load_directory(tmp_path)
    sources = [c.metadata["source"] for c in chunks]
    assert any(s.endswith("note.md") for s in sources)
    assert not any(s.endswith("image.png") for s in sources)


def test_loader_directory_skips_empty_files(tmp_path):
    (tmp_path / "empty.md").write_text("", encoding="utf-8")
    (tmp_path / "real.md").write_text("real content", encoding="utf-8")
    chunks = load_directory(tmp_path)
    sources = [c.metadata["source"] for c in chunks]
    assert any(s.endswith("real.md") for s in sources)
    assert not any(s.endswith("empty.md") for s in sources)


# ── knowledge store ──────────────────────────────────────────────────────
def test_null_store_returns_empty_sop():
    store = NullKnowledgeStore()
    assert store.query_sop("anything") == []
    assert store.upsert_sop_chunks([SOPChunk(id="x", text="y", metadata={})]) == 0


def test_chroma_store_upsert_then_query_sop():
    """End-to-end on a real in-memory Chroma client — verifies the SOP
    collection wires correctly and a basic similarity query works."""
    chromadb = pytest.importorskip("chromadb")
    from memory.knowledge import ChromaKnowledgeStore

    client = chromadb.EphemeralClient()  # in-memory, no disk
    store = ChromaKnowledgeStore(client=client)

    chunks = [
        SOPChunk(id="c1", text="Approve loss-draft claims under $5000 silently.",
                 metadata={"source": "lossdraft_sop.md"}),
        SOPChunk(id="c2", text="For RDP disconnects, wait 60 seconds and retry once.",
                 metadata={"source": "rdp_recovery.md"}),
        SOPChunk(id="c3", text="IIM loan search requires the 9-digit loan number.",
                 metadata={"source": "iim_sop.md"}),
    ]
    assert store.upsert_sop_chunks(chunks) == 3

    hits = store.query_sop("how should I handle small claim approvals", k=2)
    assert len(hits) >= 1
    # The lossdraft chunk should rank first.
    assert "loss-draft" in hits[0].text.lower() or "approve" in hits[0].text.lower()


# ── planner injection ───────────────────────────────────────────────────
def _patch_capture(client, captured: dict):
    original = client._create
    def _spy(**kwargs):
        captured.update(kwargs)
        return original(**kwargs)
    client.chat.completions.create = _spy


def test_planner_injects_sop_when_hits_available():
    from agent.planner import ActionPlanner
    from agent.schemas import ScreenState
    from tests.fixtures.mock_llm import MockOpenAIClient, make_action_plan

    knowledge = MagicMock()
    knowledge.query_sop.return_value = [
        KnowledgeHit(id="c1", text="POLICY: approve < $5000 silently.",
                     metadata={"source": "sop.md"}, distance=0.1),
    ]
    client = MockOpenAIClient(responses=[make_action_plan(confidence=0.95)])
    captured: dict = {}
    _patch_capture(client, captured)
    planner = ActionPlanner(client=client, knowledge=knowledge)
    planner.decide(
        screen_state=ScreenState(app_type="browser",
                                 state_summary="claim review page",
                                 confidence=0.9),
        working={"step": 0},
        goal="approve $1200 claim",
    )
    knowledge.query_sop.assert_called_once()
    sent = captured["messages"]
    assert sent[0]["role"] == "system"
    assert "POLICY: approve" in sent[0]["content"]
    assert "SOP CONTEXT" in sent[0]["content"] or "SOP GUIDANCE" in sent[0]["content"]


def test_planner_omits_system_message_when_no_sop_hits():
    from agent.planner import ActionPlanner
    from agent.schemas import ScreenState
    from tests.fixtures.mock_llm import MockOpenAIClient, make_action_plan

    knowledge = MagicMock()
    knowledge.query_sop.return_value = []
    client = MockOpenAIClient(responses=[make_action_plan(confidence=0.95)])
    captured: dict = {}
    _patch_capture(client, captured)
    planner = ActionPlanner(client=client, knowledge=knowledge)
    planner.decide(
        screen_state=ScreenState(app_type="browser", state_summary="x", confidence=0.9),
        working={"step": 0},
        goal="g",
    )
    sent = captured["messages"]
    # Only the user message — no system block.
    assert [m["role"] for m in sent] == ["user"]


def test_planner_swallows_sop_query_errors():
    from agent.planner import ActionPlanner
    from agent.schemas import ScreenState
    from tests.fixtures.mock_llm import MockOpenAIClient, make_action_plan

    knowledge = MagicMock()
    knowledge.query_sop.side_effect = RuntimeError("chroma down")
    client = MockOpenAIClient(responses=[make_action_plan(confidence=0.95)])
    planner = ActionPlanner(client=client, knowledge=knowledge)
    # Must not raise — retrieval is best-effort.
    plan = planner.decide(
        screen_state=ScreenState(app_type="browser", state_summary="x", confidence=0.9),
        working={"step": 0},
        goal="g",
    )
    assert plan is not None
