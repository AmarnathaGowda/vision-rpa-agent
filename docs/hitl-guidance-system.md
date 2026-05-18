# HITL Guidance + Learning System

Status: implemented 2026-05-18 (MVP). Supersedes the 3-button control panel.

The HITL layer evolved from "approve / skip / abort" into an interactive
**guidance + learning surface** the operator uses to:

1. Tell the agent *what to do* via plain-English instructions.
2. Hand the agent a corrected target name + verified selector when it
   misidentified the element.
3. Persist that correction to org-wide memory so future runs reuse it.
4. Capture the lesson as a new SOP chunk for the planner's retrieval.

---

## 1. What changed

### 1.1 New `HumanGuidance` payload

[hitl/queue.py](hitl/queue.py) defines a small dataclass that travels with
every resolution that carries actual guidance:

```python
@dataclass
class HumanGuidance:
    instruction: str = ""              # free-form operator text
    corrected_target: str | None = None
    selector_hint: str | None = None
    save_to_memory: bool = False       # → ui_patterns collection
    save_to_sop: bool = False          # → sop_chunks collection
    confidence: float = 0.9
    created_by: str = "floating-ui"
```

`HumanGuidance.from_resolution(...)` returns one when the operator
submitted at least one of `instruction` / `corrected_target` /
`selector_hint`. Otherwise None — control-only actions (approve / skip /
abort) still work unchanged.

### 1.2 New resolution actions

Backwards-compatible additions to the existing `approve | correct | skip | abort | retry_with_values`:

| Action | Triggered by | Effect |
|---|---|---|
| `retry_with_hint` | "Submit guidance & retry" button with text only | Stash guidance; retry same step |
| `correct_target` | Target field set but no instruction | Same as above; target field is the focus |
| `teach_selector` | Target + selector + (implies save_to_memory) | Persists to ui_patterns, retries same step |
| `save_as_sop` | Save-to-SOP checkbox ticked | Writes a new SOP chunk + retries |

All four **clear retry counters and DO NOT advance the step** — the
operator just provided guidance; the agent should retry with it.

### 1.3 Planner injection

[agent/planner.py](agent/planner.py) — `_human_guidance_context()` returns
a high-priority system block when the working memory contains
`extracted_values["human_guidance"]`. The block instructs the LLM to
treat the operator as ground truth and overrides any conflicting
inference from the screenshot.

After the next plan executes, [agent/loop.py](agent/loop.py) `_store()`
clears the guidance — one-shot consumption. Re-submission required for
repeat application (avoids stale-hint loops).

### 1.4 Knowledge persistence

When `save_to_memory=True` and both `corrected_target` and
`selector_hint` are set, [hitl/queue.py](hitl/queue.py)
`_persist_guidance_to_knowledge()` calls
`KnowledgeStore.store_ui_pattern(...)` and flushes — making the mapping
available to subsequent runs via `SelectorResolver`'s locator-map path.

When `save_to_sop=True`, the same method writes a markdown chunk to the
`sop_chunks` Chroma collection with `metadata.source = hitl/<task_id>`
and `metadata.kind = hitl_correction`. The planner's existing SOP
retrieval surfaces it on similar tasks.

### 1.5 UI surface ([hitl/server.py](hitl/server.py))

The floating runtime panel now shows, in order:

1. **Friendly explanation** — plain-English description of why HITL fired.
2. **Failure category chip** — `selector_missing`, `page_not_loaded`,
   `modal_blocking`, `session_disconnected`, `missing_credentials`,
   `stuck_loop`, `low_confidence`, `uncertain_target`. Derived client-side
   from the reason text — useful at-a-glance.
3. **Screenshot** — what the agent saw.
4. **Credential input rows** — when `credential_keys` is non-empty.
5. **Guidance box** — the new core:
   - Multi-line instruction textarea.
   - Two side-by-side inputs: corrected target name, verified selector.
   - Two checkboxes: "Save this correction so the agent reuses it next time"
     and "Add to SOP knowledge".
6. **Action buttons**:
   - 💬 Submit guidance & retry (primary)
   - 🔑 Submit credentials & continue (shown only when credentials missing)
   - ↻ Just retry
   - ⤼ Skip this step
   - ✕ Stop task

7. **Technical details** (collapsed) — raw reason + task/agent ID for engineers.

### 1.6 Safety

[hitl/queue.py](hitl/queue.py) `_scrub_instruction()` strips obvious
injection patterns before the instruction is stored or shown to the
LLM:

