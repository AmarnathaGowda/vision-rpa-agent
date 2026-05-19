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


# ── Case 2 OCR field extractors (planner-side, no LLM) ───────────────────
# Mirrors _extract_borrower_name / _extract_ocr_address / _extract_ocr_carrier
# from legacy/automation/demo/run_case2_e2e_demo.py.

def _extract_borrower(cleaned_lines: list[str]) -> str:
    pats = [
        r"our\s+client\s*[:\s]+([A-Za-z][A-Za-z .'-]{2,50})$",
        r"insured\s+name\s*[:\s]+([A-Za-z][A-Za-z .'-]{2,50})$",
        r"payee\s+name\s*[:\s]+([A-Za-z][A-Za-z .'-]{2,50})(?:\s*&|$)",
        r"pay\s+to\s+(?:the\s+)?order\s+of\s*[:\s]+([A-Za-z][A-Za-z .'-]{2,50})(?:\s*&|$)",
    ]
    for line in cleaned_lines:
        for p in pats:
            m = re.search(p, line, re.IGNORECASE)
            if m:
                name = m.group(1).strip().rstrip(".,")
                if len(name) > 3:
                    return name.upper()
    return ""


def _extract_address(cleaned_lines: list[str]) -> str:
    pat = re.compile(
        r"\b(\d{3,5}\s+[A-Za-z][A-Za-z0-9\s]{2,35}"
        r"(?:AVE|AVENUE|ST|STREET|CIR|CIRCLE|DR|DRIVE|BLVD|BOULEVARD|"
        r"RD|ROAD|LN|LANE|WAY|PL|PLACE|CT|COURT|LOOP|TER|TERRACE|TRAIL|TRL))\b",
        re.IGNORECASE,
    )
    for line in cleaned_lines:
        m = pat.search(line)
        if m:
            return m.group(1).strip().upper()
    return ""


