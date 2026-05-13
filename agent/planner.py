"""Action planner — ScreenState + working memory + goal → ActionPlan.

Only produces an ActionPlan. Never executes actions (CLAUDE.md boundary).
"""
from __future__ import annotations

import json
from typing import Any

from agent.llm_client import get_client, strip_json_fence
from agent.schemas import ActionPlan, ScreenState
from config.settings import settings

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
    def __init__(self, client: Any | None = None, retry_limit: int = 3) -> None:
        self._client = client
        self.retry_limit = retry_limit

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = get_client()
        return self._client

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
            completed=working.get("decisions_log", [])[-5:],
            state_summary=screen_state.state_summary,
            elements=json.dumps([e.model_dump() for e in screen_state.visible_elements]),
            last_result=working.get("last_result") or "none",
            blocking_issue=screen_state.blocking_issue,
            retry_count=retry_count,
            hitl_threshold=settings.confidence_threshold,
            retry_limit=self.retry_limit,
        )

        response = self.client.chat.completions.create(
            model=settings.model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=512,
            temperature=0.1,
        )
        raw = strip_json_fence(response.choices[0].message.content or "")
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
