"""Action planner — ScreenState + working memory + goal → ActionPlan.

Only produces an ActionPlan. Never executes actions (CLAUDE.md boundary).
"""
from __future__ import annotations

import json
from typing import Any

from agent.llm_client import strip_json_fence
from agent.providers import LLMProvider, get_provider
from agent.providers.legacy_adapter import _LegacyClientProvider
from agent.schemas import ActionPlan, ScreenState
from config.logging_config import get_logger
from config.settings import settings

log = get_logger(__name__)

PLANNING_PROMPT = """You are an RPA action planner for insurance claim automation.

TASK GOAL: {goal}
COMPLETED STEPS: {completed}
CURRENT SCREEN: {state_summary}
VISIBLE ELEMENTS: {elements}
LAST ACTION RESULT: {last_result}
BLOCKING ISSUE: {blocking_issue}
RETRY COUNT THIS STEP: {retry_count}

Choose the single next action. Return ONLY valid JSON:
{{
  "action_type": "click|type|navigate|read|extract|wait|flag_human|js_eval",
  "target": "<element description or selector>",
  "value": "<text to type, URL, or JS expression>",
  "reason": "<why this action>",
  "confidence": 0.0,
  "fallback": "<alternative selector if primary fails>",
  "is_financial": false,
  "requires_hitl": false
}}

Rules:
- One action only.
- Never guess financial values — use extract or flag_human.
- If confidence < {hitl_threshold} → set requires_hitl: true.
- If retry_count >= {retry_limit} → action_type must be "flag_human".
- If error_present → action_type must be "click" on dismiss button.
- Prefer data-testid selectors from known locators.
"""


class ActionPlanner:
    SOP_TOP_K = 2          # how many SOP chunks to inject per plan
    SOP_MAX_CHARS = 1600   # truncate combined SOP text to stay within prompt budget

    def __init__(
        self,
        client: Any | None = None,
        provider: LLMProvider | None = None,
        retry_limit: int = 3,
        knowledge: Any | None = None,
    ) -> None:
        # ``provider`` preferred; ``client`` wrapped for backward compat.
        if provider is not None:
            self._provider: LLMProvider | None = provider
        elif client is not None:
            self._provider = _LegacyClientProvider(client)
        else:
            self._provider = None
        self.retry_limit = retry_limit
        # ``knowledge`` is a KnowledgeStore — typically ChromaKnowledgeStore in
        # production, NullKnowledgeStore in tests/CI. Resolved lazily so the
        # planner doesn't try to open Chroma when SOPs are unused.
        self._knowledge = knowledge

    @property
    def knowledge(self):
        if self._knowledge is None:
            from memory.knowledge import get_knowledge_store
            self._knowledge = get_knowledge_store()
        return self._knowledge

    def _sop_context(self, goal: str, screen_summary: str) -> str:
        """Return a 'SOP CONTEXT' block to inject into the system prompt, or ''
        if no relevant chunks are available. Failures are swallowed — SOP
        retrieval is an enhancement, not a hard dependency."""
        try:
            query = f"{goal}\n{screen_summary}".strip()
            hits = self.knowledge.query_sop(query, k=self.SOP_TOP_K)
        except Exception as e:  # noqa: BLE001 — never block a plan on retrieval
            log.warning("sop_query_failed", error=str(e))
            return ""
        if not hits:
            return ""
        parts: list[str] = []
        total = 0
        for h in hits:
            src = (h.metadata or {}).get("source", "sop")
            block = f"[SOP — {src}]\n{h.text}".strip()
            if total + len(block) > self.SOP_MAX_CHARS:
                break
            parts.append(block)
            total += len(block) + 2
        if not parts:
            return ""
        return (
            "RELEVANT SOP GUIDANCE (retrieved by similarity to the current "
            "task/screen — follow only when applicable):\n\n"
            + "\n\n".join(parts)
        )

    @property
    def _active_provider(self) -> LLMProvider:
        if self._provider is None:
            self._provider = get_provider()
        return self._provider

    def decide(
        self,
        screen_state: ScreenState | dict,
        working: dict,
        goal: str,
    ) -> ActionPlan:
        if isinstance(screen_state, dict):
            screen_state = ScreenState(**screen_state)

        step_key = str(working.get("step", 0))
        retry_count = int(working.get("retry_counts", {}).get(step_key, 0))

        prompt = PLANNING_PROMPT.format(
            goal=goal,
            completed=working.get("decisions_log", [])[-3:],
            state_summary=screen_state.state_summary,
            elements=json.dumps([e.model_dump() for e in screen_state.visible_elements]),
            last_result=working.get("last_result") or "none",
            blocking_issue=screen_state.blocking_issue,
            retry_count=retry_count,
            hitl_threshold=settings.confidence_threshold,
            retry_limit=self.retry_limit,
        )

        sop_block = self._sop_context(goal=goal,
                                      screen_summary=screen_state.state_summary)
        messages: list[dict] = []
        if sop_block:
            messages.append({"role": "system", "content": sop_block})
        messages.append({"role": "user", "content": prompt})

        raw = strip_json_fence(
            self._active_provider.complete(
                messages=messages,
                max_tokens=512,
                temperature=0.1,
            )
        )
        data = json.loads(raw)
        plan = ActionPlan(**data)

        # Apply deterministic HITL rules — independent of model judgement.
        plan = self._enforce_hitl_rules(plan, retry_count)
        return plan

    def _enforce_hitl_rules(self, plan: ActionPlan, retry_count: int) -> ActionPlan:
        threshold = settings.confidence_threshold
        fin_threshold = settings.financial_confidence_threshold

        if plan.confidence < threshold:
            plan.requires_hitl = True
        if plan.is_financial and plan.confidence < fin_threshold:
            plan.requires_hitl = True
        if retry_count >= self.retry_limit:
            plan.action_type = "flag_human"
            plan.requires_hitl = True
        return plan
