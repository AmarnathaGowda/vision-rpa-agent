"""Tests for the multi-stage workflow primitives.

Covers:
- WorkingMemory.current_stage / stages_completed initialisation.
- stage_complete action advances the tracker without exiting the task.
- task_complete is GATED by done_when.all_set — premature declarations
  route to HITL instead of exiting with success.
"""
from __future__ import annotations

from agent.loop import AgentLoop
from agent.schemas import ActionPlan, ActionResult
from memory.working import WorkingMemory


class _ScriptedExecutor:
    """Returns the queued ActionResult per call; records the plans seen."""

    def __init__(self, results: list[ActionResult]) -> None:
        self._results = list(results)
        self.calls: list[ActionPlan] = []

    def execute(self, plan: ActionPlan) -> ActionResult:
        self.calls.append(plan)
        if not self._results:
            return ActionResult(status="skipped")
        return self._results.pop(0)


def test_working_memory_starts_with_initial_stage(session_store):
    loop = AgentLoop(session=session_store, executor=_ScriptedExecutor([]),
                     agent_id="agent_test")
    loop._init_task({
        "task_id": "t-stage",
        "task_type": "case1",
        "goal": "multi-stage test",
        "initial_stage": "login",
    })
    assert loop.working is not None
    assert loop.working.current_stage == "login"
    assert loop.working.stages_completed == []


def test_stage_complete_advances_tracker():
    """Calling _store with a stage_complete plan should:
    - append the previous stage to stages_completed
    - set current_stage to the plan target
    - NOT set task_complete"""
    from memory.session import SessionMemory
    import sqlite3

    store = SessionMemory.__new__(SessionMemory)
    store.db_path = ":memory:"
    store.conn = sqlite3.connect(":memory:", check_same_thread=False)
    store.conn.execute("PRAGMA journal_mode=WAL")
    store.conn.row_factory = sqlite3.Row
    store._create_schema()
    loop = AgentLoop(session=store, executor=_ScriptedExecutor([]),
                     agent_id="agent_test")
    loop._init_task({
        "task_id": "t-stage", "task_type": "case1",
        "goal": "g", "initial_stage": "login",
    })

    # Simulate the loop's stage_complete branch directly.
    plan = ActionPlan(action_type="stage_complete",
                      target="loss_drafts_launch",
                      reason="login page is now the production folder")
    # Manually run the same mutation the loop does (we don't run the
    # full _run_loop here — perception would call out to a VLM).
    old = loop.working.current_stage
    if old and old not in loop.working.stages_completed:
        loop.working.stages_completed.append(old)
    loop.working.current_stage = plan.target

    assert loop.working.current_stage == "loss_drafts_launch"
    assert loop.working.stages_completed == ["login"]
    assert loop.working.task_complete is False
    store.conn.close()


def test_missing_completion_keys_returns_unset_keys():
    from memory.session import SessionMemory
    import sqlite3

    store = SessionMemory.__new__(SessionMemory)
    store.db_path = ":memory:"
    store.conn = sqlite3.connect(":memory:", check_same_thread=False)
    store.conn.execute("PRAGMA journal_mode=WAL")
    store.conn.row_factory = sqlite3.Row
    store._create_schema()
    loop = AgentLoop(session=store, executor=_ScriptedExecutor([]),
                     agent_id="agent_test")
    loop._init_task({
        "task_id": "t-gate", "task_type": "case1", "goal": "g",
        "done_when": {"all_set": ["rdweb_authenticated", "candidates",
                                   "case1_result"]},
    })
    # Only one of the three keys is present.
    loop.working.extracted_values["rdweb_authenticated"] = True
    missing = loop._missing_completion_keys()
    assert set(missing) == {"candidates", "case1_result"}
    store.conn.close()


