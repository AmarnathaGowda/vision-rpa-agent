"""Action planner — ScreenState + working memory + goal → ActionPlan.

Only produces an ActionPlan. Never executes actions (CLAUDE.md boundary).
"""
from __future__ import annotations

import json
import re
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
CURRENT WORKFLOW STAGE: {current_stage}
STAGES ALREADY COMPLETED: {stages_completed}
ACTIONS ALREADY DONE (most recent last — do NOT repeat any of these
unless the screen shows the previous action did not take effect):
{completed}
CURRENT URL (ground truth from the browser): {current_url}
CURRENT SCREEN: {state_summary}
VISIBLE ELEMENTS: {elements}
LAST ACTION RESULT: {last_result}
BLOCKING ISSUE: {blocking_issue}
RETRY COUNT THIS STEP: {retry_count}

Choose the single next action. Return ONLY valid JSON:
{{
  "action_type": "click|type|navigate|read|extract|wait|flag_human|js_eval|task_complete|click_download_open",
  "target": "<element description or selector>",
  "value": "<text to type, URL, or JS expression>",
  "reason": "<why this action>",
  "confidence": 0.0,
  "fallback": "<alternative selector if primary fails>",
  "is_financial": false,
  "requires_hitl": false
}}

TASK COMPLETION:
- When the current screen shows the goal has been satisfied (e.g. the
  goal said "log in" and the page is now the post-login landing page),
  emit action_type "task_complete". Put a short reason explaining what
  evidence in the screen confirms success.
- task_complete is the ONLY way to end an LLM-driven task. If you keep
  emitting flag_human or other actions after the goal is met, the loop
  will eventually trip the duplicate-plan guardrail.

WORKFLOW STAGES:
- For multi-stage tasks, the goal lists the sequence of stages and the
  exit criteria for each. Working memory tracks `current_stage` and
  `stages_completed`.
- When you see clear evidence the current stage's exit criteria are met
  AND another stage remains, emit action_type "stage_complete" with
  target=<next_stage_id> (matching the names in the goal). This advances
  the workflow without ending the task.
- Emit task_complete ONLY when the FINAL stage's exit criteria are met
  (or when working memory has all keys listed under done_when in the
  task YAML).

Rules:
- One action only.
- Never guess financial values — use extract or flag_human.
- If confidence < {hitl_threshold} → set requires_hitl: true.
- If retry_count >= {retry_limit} → action_type must be "flag_human".
- If error_present → action_type must be "click" on dismiss button.

COMMON SENSE RULES (apply BEFORE looking at the screen):
- The CURRENT URL above comes from the browser itself — TRUST IT over
  any URL you might infer from the screenshot.
- If CURRENT URL is empty, about:blank, chrome://newtab/, or data:,
  the browser has NOT loaded the application yet — the only correct
  action_type is "navigate" using the URL in the goal.
- Never type, click, or read on a blank page. There is nothing there
  to interact with regardless of what the screenshot suggests.
- If CURRENT URL doesn't contain the goal's target URL fragment, the
  next action should usually be "navigate" — not type or click.

CREDENTIAL VARIABLES (use these placeholders — do NOT type the literal
text, the framework substitutes them at action time):
- {{RDWEB_USERNAME}} — domain\\username for RD Web login
- {{RDWEB_PASSWORD}} — password for RD Web login
- {{SIM_USERNAME}} / {{SIM_PASSWORD}} — same values for the local sim
- Any other key the operator has set in .env can be referenced as {{KEY}}
NEVER emit a plan with `value: "rdweb_username"` (the bare name) —
that types the literal string into the form. Always use `{{ }}`.

ACTION ↔ ELEMENT TYPE RULES (most common LLM mistake):
- `type` actions ONLY target form INPUTS (<input>, <textarea>, <select>).
  NEVER target a label, button, link, or div for `type` — the framework
  will reject it with a "wrong_element_for_type" error.
- After filling all visible input fields, the NEXT action is usually
  `click` on the submit button — NOT another `type`.
- Read the visible_elements list carefully: only entries whose `type` is
  "field" are valid `type` targets. Entries with type "button" require
  a `click` action.

CLICK-THAT-DOWNLOADS-A-LAUNCHER:
- If the goal/SOP says clicking a button or tile will trigger a file
  download (RDWeb "Loss Drafts" tile is the canonical example — clicking
  it downloads an HTML launcher whose meta-refresh redirects to the real
  app URL), emit action_type=`click_download_open` (NOT plain `click`).