```
<script, </script>, javascript:, data:text/html,
eval(, Function(, subprocess, os.system,
rm -rf, ; rm , | rm , $(, `
```

And caps instruction length to 2000 chars. The planner's `action_type`
allowlist is the durable safety net — even a malicious instruction can
only produce one of the framework's existing action primitives.

---

## 2. End-to-end example

The screenshot of the moment shows: `Password` is the planner's target,
the field can't be found, retries exhausted, HITL fires.

**Operator does:**

1. Looks at the screenshot in the floating panel.
2. Identifies that the password field is to the right of the "Password:"
   label (not the label itself).
3. In the guidance box:
   - **Instruction:** "The password field is the textbox immediately to
     the right of the 'Password:' label. Use the data-testid
     `login-password`."
   - **Corrected target:** `login_password`
   - **Verified selector:** `[data-testid="login-password"]`
   - **Tick:** "Save this correction so the agent reuses it next time"
4. Clicks **💬 Submit guidance & retry**.

**Behind the scenes:**

- UI POSTs `{action: "teach_selector", corrected_target: "login_password",
  selector_hint: "[data-testid=login-password]"}` to
  `/api/agent/{agent_id}/resolve/{hitl_id}`.
- `apply_resolution`:
  1. Stashes `HumanGuidance` in `working.extracted_values["human_guidance"]`.
  2. Calls `KnowledgeStore.store_ui_pattern(app="browser",
     element_desc="login_password",
     selector="[data-testid=login-password]", action_type="hitl_taught")`
     and flushes.
  3. Clears retry counters for the current step.
- `AgentLoop.resume()` re-perceives, then the planner sees the new
  high-priority system message and emits `type → login_password`.
- `SelectorResolver` looks up `login_password` in the locator map
  (we just wrote it via `store_ui_pattern`), gets
  `[data-testid="login-password"]`, fills the field successfully.

**Next run:**

- The ui_patterns chunk is already in chromadb. When the agent hits a
  similar screen and the planner queries / SelectorResolver checks the
  cache, it gets the verified selector directly — no LLM hallucination,
  no HITL.

---

## 3. Files changed in this round

| File | What |
|---|---|
| [hitl/queue.py](hitl/queue.py) | `HumanGuidance` dataclass, `_scrub_instruction`, 4 new actions, `_persist_guidance_to_knowledge`, `_clear_retry_counters` |
| [agent/planner.py](agent/planner.py) | `_human_guidance_context()` injected as priority system message |
| [agent/loop.py](agent/loop.py) | One-shot guidance consumption in `_store()` |
| [hitl/server.py](hitl/server.py) | Floating UI: guidance box (textarea + 2 inputs + 2 checkboxes), failure category chip, advanced submit button, `submitGuidance()` JS handler, JSON resolve endpoints accept new actions |
| [tests/test_human_guidance.py](tests/test_human_guidance.py) | 7 new tests covering dataclass parsing, scrubbing, all 4 new actions, knowledge writes |

51 tests pass across HITL + planner + loop + parity test suites.

---

## 4. What was deferred (and the reason)

| Asked for | Status | Why deferred |
|---|---|---|
| Visual annotation (click screenshot to mark element) | Defer | Real chunk of canvas + coordinate-storage work. Operator's text + corrected_target covers the same need today. |
| Agent doubt / ambiguity prompts | Defer | Already partially present via confidence threshold. Adding "I see 3 candidate fields, which one?" requires planner-side ambiguity tracking. |
| `ApplicationKnowledgeStore` scoped by URL/env/title | Defer | Existing `ui_patterns` collection covers 90%; scoping is a metadata-only enhancement (`where={"app": ..., "url": ...}` on query). |
| Full UI sections (Suggested Selectors / Agent Reasoning / Resume Strategy) | Defer | Most info is already in the existing dashboard at `/agent/{id}/review/{id}`. The runtime panel intentionally stays compact. |

Each deferral is a clear seam — adding any of them later is additive,
not a rewrite.

---

## 5. Honest limitations

1. **Selector hints are trusted but not validated.** If the operator
   types a CSS selector that doesn't actually match anything, the next
   action fails as before. We don't verify the selector against the
   current page (would require executor access from the queue, which
   crosses the layer boundary). The retry-loop guardrail catches the
   subsequent failure.
2. **`save_to_memory` requires both target AND selector.** A
   target-without-selector hint can't be cached because the cache layer
   stores `(target → selector)` pairs. Document this in the operator
   training.
3. **Cross-session learning happens only when chromadb is reachable.**
   Without it, `NullKnowledgeStore.store_ui_pattern()` is a no-op and
   the correction works for the current session only. Visible in logs.
4. **One-shot guidance consumption** means submitting the same hint
   twice in a row requires re-typing. If you want sticky guidance for a
   whole task, set `save_to_memory=True` — that's the supported
   mechanism for "remember this forever".
5. **No visual annotation yet.** Operator must describe in words. If the
   screenshot shows a confusing layout, the operator carries the burden
   of translating it to text.