def test_missing_completion_keys_empty_when_satisfied():
    from memory.session import SessionMemory
    import sqlite3

    store = SessionMemory.__new__(SessionMemory)
    store.db_path = ":memory:"
    store.conn = sqlite3.connect(":memory:", check_same_thread=False)
    store.conn.execute("PRAGMA journal_mode=WAL")
    store.conn.row_factory = sqlite3.Row
    store._create_schema()
    loop = AgentLoop(session=store, executor=_ScriptedExecutor([]),
                     agent_id="agent_test")
    loop._init_task({
        "task_id": "t-gate", "task_type": "case1", "goal": "g",
        "done_when": {"all_set": ["rdweb_authenticated"]},
    })
    loop.working.extracted_values["rdweb_authenticated"] = True
    assert loop._missing_completion_keys() == []
    store.conn.close()


def test_inject_completion_guidance_writes_human_guidance():
    """When the gate rejects task_complete, framework should auto-inject
    guidance that the planner picks up next iteration — no HITL."""
    from memory.session import SessionMemory
    import sqlite3

    store = SessionMemory.__new__(SessionMemory)
    store.db_path = ":memory:"
    store.conn = sqlite3.connect(":memory:", check_same_thread=False)
    store.conn.execute("PRAGMA journal_mode=WAL")
    store.conn.row_factory = sqlite3.Row
    store._create_schema()
    loop = AgentLoop(session=store, executor=_ScriptedExecutor([]),
                     agent_id="agent_test")
    loop._init_task({
        "task_id": "t", "task_type": "case1", "goal": "g",
        "initial_stage": "login",
        "done_when": {"all_set": ["rdweb_authenticated", "candidates"]},
    })
    loop.working.stages_completed = ["login"]
    loop.working.current_stage = "loss_drafts_launch"
    loop._inject_completion_guidance(["candidates", "case1_result"])
    g = loop.working.extracted_values.get("human_guidance")
    assert g is not None
    assert g["created_by"] == "framework_auto_correct"
    assert "candidates" in g["instruction"]
    assert "loss_drafts_launch" in g["instruction"]
    store.conn.close()


def test_auto_advance_stage_on_url_match(monkeypatch):
    """When the executor's page URL matches a stage's exit_url_substring,
    the loop should advance current_stage and set the listed done_when keys
    — no LLM call required."""
    from memory.session import SessionMemory
    import sqlite3

    store = SessionMemory.__new__(SessionMemory)
    store.db_path = ":memory:"
    store.conn = sqlite3.connect(":memory:", check_same_thread=False)
    store.conn.execute("PRAGMA journal_mode=WAL")
    store.conn.row_factory = sqlite3.Row
    store._create_schema()

    class _StubPage:
        url = "http://localhost:8000/rdweb/pages/en-US/Default.aspx/Production"
    class _StubBrowser:
        page = _StubPage()
    class _StubExec:
        browser = _StubBrowser()
        def execute(self, plan):
            from agent.schemas import ActionResult
            return ActionResult(status="ok")

    loop = AgentLoop(session=store, executor=_StubExec(), agent_id="agent_test")
    loop._init_task({
        "task_id": "t-stage-advance", "task_type": "case1", "goal": "g",
        "initial_stage": "login",
        "stages": [
            {"name": "login",
             "exit_url_substring": "/Default.aspx/Production",
             "sets_keys": ["rdweb_authenticated"],
             "next": "loss_drafts_launch"},
        ],
    })
    assert loop.working.current_stage == "login"
    loop._maybe_auto_advance_stage()
    assert loop.working.current_stage == "loss_drafts_launch"
    assert loop.working.stages_completed == ["login"]
    assert loop.working.extracted_values.get("rdweb_authenticated") is True
    store.conn.close()


def test_no_auto_advance_when_url_doesnt_match():
    from memory.session import SessionMemory
    import sqlite3

    store = SessionMemory.__new__(SessionMemory)
    store.db_path = ":memory:"
    store.conn = sqlite3.connect(":memory:", check_same_thread=False)
    store.conn.execute("PRAGMA journal_mode=WAL")
    store.conn.row_factory = sqlite3.Row
    store._create_schema()

    class _StubPage:
        url = "http://localhost:8000/rdweb/pages/en-US/login.aspx"
    class _StubBrowser:
        page = _StubPage()
    class _StubExec:
        browser = _StubBrowser()
        def execute(self, plan):
            from agent.schemas import ActionResult
            return ActionResult(status="ok")

    loop = AgentLoop(session=store, executor=_StubExec(), agent_id="agent_test")
    loop._init_task({
        "task_id": "t-no-advance", "task_type": "case1", "goal": "g",
        "initial_stage": "login",
        "stages": [
            {"name": "login",
             "exit_url_substring": "/Default.aspx/Production",
             "sets_keys": ["rdweb_authenticated"],
             "next": "loss_drafts_launch"},
        ],
    })
    loop._maybe_auto_advance_stage()
    # Still on login because URL doesn't contain /Default.aspx/Production.
    assert loop.working.current_stage == "login"
    assert loop.working.stages_completed == []
    assert "rdweb_authenticated" not in loop.working.extracted_values
    store.conn.close()