def _extract_carrier(cleaned_lines: list[str]) -> str:
    pats = [
        r"(tower\s+hill[a-z\s,\.]*(?:preferred|mutual|prime)?[a-z\s,\.]*(?:insurance|ins)[a-z\s,\.]*(?:company|co\.?)?)",
        r"carrier\s*[:\s]+([A-Za-z][A-Za-z\s,\.&]{3,50}(?:insurance|ins)[A-Za-z\s,\.]*)",
        r"insurer\s*[:\s]+([A-Za-z][A-Za-z\s,\.&]{3,50})",
    ]
    for line in cleaned_lines:
        for p in pats:
            m = re.search(p, line, re.IGNORECASE)
            if m:
                return m.group(1).strip().upper()
    for line in cleaned_lines:
        if re.search(r"\binsurance\b", line, re.IGNORECASE) and 8 < len(line) < 65:
            clean = re.sub(r"[^\w\s,\.&]", " ", line).strip().upper()
            if clean:
                return clean
    return ""

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
        from urllib.parse import urlparse
        try:
            path_now = urlparse(url_now).path or ""
        except Exception:  # noqa: BLE001
            path_now = ""

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

        # Rule 2 (must run BEFORE the doc-management guardrail): on the
        # SAML/SSO page, drive the form completely. The VLM mis-perceives
        # the SSO page as the RD Web login form, so the LLM picks the wrong
        # credentials and frequently clicks Sign On without filling fields.
        # We KNOW this form (USERNAME, PASSWORD, Sign On) — script it.
        if "/sso/" in path_now:
            sso_state = (working.get("extracted_values") or {}).get(
                "sso_state") or {}
            if not sso_state.get("username_filled"):
                return ActionPlan(
                    action_type="type",
                    target="USERNAME",
                    value="{{SSO_USERNAME}}",
                    reason=("SSO guardrail: fill USERNAME with the SSO "
                            "credential (NOT the RDWEB one)."),
                    confidence=1.0,
                    requires_hitl=False,
                    cache_hit=True,
                )
            if not sso_state.get("password_filled"):
                return ActionPlan(
                    action_type="type",
                    target="PASSWORD",
                    value="{{SSO_PASSWORD}}",
                    reason=("SSO guardrail: fill PASSWORD with the SSO "
                            "credential."),
                    confidence=1.0,
                    requires_hitl=False,
                    cache_hit=True,
                )
            # Both fields filled — submit.
            return ActionPlan(
                action_type="click",
                target="Sign On",
                reason=("SSO guardrail: USERNAME + PASSWORD filled — "
                        "submit the form."),
                confidence=1.0,
                requires_hitl=False,
                cache_hit=True,
            )

        # Rule 3: stage=document_management but the agent is NOT on the
        # document-management page yet (e.g. landed on Claim Search by
        # default after SSO). The SOP requires clicking the Document
        # Management tab first — emit that deterministically.
        if (working.get("current_stage") == "document_management"
                and "/lossdrafts" in path_now
                and "/document-management" not in path_now):
            return ActionPlan(
                action_type="click",
                target="Document Management",
                reason=("guardrail: stage=document_management requires the "
                        "Document Management tab to be active before any "
                        "row selection or document interaction."),
                confidence=1.0,
                requires_hitl=False,
                cache_hit=True,
            )

        # Rule 4: on /document-management page in document_management
        # stage, drive the row+link state machine deterministically.
        # Matches the legacy `run_case1_e2e_demo.py` flow:
        #   1. Click the Case 1 row (selects it)
        #   2. Click the Link in that row (opens PDF in new tab)
        #   3. Capture the popup URL as pdf_url
        # NOTE: Case 1 ONLY. Case 2 uses multi-select + per-doc state
        # machines (Rule 7 in _case2_plan), and case1-row would not match
        # any Case 2 row anyway. Hard-gate to avoid accidental triggers.
        if ((working.get("task_type") or "") == "case1"
                and working.get("current_stage") == "document_management"
                and "/document-management" in path_now):
            extracted = working.get("extracted_values") or {}
            if not extracted.get("pdf_url"):
                doc_state = extracted.get("doc_state") or {}
                if not doc_state.get("row_clicked"):
                    return ActionPlan(
                        action_type="click",
                        target="case1-row",
                        reason=("doc-mgmt guardrail: select the Case 1 row "
                                "in the Pending Scanned Documents table."),
                        confidence=1.0,
                        requires_hitl=False,
                        cache_hit=True,
                    )
                return ActionPlan(
                    action_type="click_open_popup",
                    target="case1-link",
                    reason=("doc-mgmt guardrail: open the Case 1 Link "
                            "(PDF popup) and capture its URL."),
                    confidence=1.0,
                    requires_hitl=False,
                    cache_hit=True,
                )

        # Rule 5: on pdf_extraction stage, run the OCR pipeline against
        # the captured pdf_url via the case1_extract_pdf tool, which
        # downloads the PDF then calls the legacy extract_from_pdf
        # (the only pipeline that produces `candidates` with header/body
        # roles, which Case 1's evaluator needs).
        if (working.get("current_stage") == "pdf_extraction"
                and (working.get("extracted_values") or {}).get("pdf_url")
                and not (working.get("extracted_values") or {}).get("candidates")):
            pdf_url = working["extracted_values"]["pdf_url"]
            return ActionPlan(
                action_type="extract",
                target="case1_extract_pdf",
                value=pdf_url,
                app="tool",
                reason=("pdf-extraction guardrail: download the PDF and "
                        "run the legacy OCR pipeline → candidates."),
                confidence=1.0,
                requires_hitl=False,
                cache_hit=True,
            )

        # Rule 6: on claim_validation stage, deterministically search each
        # extracted candidate's claim ID in the Claim Search panel (on the
        # same /document-management page). State machine in working memory:
        #   validation_state = {"index": <int>, "step": "type"|"submit"|"read"}
        # When all candidates are processed, write `validations` list and
        # let the stage auto-advance.
        if working.get("current_stage") == "claim_validation":
            extracted = working.get("extracted_values") or {}
            candidates = extracted.get("candidates") or []
            validations = extracted.get("validations") or []
            if candidates and len(validations) < len(candidates):
                state = extracted.get("validation_state") or {}
                idx = int(state.get("index", len(validations)))
                step = state.get("step", "type")
                if idx >= len(candidates):
                    return None
                claim_id = candidates[idx].get("value", "")
                if step == "type":
                    return ActionPlan(
                        action_type="type",
                        target="ld-doc-search-claim",
                        value=claim_id,
                        reason=(f"claim-validation guardrail: type "
                                f"candidate {idx+1}/{len(candidates)} "
                                f"({candidates[idx].get('role','?')}) "
                                f"into the Claim search field."),
                        confidence=1.0, requires_hitl=False, cache_hit=True,
                    )
                if step == "submit":
                    return ActionPlan(
                        action_type="click",
                        target="ld-doc-search-submit",
                        reason="claim-validation guardrail: submit the Claim search.",
                        confidence=1.0, requires_hitl=False, cache_hit=True,
                    )
                if step == "probe":
                    return ActionPlan(
                        action_type="js_eval",
                        target="claim_search_result_probe",
                        value=(
                            # Returns: "empty" | "found:<loan_no>" | "unknown"
                            "(() => {"
                            " const empty = document.querySelector("
                            "  \"[data-testid='ld-doc-claim-results-empty']\");"
                            " if (empty) return 'empty';"
                            " const row = document.querySelector("
                            "  \"[data-testid^='ld-doc-claim-row-']\");"
                            " if (row) {"
                            "  const tid = row.getAttribute('data-testid')||'';"
                            "  return 'found:' + tid.replace('ld-doc-claim-row-','');"
                            " }"
                            " return 'unknown';"
                            "})()"
                        ),
                        reason="claim-validation guardrail: probe results panel.",
                        confidence=1.0, requires_hitl=False, cache_hit=True,
                    )
                if step == "select":
                    loan_no = state.get("pending_loan_no") or ""
                    return ActionPlan(
                        action_type="click",
                        target=f"ld-doc-claim-row-{loan_no}",
                        reason=(f"claim-validation guardrail: select the "
                                f"matched result row (loan_no={loan_no})."),
                        confidence=1.0, requires_hitl=False, cache_hit=True,
                    )
                # Short-circuit: as soon as case1_result is synthesized
                # (closed claim found), end the task — no need to keep
                # validating other candidates.
                if extracted.get("case1_result"):
                    return ActionPlan(
                        action_type="task_complete",
                        target="",
                        reason=("Case 1 'Already Closed' verdict reached: "
                                f"{extracted['case1_result'].get('verdict')}. "
                                "Short-circuiting remaining stages."),
                        confidence=1.0, requires_hitl=False, cache_hit=True,
                    )
                if step == "check_closed":
                    return ActionPlan(
                        action_type="js_eval",
                        target="claim_closed_status_probe",
                        value=(
                            # Returns: "closed" | "open" — based on the
                            # banner + status badge rendered after selection.
                            "(() => {"
                            " const banner = document.querySelector("
                            "  \"[data-testid='ld-closed-claim-banner']\");"
                            " if (banner) return 'closed';"
                            " const badge = document.querySelector("
                            "  '.ld-status-closed');"
                            " if (badge) return 'closed';"
                            " return 'open';"
                            "})()"
                        ),
                        reason=("claim-validation guardrail: check whether "
                                "the selected claim is Closed."),
                        confidence=1.0, requires_hitl=False, cache_hit=True,
                    )

        # ── Case 2 guardrails (Rule 7) ────────────────────────────────
        # Each Case 2 stage has a dedicated, gated guardrail. They run only
        # when current_stage matches AND the task is case2 — Case 1 paths
        # never enter this block.
        if (working.get("task_type") or "").startswith("case2"):
            c2 = self._case2_plan(working, path_now=path_now)
            if c2 is not None:
                return c2

        return None

    # ── Case 2 deterministic dispatcher ────────────────────────────────
    CASE2_DOC_IDS = ("8184371", "8184373", "8184372")

    def _case2_plan(self, working: dict, *, path_now: str = "") -> ActionPlan | None:  # noqa: C901
        ev = working.get("extracted_values") or {}
        stage = working.get("current_stage") or ""

        # URL guard for ALL post-doc-mgmt sub-stages — if the LLM
        # navigated us off /document-management between iterations
        # (e.g. clicked "Search" thinking we wanted the Claim Search
        # tab), come back before doing anything row-specific.
        DM_STAGES = {"document_management", "multi_select", "pdf_capture",
                     "claim_search"}
        if stage in DM_STAGES and "/document-management" not in path_now:
            # The fallback-chain inside claim_search owns its own
            # navigation (proctor / claim-details URLs are legit). Do
            # not yank us back if a fallback is active for this doc.
            cs_state = ev.get("claim_search_state") or {}
            if not (stage == "claim_search" and cs_state.get("fallback") == "iim"):
                return ActionPlan(
                    action_type="navigate",
                    target="http://localhost:8000/lossdrafts/document-management",
                    value="http://localhost:8000/lossdrafts/document-management",
                    reason=(f"case2 guardrail: stage={stage} requires the "
                            f"Document Management page; current URL is "
                            f"'{path_now}' — navigate back."),
                    confidence=1.0, requires_hitl=False, cache_hit=True,
                )

        # Stage 3: document_management — open the tab if not already there.
        # Loop tracker flips document_management_open=true once the URL
        # actually contains /document-management.
        if stage == "document_management" and not ev.get("document_management_open"):
            return ActionPlan(
                action_type="click", target="ld-tab-document-management",
                reason="case2 guardrail: open Document Management tab.",
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )

        # Stage 4: multi_select — emit one click per row, modifiers=Meta for rows 2-3.
        if stage == "multi_select":
            state = ev.get("multi_select_state") or {"index": 0}
            idx = int(state.get("index", 0))
            if idx < len(self.CASE2_DOC_IDS):
                doc_id = self.CASE2_DOC_IDS[idx]
                modifiers = ["Meta"] if idx > 0 else []
                return ActionPlan(
                    action_type="click",
                    target=f"ld-pending-doc-{doc_id}",
                    modifiers=modifiers,
                    reason=(f"case2 multi-select guardrail: click row "
                            f"{idx+1}/3 (doc {doc_id})"
                            + (" with Cmd held" if modifiers else "")),
                    confidence=1.0, requires_hitl=False, cache_hit=True,
                )

        # Stage 5: pdf_capture — one tool call per doc.
        if stage == "pdf_capture":
            captured = ev.get("pdf_records") or []
            idx = len(captured)
            if idx < len(self.CASE2_DOC_IDS):
                doc_id = self.CASE2_DOC_IDS[idx]
                payload = json.dumps({"link_target": f"case2-link-{doc_id}",
                                       "doc_id": doc_id})
                return ActionPlan(
                    action_type="extract",
                    target="case2_open_pdf_capture",
                    value=payload, app="tool",
                    reason=(f"case2 pdf-capture guardrail: open PDF "
                            f"{idx+1}/3 (doc {doc_id})."),
                    confidence=1.0, requires_hitl=False, cache_hit=True,
                )

        # Stage 6: ocr_extract — one tool call per captured PDF.
        if stage == "ocr_extract":
            extractions = ev.get("extractions_by_doc") or {}
            pdf_records = ev.get("pdf_records") or []
            for rec in pdf_records:
                if rec.get("doc_id") not in extractions:
                    return ActionPlan(
                        action_type="extract",
                        target="case2_extract_pdf",
                        value=rec.get("path", ""), app="tool",
                        reason=(f"case2 ocr guardrail: extract PDF for "
                                f"doc {rec.get('doc_id')}."),
                        confidence=1.0, requires_hitl=False, cache_hit=True,
                    )

        # Stage 7: claim_search — per-doc state machine; on empty, switch
        # the doc into an IIM fallback sub-machine.
        if stage == "claim_search":
            return self._case2_claim_search_plan(ev)

        # Stage 8 (final): case2_evaluate — single tool call producing result.
        if stage == "case2_evaluate" and not ev.get("case2_result"):
            payload = json.dumps({
                "selected_doc_ids": ev.get("selected_doc_ids") or list(self.CASE2_DOC_IDS),
                "pdf_records": ev.get("pdf_records") or [],
                "claim_search_outcomes": ev.get("claim_search_outcomes") or [],
            })
            return ActionPlan(
                action_type="extract", target="case2_evaluate",
                value=payload, app="tool",
                reason="case2 evaluate guardrail: synthesize Case2FullResult.",
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )

        # Once case2_result is set, declare task complete.
        if ev.get("case2_result"):
            return ActionPlan(
                action_type="task_complete", target="",
                reason=("Case 2 complete: "
                        f"{ev['case2_result'].get('status')} "
                        f"({ev['case2_result'].get('matched_count')}"
                        f"/{ev['case2_result'].get('total_count')} matched)."),
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )
        return None

    def _case2_claim_search_plan(self, ev: dict) -> ActionPlan | None:  # noqa: C901
        """Per-doc Case 2 claim search + IIM fallback + stage-bridge chain.

        State: ``claim_search_state = {"index", "step", "fallback", "fallback_step", "pending_loan_no", "iim_match", "claim_details"}``.
        """
        outcomes = ev.get("claim_search_outcomes") or []
        extractions = ev.get("extractions_by_doc") or {}
        state = ev.get("claim_search_state") or {"index": 0, "step": "type"}
        idx = int(state.get("index", 0))

        if idx >= len(self.CASE2_DOC_IDS):
            return None
        doc_id = self.CASE2_DOC_IDS[idx]
        extraction = extractions.get(doc_id) or {}
        candidates = extraction.get("candidates") or []

        # Pick best candidate (header role preferred).
        best = next((c for c in candidates if c.get("role") == "header"), None) \
            or (candidates[0] if candidates else None)

        # If no claim ID at all, skip straight to outcome record (loop absorber handles advancement).
        if not best:
            return ActionPlan(
                action_type="js_eval",
                target="case2_no_candidate_marker",
                value=f"(() => 'no_candidate:{doc_id}')()",
                reason=f"case2 claim_search: doc {doc_id} has no candidate — record empty outcome.",
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )

        fallback = state.get("fallback")
        if fallback == "iim":
            return self._case2_iim_fallback_plan(ev, state, doc_id, extraction, best)

        step = state.get("step", "type")
        if step == "ensure_doc_mgmt":
            return ActionPlan(
                action_type="navigate",
                target="http://localhost:8000/lossdrafts/document-management",
                value="http://localhost:8000/lossdrafts/document-management",
                reason="case2 claim_search: return to Document Management.",
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )
        if step == "type":
            return ActionPlan(
                action_type="type", target="ld-doc-search-claim",
                value=best.get("value", ""),
                reason=(f"case2 claim_search: type claim ID for doc {doc_id} "
                        f"({idx+1}/3)."),
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )
        if step == "submit":
            return ActionPlan(
                action_type="click", target="ld-doc-search-submit",
                reason="case2 claim_search: submit Claim Search.",
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )
        if step == "probe":
            return ActionPlan(
                action_type="js_eval", target="claim_search_result_probe",
                value=(
                    "(() => {"
                    " const empty = document.querySelector("
                    "  \"[data-testid='ld-doc-claim-results-empty']\");"
                    " if (empty) return 'empty';"
                    " const row = document.querySelector("
                    "  \"[data-testid^='ld-doc-claim-row-']\");"
                    " if (row) {"
                    "  const tid = row.getAttribute('data-testid')||'';"
                    "  return 'found:' + tid.replace('ld-doc-claim-row-','');"
                    " }"
                    " return 'unknown';"
                    "})()"
                ),
                reason="case2 claim_search: probe results panel.",
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )
        if step == "select":
            loan_no = state.get("pending_loan_no") or ""
            return ActionPlan(
                action_type="click",
                target=f"ld-doc-claim-row-{loan_no}",
                reason=(f"case2 claim_search: select matched loan "
                        f"{loan_no} for doc {doc_id}."),
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )
        return None

    def _case2_iim_fallback_plan(self, ev: dict, state: dict, doc_id: str,
                                    extraction: dict, best: dict) -> ActionPlan | None:  # noqa: C901
        """IIM fallback + stage bridge chain for a single Case 2 doc."""
        sub = state.get("fallback_step", "extract_fields")
        if sub == "extract_fields":
            # Pure-Python regex pass over extraction["cleaned_lines"] —
            # surfaced as a js_eval no-op so the loop's absorber picks it up.
            cleaned = extraction.get("cleaned_lines") or []
            borrower = _extract_borrower(cleaned)
            address = _extract_address(cleaned)
            carrier = _extract_carrier(cleaned)
            payload = json.dumps({"borrower": borrower, "address": address,
                                   "carrier": carrier, "doc_id": doc_id})
            return ActionPlan(
                action_type="js_eval",
                target="case2_iim_fields_marker",
                value=f"(() => {json.dumps(payload)})()",
                reason=(f"case2 IIM fallback: extracted borrower/address/"
                        f"carrier from OCR for doc {doc_id}."),
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )
        if sub == "navigate":
            return ActionPlan(
                action_type="navigate",
                target="http://localhost:8000/proctor/loan-search",
                value="http://localhost:8000/proctor/loan-search",
                reason="case2 IIM fallback: open Proctor Loan Search.",
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )
        if sub == "type_first_name":
            borrower = (state.get("iim_borrower") or "").strip()
            first_name = borrower.split()[0] if borrower else ""
            return ActionPlan(
                action_type="type",
                target="pf-input-contact-name",
                value=first_name,
                reason=(f"case2 IIM fallback: type first name '{first_name}' "
                        f"for doc {doc_id}."),
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )
        if sub == "submit":
            return ActionPlan(
                action_type="click", target="pf-btn-search",
                reason="case2 IIM fallback: submit Proctor search.",
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )
        if sub == "scrape_rows":
            return ActionPlan(
                action_type="extract", target="case2_scrape_iim_rows",
                app="tool",
                reason="case2 IIM fallback: scrape result rows.",
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )
        if sub == "score":
            payload = json.dumps({
                "ocr_name": state.get("iim_borrower", ""),
                "ocr_address": state.get("iim_address", ""),
                "ocr_carrier": state.get("iim_carrier", ""),
                "iim_rows": state.get("iim_rows", []),
                "threshold": 60.0,
            })
            return ActionPlan(
                action_type="extract", target="case2_fuzzy_score",
                value=payload, app="tool",
                reason="case2 IIM fallback: fuzzy score candidates.",
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )
        if sub == "navigate_details":
            loan_no = state.get("iim_best_loan_no", "")
            url = f"http://localhost:8000/proctor/loan-details?loan_no={loan_no}"
            return ActionPlan(
                action_type="navigate", target=url, value=url,
                reason=f"case2 IIM fallback: open loan details for {loan_no}.",
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )
        if sub == "scrape_carrier":
            return ActionPlan(
                action_type="extract",
                target="case2_scrape_loan_details_carrier", app="tool",
                reason="case2 IIM fallback: scrape carrier from Loan Details.",
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )
        if sub == "rescore":
            payload = json.dumps({
                "ocr_name": state.get("iim_borrower", ""),
                "ocr_address": state.get("iim_address", ""),
                "ocr_carrier": state.get("iim_carrier", ""),
                "iim_rows": state.get("iim_rows", []),
                "iim_carrier": state.get("iim_loan_details_carrier", ""),
                "threshold": 60.0,
            })
            return ActionPlan(
                action_type="extract", target="case2_fuzzy_score",
                value=payload, app="tool",
                reason="case2 IIM fallback: re-score with carrier.",
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )

        # Stage-bridge sub-chain — loan_search → claim_details → letter →
        # comm_history → claim_linking → doc_assignment.
        if sub == "loan_search_navigate":
            return ActionPlan(
                action_type="navigate",
                target="http://localhost:8000/lossdrafts/",
                value="http://localhost:8000/lossdrafts/",
                reason="case2 stage 8: navigate to Loss Drafts home.",
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )
        if sub == "loan_search_type":
            return ActionPlan(
                action_type="type", target="ld-field-loan-no",
                value=state.get("iim_best_loan_no", ""),
                reason="case2 stage 8: type loan number.",
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )
        if sub == "loan_search_submit":
            return ActionPlan(
                action_type="click", target="ld-search-submit",
                reason="case2 stage 8: submit loan search.",
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )
        if sub == "loan_search_open_details":
            loan_no = state.get("iim_best_loan_no", "")
            url = f"http://localhost:8000/lossdrafts/claim-details?loan_no={loan_no}"
            return ActionPlan(
                action_type="navigate", target=url, value=url,
                reason=f"case2 stage 8: open Claim Details for {loan_no}.",
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )
        if sub == "scrape_claim_details":
            return ActionPlan(
                action_type="extract", target="case2_scrape_claim_details",
                app="tool",
                reason="case2 stage 9: scrape Claim Details fields.",
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )
        if sub == "letter_open_header":
            return ActionPlan(
                action_type="click",
                target="ld-cd-letter-requests-header",
                reason="case2 stage 10: expand Letter Requests section.",
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )
        if sub == "letter_open_add":
            return ActionPlan(
                action_type="click", target="ld-cd-letter-requests-add",
                reason="case2 stage 10: open Create Letter panel.",
                confidence=1.0, requires_hitl=False, cache_hit=True,
            )
        if sub in ("run_stage7", "run_stage8", "run_stage9", "run_stage10"):
            tool_target = f"case2_{sub}"
            claim_data = state.get("claim_details") or {}
            payload = json.dumps({**claim_data,
                                   "loan_no": state.get("iim_best_loan_no", ""),
                                   "doc_id": doc_id})
            return ActionPlan(
                action_type="extract", target=tool_target,
                value=payload, app="tool",
                reason=f"case2 stage bridge: {sub}.",
                confidence=1.0, requires_hitl=False, cache_hit=True,
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

    # Destructive click targets that the LLM occasionally picks when
    # confused (e.g. "Logout" / "Sign out" to escape a stuck state).
    # During an active workflow this destroys the session and forces the
    # operator to restart. We intercept and route to HITL instead.
    DESTRUCTIVE_TARGETS = {
        "logout", "log out", "sign out", "signout",
        "cancel", "close", "exit", "abort",
        "delete", "remove", "discard",
    }

    def _enforce_action_rules(self, plan: ActionPlan) -> ActionPlan:
        """Deterministic plan rewrites that don't depend on LLM judgement."""
        target_norm = (plan.target or "").lower().strip()
        if plan.action_type == "click":
            if target_norm in self.LAUNCHER_TARGETS:
                log.info("planner_upgraded_click_to_download_open",
                         target=plan.target,
                         reason="known launcher — clicking triggers a download")
                plan.action_type = "click_download_open"
            elif target_norm in self.DESTRUCTIVE_TARGETS:
                log.warning("planner_blocked_destructive_click",
                            target=plan.target,
                            reason=(
                                "destructive target during workflow — "
                                "would destroy session / cancel work. "
                                "Routing to HITL instead."
                            ))
                # Force HITL so the operator can confirm or correct.
                plan.requires_hitl = True
                plan.reason = (
                    f"BLOCKED: agent tried to click {plan.target!r} which "
                    f"destroys the active session. If you actually want to "
                    f"end the task, click 'Stop task'. Otherwise give the "
                    f"agent a corrected next step."
                )
                plan.confidence = 0.0
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