- The framework will: capture the download, parse the meta-refresh URL,
  and navigate the current tab to that URL automatically.
- Use plain `click` for buttons that DO NOT trigger downloads (e.g.
  Sign-in, regular folder tiles).

FORM-COMPLETENESS RULE (do not rush past empty fields):
- Before emitting `click` on a Submit / Sign-in / OK button, verify
  EVERY visible input field has been filled. Check the previous "ACTIONS
  ALREADY DONE" list — if it contains, say, "type into username" but NOT
  "type into password", you MUST emit `type` on the password field NEXT.
  Do NOT click submit yet.
- If the screen shows a form-validation message like "Please fill in
  this field" or "Required", look at which input is highlighted in the
  screenshot — that is the field you need to type into NEXT.

SELECTOR RULES (critical — selector miss is the most common failure):
- The "target" field MUST be one of the following, in priority order:
  1. The exact `testid` string from a visible_element (e.g. "user-input").
     Do NOT wrap it in `[data-testid='…']`. Do NOT invent variants.
  2. The element's `label` string verbatim (e.g. "Sign in").
  3. Only if neither testid nor label exists, a CSS selector you compose.
- Never modify, prefix, or "improve" a testid you can see. "user-input"
  stays "user-input", not "domain-user-input" or "[data-testid=user-input]".