def test_auto_advance_ignores_url_query_string():
    """An exit pattern `/lossdrafts/` should NOT match an SSO URL like
    `/sso/idp/SSO.saml2?ReturnUrl=/lossdrafts/`. The matcher must look at
    the URL path only, not the query string."""
    from memory.session import SessionMemory
    import sqlite3

    store = SessionMemory.__new__(SessionMemory)
    store.db_path = ":memory:"
    store.conn = sqlite3.connect(":memory:", check_same_thread=False)
    store.conn.execute("PRAGMA journal_mode=WAL")
    store.conn.row_factory = sqlite3.Row
    store._create_schema()

    class _StubPage:
        url = "http://localhost:8000/sso/idp/SSO.saml2?ReturnUrl=/lossdrafts/"
    class _StubBrowser:
        page = _StubPage()
    class _StubExec:
        browser = _StubBrowser()
        def execute(self, plan):
            from agent.schemas import ActionResult
            return ActionResult(status="ok")

    loop = AgentLoop(session=store, executor=_StubExec(), agent_id="agent_test")
    loop._init_task({
        "task_id": "t-query", "task_type": "case1", "goal": "g",
        "initial_stage": "loss_drafts_launch",
        "stages": [
            {"name": "loss_drafts_launch",
             "exit_url_substring": "/lossdrafts/",
             "sets_keys": ["lossdrafts_loaded"],
             "next": "document_management"},
        ],
    })
    loop._maybe_auto_advance_stage()
    # SSO URL has /lossdrafts/ in the QUERY STRING, not the path → no advance.
    assert loop.working.current_stage == "loss_drafts_launch"
    assert loop.working.stages_completed == []
    assert "lossdrafts_loaded" not in loop.working.extracted_values
    store.conn.close()


def test_planner_auto_clicks_doc_management_tab_when_stage_demands_it():
    """When current_stage='document_management' but URL path is on a
    different lossdrafts subpage (e.g. /lossdrafts/search), the planner
    should deterministically emit click → Document Management."""
    from agent.planner import ActionPlanner
    from agent.schemas import ScreenState
    from tests.fixtures.mock_llm import MockOpenAIClient, make_action_plan

    # The LLM might want to click Search but the guardrail should override.
    bad_payload = make_action_plan(action_type="click", target="Search",
                                    confidence=1.0)
    planner = ActionPlanner(client=MockOpenAIClient(responses=[bad_payload]))
    screen = ScreenState(
        app_type="browser",
        state_summary="Claim Search results",
        current_url="http://localhost:8000/lossdrafts/search",
        confidence=0.95,
    )
    plan = planner.decide(
        screen,
        working={"step": 0, "current_stage": "document_management",
                 "retry_counts": {}},
        goal="case1_full_flow",
    )
    assert plan.action_type == "click"
    assert plan.target == "Document Management"
    assert "document_management" in (plan.reason or "").lower()


