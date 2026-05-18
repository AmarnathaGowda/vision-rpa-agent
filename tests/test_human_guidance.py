"""Tests for the HumanGuidance + retry_with_hint resolution flow."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hitl.queue import HITLQueue, HumanGuidance, _scrub_instruction
from memory.working import WorkingMemory


def _make_working(step: int = 1) -> WorkingMemory:
    w = WorkingMemory(task_id="t", task_type="case1", goal="g",
                      agent_id="a", step=step)
    w.retry_counts[str(step)] = 3
    w.retry_counts[f"recovery_{step}"] = 1
    w.hitl_pending = True
    return w


# ── HumanGuidance dataclass ───────────────────────────────────────────
def test_from_resolution_returns_none_for_control_only():
    g = HumanGuidance.from_resolution({"action": "approve"})
    assert g is None


def test_from_resolution_picks_up_instruction():
    g = HumanGuidance.from_resolution({
        "action": "retry_with_hint",
        "instruction": "Use the field labelled 'Domain\\\\user name'",
        "corrected_target": "login_username",
    })
    assert g is not None
    assert "Domain" in g.instruction
    assert g.corrected_target == "login_username"
    assert g.selector_hint is None


def test_to_dict_scrubs_dangerous_patterns():
    g = HumanGuidance(instruction="ok <script>alert(1)</script> rm -rf /")
    d = g.to_dict()
    assert "<script" not in d["instruction"]
    assert "rm -rf" not in d["instruction"]


def test_scrub_caps_length():
    long = "x" * 5000
    assert len(_scrub_instruction(long)) == 2000


# ── HITLQueue.apply_resolution ────────────────────────────────────────
def test_retry_with_hint_stashes_guidance_and_clears_retries(session_store):
    q = HITLQueue(session=session_store)
    w = _make_working(step=2)
    q.apply_resolution({
        "action": "retry_with_hint",
        "instruction": "Use the textbox NEXT to the label, not the label itself.",
        "corrected_target": "login_username",
    }, w)
    # Guidance landed in working memory for the planner to pick up.
    g = w.extracted_values.get("human_guidance")
    assert g is not None
    assert g["corrected_target"] == "login_username"
    assert "textbox NEXT to the label" in g["instruction"]
    # Retry counters cleared, step NOT advanced.
    assert w.step == 2
    assert "2" not in w.retry_counts
    assert "recovery_2" not in w.retry_counts
    assert w.hitl_pending is False


def test_teach_selector_forces_save_to_memory(session_store):
    """The teach_selector action implies save_to_memory=True even if the
    operator forgot the checkbox."""
    q = HITLQueue(session=session_store, knowledge=MagicMock())
    w = _make_working(step=1)
    q.apply_resolution({
        "action": "teach_selector",
        "corrected_target": "login_username",
        "selector_hint": "[data-testid='login-username']",
    }, w)
    g = w.extracted_values["human_guidance"]
    assert g["save_to_memory"] is True


def test_guidance_persisted_to_knowledge_when_save_flags(session_store):
    fake_kb = MagicMock()
    q = HITLQueue(session=session_store, knowledge=fake_kb)
    w = _make_working(step=1)
    q.apply_resolution({
        "action": "teach_selector",
        "corrected_target": "login_username",
        "selector_hint": "[data-testid='login-username']",
    }, w)
    fake_kb.store_ui_pattern.assert_called_once()
    kwargs = fake_kb.store_ui_pattern.call_args.kwargs
    assert kwargs["element_desc"] == "login_username"
    assert kwargs["selector"] == "[data-testid='login-username']"
    fake_kb.flush.assert_called()


def test_save_as_sop_writes_chunk(session_store):
    fake_kb = MagicMock()
    q = HITLQueue(session=session_store, knowledge=fake_kb)
    w = _make_working(step=1)
    q.apply_resolution({
        "action": "save_as_sop",
        "instruction": "When you see the MotownPLP login page, the username field is to the right of the 'Domain\\user name' label.",
        "corrected_target": "login_username",
    }, w)
    fake_kb.upsert_sop_chunks.assert_called_once()
    chunks = fake_kb.upsert_sop_chunks.call_args.args[0]
    assert len(chunks) == 1
    assert "MotownPLP login page" in chunks[0].text


def test_approve_on_flag_human_emits_override(session_store):
    """When the operator approves a flag_human plan, the queue should
    stash a one-shot next_action_override so the loop bypasses the LLM
    and executes the concrete action (click on the flagged target)."""
    q = HITLQueue(session=session_store)
    w = _make_working(step=3)
    w.decisions_log.append({
        "step": 3, "action_type": "flag_human", "target": "Sign in",
        "value": "", "confidence": 1.0,
    })
    q.apply_resolution({"action": "approve"}, w)
    override = w.extracted_values.get("next_action_override")
    assert override is not None
    assert override["action_type"] == "click"
    assert override["target"] == "Sign in"
    assert override["requires_hitl"] is False


def test_retry_with_hint_proceed_text_emits_override(session_store):
    """retry_with_hint with text like 'please proceed' should also unlock."""
    q = HITLQueue(session=session_store)
    w = _make_working(step=3)
    w.decisions_log.append({
        "step": 3, "action_type": "flag_human", "target": "Sign in",
    })
    q.apply_resolution({
        "action": "retry_with_hint",
        "instruction": "Please proceed",
    }, w)
    assert w.extracted_values.get("next_action_override") is not None


def test_retry_with_hint_unrelated_text_does_not_emit_override(session_store):
    """A hint that gives a real correction should not be auto-converted."""
    q = HITLQueue(session=session_store)
    w = _make_working(step=3)
    w.decisions_log.append({
        "step": 3, "action_type": "flag_human", "target": "Sign in",
    })
    q.apply_resolution({
        "action": "retry_with_hint",
        "instruction": "The username field is to the right of the label.",
        "corrected_target": "login_username",
    }, w)
    # When a corrected target IS provided we keep the LLM-driven path so
    # the planner can re-reason with the new hint.
    assert "next_action_override" not in w.extracted_values


def test_approve_without_flag_human_does_not_emit_override(session_store):
    """Approving a non-flag plan must not invent a click."""
    q = HITLQueue(session=session_store)
    w = _make_working(step=2)
    w.decisions_log.append({
        "step": 2, "action_type": "type", "target": "username",
    })
    q.apply_resolution({"action": "approve"}, w)
    assert "next_action_override" not in w.extracted_values


def test_unknown_action_still_rejected(session_store):
    q = HITLQueue(session=session_store)
    w = _make_working(step=1)
    with pytest.raises(ValueError):
        q.apply_resolution({"action": "yolo"}, w)