- If the element you want is NOT in visible_elements, set requires_hitl: true
  rather than guessing.
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

    # ── Human guidance injection ─────────────────────────────────────
    @staticmethod
    def _human_guidance_context(working: dict) -> str:
        """Return a high-priority system block when the operator has just
        submitted guidance via HITL. The guidance is consumed once — after
        producing a plan that uses it, the planner removes it from working
        memory so the loop doesn't keep applying stale hints."""
        extracted = working.get("extracted_values") or {}
        g = extracted.get("human_guidance")
        if not g:
            return ""
        parts = ["⚠ OPERATOR GUIDANCE (just submitted — apply on this turn):"]
        if g.get("instruction"):
            parts.append(f"- Instruction: {g['instruction']}")
        if g.get("corrected_target"):
            parts.append(
                f"- USE THIS TARGET EXACTLY for the next action: "
                f"{g['corrected_target']!r}. Do not invent alternatives."
            )
        if g.get("selector_hint"):
            parts.append(
                f"- The verified selector for that target is "
                f"{g['selector_hint']!r}."
            )
        parts.append(
            "This guidance overrides any conflicting inference from the "
            "screenshot. Treat the operator as ground truth."
        )
        return "\n".join(parts)

    # ── Decisions formatter ──────────────────────────────────────────
    @staticmethod
    def _format_decisions(entries: list[dict], limit: int = 6) -> str:
        """Render the most recent decisions as readable bullet points.

        The LLM ignores raw dict dumps. A bulleted list with the action
        verb, the target, and (for `type`) the value length makes it
        obvious what's already been done — and stops the agent from
        re-typing the same field five times in a row.
        """
        if not entries:
            return "(none yet — this is the first action)"
        recent = entries[-limit:]
        out = []
        for e in recent:
            action = e.get("action_type", "?")
            target = e.get("target", "?")
            value = e.get("value", "") or ""
            status = e.get("result_status", "?")
            if action == "type":
                detail = f"type '{value[:20] + '…' if len(value) > 20 else value}' into '{target}'"
            elif action == "navigate":
                detail = f"navigate to {value or target}"
            elif action == "click":
                detail = f"click '{target}'"
            else:
                detail = f"{action} on '{target}'"
            out.append(f"  - {detail}  [{status}]")
        return "\n".join(out)

    # ── Common-sense guardrails ─────────────────────────────────────
    _BLANK_URLS = ("", "about:blank", "chrome://newtab/", "data:,")
    _URL_RE = re.compile(r"https?://[^\s'\"]+")

    def _common_sense_plan(self, screen, goal: str, working: dict) -> ActionPlan | None:
        """Return a deterministic ActionPlan when basic rules apply.

        These guardrails fire BEFORE the LLM and short-circuit it. Each
        rule is intentionally narrow — if any rule misses, we fall through
        to the LLM (which is the right behaviour for anything ambiguous).

        Rule 1 — blank page + URL in goal: navigate to that URL first.
                 Stops the agent from typing into about:blank.

        Rule 2 — current URL doesn't share host/path with goal URL but
                 we're already on a real page: do nothing here; let the
                 LLM decide (maybe SAML redirect, maybe legit cross-app).

        Rule 3 — already on the goal URL: do nothing here; let the LLM
                 plan the form fill / click / etc.
        """
        url_now = (screen.current_url or "").strip()
        goal_urls = self._URL_RE.findall(goal or "")

        # Rule 1: blank page + goal mentions a URL → navigate.
        if url_now in self._BLANK_URLS and goal_urls:
            target_url = goal_urls[0]
            return ActionPlan(
                action_type="navigate",
                target=target_url,
                value=target_url,
                reason=("guardrail: page is blank and the goal mentions "
                        f"{target_url} — navigate first before any other action."),
                confidence=1.0,
                requires_hitl=False,
                cache_hit=True,
            )
        return None

    def _known_targets_context(self) -> str:
        """Return a system message listing the verified locator-map keys.

        The VLM hallucinates testids from screenshots (e.g. reads
        ``login-username`` as ``user-input``). Naming the real keys in the
        prompt redirects the LLM to pick from this whitelist instead. The
        SelectorResolver then resolves the friendly key via the locator map.
        """
        try:
            from config.locators.rdweb import ALL
        except Exception:  # noqa: BLE001 — never block planning on this
            return ""
        if not ALL:
            return ""
        # Cap to a reasonable size — 628 keys × ~25 chars would overflow.
        keys = sorted(ALL.keys())[:300]
        return (
            "VERIFIED ELEMENT NAMES (the page-source-of-truth — when one of "
            "these names matches the element you want, set target to the "
            "EXACT key; do NOT improvise or wrap in [data-testid=…]):\n"
            + ", ".join(keys)
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

        # ── Deterministic guardrails (run BEFORE the LLM) ────────────
        # Some plans don't need a model. They need basic common sense.
        # These guardrails save an LLM round-trip AND prevent the agent
        # from typing into pages that don't exist yet.
        guardrail = self._common_sense_plan(screen_state, goal, working)
        if guardrail is not None:
            log.info("planner_guardrail_used",
                     action=guardrail.action_type,
                     target=guardrail.target,
                     reason=guardrail.reason)
            return guardrail

        step_key = str(working.get("step", 0))
        retry_count = int(working.get("retry_counts", {}).get(step_key, 0))

        prompt = PLANNING_PROMPT.format(
            goal=goal,
            current_stage=working.get("current_stage") or "(none — single-stage task)",
            stages_completed=", ".join(working.get("stages_completed") or []) or "(none)",
            completed=self._format_decisions(working.get("decisions_log", [])),
            current_url=screen_state.current_url or "(unknown)",
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
        names_block = self._known_targets_context()
        guidance_block = self._human_guidance_context(working)
        messages: list[dict] = []
        # Guidance goes FIRST and is the highest-priority system message —
        # the operator just told us what to do, trust them over the model.
        if guidance_block:
            messages.append({"role": "system", "content": guidance_block})
        if names_block:
            messages.append({"role": "system", "content": names_block})
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

        # Apply deterministic plan rewrites BEFORE HITL classification.
        # These turn common LLM mistakes into the correct primitive instead
        # of letting them fail and trigger HITL.
        plan = self._enforce_action_rules(plan)

        # Apply deterministic HITL rules — independent of model judgement.
        plan = self._enforce_hitl_rules(plan, retry_count)
        return plan

    # Known launcher targets — clicking these triggers a file download
    # whose meta-refresh URL we want to follow. The framework auto-rewrites
    # plain `click` on these to `click_download_open` so the LLM doesn't
    # have to remember the right primitive on every run.
    LAUNCHER_TARGETS = {"loss drafts", "loss draft", "lossdrafts"}

    def _enforce_action_rules(self, plan: ActionPlan) -> ActionPlan:
        """Deterministic plan rewrites that don't depend on LLM judgement."""
        if plan.action_type == "click":
            target_norm = (plan.target or "").lower().strip()
            if target_norm in self.LAUNCHER_TARGETS:
                log.info("planner_upgraded_click_to_download_open",
                         target=plan.target,
                         reason="known launcher — clicking triggers a download")
                plan.action_type = "click_download_open"
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
        # The action_type itself encodes intent: "flag_human" means
        # "stop and ask the operator". The LLM doesn't always set the
        # `requires_hitl` flag when it emits this action — make it
        # implicit so the loop routes to HITL instead of no-op dispatching.
        if plan.action_type == "flag_human":
            plan.requires_hitl = True
        return plan