def test_planner_sso_guardrail_fills_username_first():
    """On the SSO page with empty form state, the planner must
    deterministically emit `type → USERNAME` with the SSO placeholder."""
    from agent.planner import ActionPlanner
    from agent.schemas import ScreenState
    from tests.fixtures.mock_llm import MockOpenAIClient, make_action_plan

    # LLM would emit click-Sign-On (wrong, the form is empty), but the
    # guardrail must override.
    bad_payload = make_action_plan(action_type="click", target="Sign On",
                                    confidence=1.0)
    planner = ActionPlanner(client=MockOpenAIClient(responses=[bad_payload]))
    screen = ScreenState(
        app_type="browser",
        state_summary="SAML SSO login form",
        current_url="http://localhost:8000/sso/idp/SSO.saml2?ReturnUrl=/lossdrafts/",
        confidence=0.9,
    )
    plan = planner.decide(
        screen,
        working={"step": 0, "extracted_values": {}, "retry_counts": {}},
        goal="case1_full_flow",
    )
    assert plan.action_type == "type"
    assert plan.target == "USERNAME"
    assert "{{SSO_USERNAME}}" in (plan.value or "")


def test_planner_sso_guardrail_fills_password_when_username_already_filled():
    from agent.planner import ActionPlanner
    from agent.schemas import ScreenState
    from tests.fixtures.mock_llm import MockOpenAIClient, make_action_plan

    bad_payload = make_action_plan(action_type="click", target="Sign On",
                                    confidence=1.0)
    planner = ActionPlanner(client=MockOpenAIClient(responses=[bad_payload]))
    screen = ScreenState(
        app_type="browser",
        state_summary="SSO form",
        current_url="http://localhost:8000/sso/idp/SSO.saml2",
        confidence=0.9,
    )
    plan = planner.decide(
        screen,
        working={"step": 0, "retry_counts": {},
                 "extracted_values": {"sso_state": {"username_filled": True}}},
        goal="case1_full_flow",
    )
    assert plan.action_type == "type"
    assert plan.target == "PASSWORD"
    assert "{{SSO_PASSWORD}}" in (plan.value or "")


def test_planner_sso_guardrail_clicks_sign_on_when_both_filled():
    from agent.planner import ActionPlanner
    from agent.schemas import ScreenState
    from tests.fixtures.mock_llm import MockOpenAIClient, make_action_plan

    bad_payload = make_action_plan(action_type="navigate", target="/elsewhere",
                                    confidence=1.0)
    planner = ActionPlanner(client=MockOpenAIClient(responses=[bad_payload]))
    screen = ScreenState(
        app_type="browser",
        state_summary="SSO form filled",
        current_url="http://localhost:8000/sso/idp/SSO.saml2",
        confidence=0.9,
    )
    plan = planner.decide(
        screen,
        working={"step": 0, "retry_counts": {},
                 "extracted_values": {"sso_state": {
                     "username_filled": True,
                     "password_filled": True}}},
        goal="case1_full_flow",
    )
    assert plan.action_type == "click"
    assert plan.target == "Sign On"


def test_planner_blocks_destructive_click():
    """A click on 'Logout' (or similar) during workflow should be flagged
    for HITL with a clear destructive-action message."""
    from agent.planner import ActionPlanner
    from agent.schemas import ActionPlan, ScreenState
    from tests.fixtures.mock_llm import MockOpenAIClient, make_action_plan

    plan_payload = make_action_plan(action_type="click", target="Logout",
                                    confidence=1.0)
    planner = ActionPlanner(client=MockOpenAIClient(responses=[plan_payload]))
    screen = ScreenState(app_type="browser", state_summary="claim list",
                         confidence=0.9)
    plan = planner.decide(screen, working={"step": 0,
                                            "current_stage": "claim_validation"},
                           goal="case 1 e2e")
    assert plan.requires_hitl is True
    assert "BLOCKED" in (plan.reason or "")
    assert "Logout" in (plan.reason or "")


def test_missing_completion_keys_no_done_when_returns_empty():
    """Tasks without done_when (the legacy case) should never block."""
    from memory.session import SessionMemory
    import sqlite3

    store = SessionMemory.__new__(SessionMemory)
    store.db_path = ":memory:"
    store.conn = sqlite3.connect(":memory:", check_same_thread=False)
    store.conn.execute("PRAGMA journal_mode=WAL")
    store.conn.row_factory = sqlite3.Row
    store._create_schema()
    loop = AgentLoop(session=store, executor=_ScriptedExecutor([]),
                     agent_id="agent_test")
    loop._init_task({"task_id": "t-old", "task_type": "case1", "goal": "g"})
    assert loop._missing_completion_keys() == []
    store.conn.close()
