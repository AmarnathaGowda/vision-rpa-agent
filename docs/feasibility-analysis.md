# Feasibility Analysis — Vision RPA Agent

## Executive Summary

The proposed dynamic vision-driven agent architecture is **feasible but requires targeted design work** in six specific areas before implementation begins. The four existing POC cases are fully supportable by this architecture, but the migration from fixed-pipeline to dynamic-agent requires a compatibility bridge layer. This document captures the complete feasibility assessment, technical challenges, risks, blockers, and required architectural changes.

---

## Part 1 — POC Case Compatibility Analysis

### Overview of Existing Cases

| Case | Name | Stages | Primary Systems | Key Tech Used |
|------|------|--------|----------------|---------------|
| Case 1 | Already Closed | Backend only | PDF extraction, mock loan DB | pdfplumber, Tesseract, Pydantic |
| Case 2 | Multi-Record + Claim Operations | 8 stages | LD browser, PDF viewer | Playwright, OCR, rapidfuzz |
| Case 3 | Hold Check Process | 13 stages | RD Web, File Explorer, LD, IIM | Playwright, pywinauto, OCR, multi-tab |
| Case 4 | Stamp and Go | 12 stages | RD Web, File Explorer, LD, Excel | Playwright, openpyxl, OCR, multi-tab |

---

### Case 1 — Already Closed

**What it does:** Pure backend — extracts claim IDs from PDFs via OCR, validates against a loan database, detects closure phrases, and returns a structured decision.

**New architecture support:** FULL

| Requirement | Supported? | How |
|-------------|-----------|-----|
| PDF text extraction | Yes | pdfplumber → Tesseract pipeline (already in architecture) |
| Claim ID extraction | Yes | Extraction pipeline + local VLM if OCR fails |
| Database lookup | Yes | Deterministic Python code — no LLM needed |
| Decision rules (header priority) | Yes | Deterministic business logic in Python |
| Closure phrase detection | Yes | Python regex — no change needed |
| Result schema (Pydantic) | Yes | Reuse Case1Result model directly |
| No browser interaction | Yes | Case 1 is backend-only — no Playwright needed |

**Migration effort:** Minimal — wrap existing handler.py as a `task_executor` callable from the agent loop.

**Risks:** None. Case 1 is the simplest — pure Python logic, no UI automation.

---

### Case 2 — Multi-Record Selection + Claim Operations

**What it does:** Selects 3 documents in LD browser, reads PDFs via OCR, performs claim search, creates letter request, verifies communication history, links claim, and assigns documents.

**New architecture support:** FULL (all interactions are browser-based)

| Requirement | Supported? | How |
|-------------|-----------|-----|
| Browser multi-select (click + Ctrl-click) | Yes | Playwright executor |
| PDF open in new tab + byte capture | Yes | Playwright multi-tab coordinator |
| OCR extraction from PDF bytes | Yes | Extraction pipeline |
| LD form fills (search, letter, claim ID) | Yes | Playwright executor |
| Dropdown selection | Yes | Playwright `select_option` |
| Modal dialogs (open/fill/save/cancel) | Yes | Playwright executor |
| Toast verification | Yes | Playwright `wait_for_selector` |
| Fuzzy borrower matching | Yes | rapidfuzz — deterministic, no LLM |
| Document assignment modal | Yes | Playwright executor |
| data-testid selectors | Yes | All selectors in rdweb.py already use data-testid |

**Dynamic agent advantage over fixed pipeline:**
- If the document list has a different number of rows than expected, the agent observes and adapts instead of hard-failing
- If a modal dialog appears unexpectedly, recovery handler dismisses it and retries
- If toast does not appear (already seen this bug in POC), agent waits and re-checks without needing a code fix

**Migration effort:** Medium — stage functions become callable tools. ActionRouter directs all Case 2 actions to BrowserExecutor.

**Risks:**
- Multi-select (`Ctrl+click`) coordination is fragile if row count changes — agent must observe current row state before selecting
- PDF byte capture from new browser tab requires tab coordination — already handled in POC but must be preserved in new architecture

---

### Case 3 — Hold Check Process

**What it does:** Full multi-system workflow — RD Web login → File Explorer → PDF OCR → LD browser → IIM browser → transaction creation → notification creation. This is the most complex POC case.

**New architecture support:** FULL with one infrastructure note

| Requirement | Supported? | How |
|-------------|-----------|-----|
| RD Web browser login | Yes | Playwright executor |
| RemoteApp workspace navigation | Yes | Playwright executor (browser-based simulation) |
| File Explorer navigation | Yes | pywinauto UIA (File Explorer is a Win32 window) |
| PDF open via File Explorer → browser tab | Yes | pywinauto opens file → Playwright captures new tab |
| OCR on PDF bytes | Yes | Extraction pipeline |
| LD browser: claim search, details, forms | Yes | Playwright executor |
| IIM browser: loan search, details | Yes | Playwright executor (IIM is also browser-based) |
| Fuzzy borrower/payee matching | Yes | rapidfuzz — deterministic |
| Multi-tab coordination (pdf_tab + main tab) | Yes | Playwright BrowserContext tracks all tabs |
| Transaction creation modal | Yes | Playwright executor |
| Duplicate detection (transaction + notif) | Yes | Deterministic check before create |
| Notification creation modal | Yes | Playwright executor |
| Stage-by-stage result tracking | Yes | Session memory SQLite records per stage |

**Dynamic agent advantage:**
- IIM recovery (Stage 8) is currently hardcoded as "if Stage 7 fails → go to IIM." Dynamic agent observes current state and decides fallback naturally
- Fuzzy match threshold (0.60) can be exposed in task YAML and adjusted without code change
- If File Explorer shows a different folder structure, agent re-navigates instead of hard-failing

**Migration effort:** High — Case 3 crosses the browser↔File Explorer boundary, which requires the ActionRouter to correctly switch between Playwright and pywinauto executors. This handoff logic needs explicit testing.

**Infrastructure note:** In production, File Explorer navigation will use the real Windows File Explorer (pywinauto UIA), not the simulated HTML File Explorer. This is a correct implementation — pywinauto is already in the stack for this purpose.

**Risks:**
- The browser↔pywinauto handoff is the highest-risk transition point in any of the 4 cases
- File Explorer path structure (network drives, subfolder names) must match exactly — agent perception layer must extract current path from breadcrumb to verify position

---

### Case 4 — Stamp and Go

**What it does:** Most comprehensive case — Excel read from shared drive, LD claim search and validation, dual-PDF OCR (check + adjuster report), RCV extraction, claim update, multiple modal workflows (Process Event, Select Docs, Letter Request, SG Request, Notifications). 12 stages.

**New architecture support:** FULL

| Requirement | Supported? | How |
|-------------|-----------|-----|
| File Explorer → Excel file location | Yes | pywinauto UIA → extract URL |
| Excel download (urllib) + openpyxl parse | Yes | FileExecutor |
| LD claim search by loan number | Yes | Playwright executor |
| Claim detail tab navigation | Yes | Playwright executor |
| Dual-PDF open + OCR (check + adjuster) | Yes | Playwright multi-tab + extraction pipeline |
| Borrower cross-validation (fuzzy ≥90%) | Yes | rapidfuzz deterministic |
| RCV value extraction from adjuster PDF | Yes | Extraction pipeline |
| Edit Claim modal (complex form fill) | Yes | Playwright executor |
| Claim Event creation + task row validation | Yes | Playwright executor |
| Process Event (checkbox + save) | Yes | Playwright executor |
| Select Docs modal (dual-list moves) | Yes | Playwright executor |
| Letter Request creation | Yes | Playwright executor |
| SG Request creation + validation | Yes | Playwright executor |
| Notifications modal (search + multi-select via JS) | Yes | Playwright + page.evaluate for JS toggle |
| Final claim detail verification | Yes | Playwright + text extraction |
| Overlay visual feedback | Yes | overlay_call via page.evaluate — preserved as-is |

**Dynamic agent advantage:**
- If the Excel file naming pattern changes, agent reads the file explorer listing and picks the most recent match rather than failing on pattern mismatch
- If an unexpected modal appears between stages (e.g., session timeout warning), recovery handler handles it
- Stage checkpointing means crash at Stage 10 resumes at Stage 10, not Stage 1

**Migration effort:** High — 12 stages with multiple modal types. Each modal becomes an action sequence the agent learns. Highest LLM token cost per task due to complexity.

**Risks:**
- `ldCdNotifToggle` JS injection for notification selection — this must be preserved exactly. In real app, the JS function name may differ; agent must detect this.
- RCV extraction requires numerical parsing from adjuster OCR — requires confidence > 0.90 before any financial write action
- SG Request validation compares specific expected values (payer name, amount, status) — must route to HITL if mismatch

---

### POC Case Support Summary

```
Case 1 ─── Backend only ──────────────────── 100% supported, minimal migration
Case 2 ─── Browser only ──────────────────── 100% supported, medium migration
Case 3 ─── Browser + File Explorer + IIM ──── 100% supported, high migration (handoff risk)
Case 4 ─── All systems, 12 stages ──────────── 100% supported, high migration (complexity)
```

---

## Part 2 — New Agent System Capability Analysis

The new architecture must support six capability areas beyond what the POC provides.

### Capability 1 — Dynamic Scenario Handling

**Requirement:** Agent adapts to unexpected UI states without code changes.

**How it works:**
```
FIXED PIPELINE (POC):              DYNAMIC AGENT (new):
────────────────────               ──────────────────────────────
Stage 7 fails → abort              Stage 7 fails →
                                     RecoveryHandler detects reason
                                     → tries dismiss_dialog
                                     → tries scroll_into_view
                                     → tries wait_and_retry
                                     → tries re_login
                                     → flags HITL if all fail
```

**Feasibility:** Yes — fully supported by the perception + recovery + HITL chain.

**Design required:**
- RecoveryHandler must be scheduled in Phase 0 (currently missing from roadmap)
- Recovery rules must be encoded as YAML alongside task definitions, not hardcoded in Python

---

### Capability 2 — State-Aware Workflow Execution

**Requirement:** Agent knows where it is, what it has done, and what state the application is in at every step.

**How it works:**
- ScreenState JSON from perception layer captures current app state after every action
- Session SQLite checkpoints record completed stages with their output data
- Working memory dict holds task-scoped state (extracted values, retry counts)

**Feasibility:** Yes — fully covered by the three-tier memory system.

**Design required:**
- ScreenState model must include a `workflow_position` field (which tab, which modal, which stage) so the agent can orient itself after recovery
- SQLite checkpoint schema must be defined and versioned before Phase 1 coding begins

---

### Capability 3 — Action and Case History Storage

**Requirement:** Every action and its result is stored and queryable for audit and pattern learning.

**Storage design:**
```
Per action (SQLite — actions table):
  task_id, step_number, action_type, target_description,
  value_used, result_status, error_if_any, duration_ms,
  screenshot_path, timestamp

Per extraction (SQLite — extractions table):
  task_id, field_name, raw_value, normalized_value,
  confidence, extraction_method, source_document, timestamp

Per task (SQLite — tasks table):
  task_id, task_type, goal_description, status,
  total_steps, started_at, completed_at, result_json
```

**Feasibility:** Yes — SQLite covers all of this with zero infrastructure.

**Design required:** Define and version the SQLite schema before any code is written (see architecture.md update below).

---

### Capability 4 — Reuse of Previous Execution Patterns

**Requirement:** When the agent encounters a UI element or workflow it has seen before, it reuses the known-good approach rather than calling the LLM.

**How it works:**
```
Agent encounters login form on LD Module
  → Query ChromaDB: "ld_module login form selector"
  → Match found (confidence 0.92): {selector: "[data-testid='username']", method: "fill"}
  → Skip LLM call, execute directly
  → LLM call saved → ~20 seconds saved per cached step

Agent encounters unknown modal
  → ChromaDB: no match
  → Call VLM → identify elements
  → Execute → store result as new pattern in ChromaDB
```

**Feasibility:** Yes — ChromaDB handles this. Critical implementation detail: pattern must be stored with app version fingerprint so outdated patterns are not reused after app updates.

**Design required:**
- ChromaDB pattern schema: `{app_name, app_version, element_description, selector, action_type, confidence, success_count, last_validated}`
- Cache invalidation strategy: re-validate pattern every N successful uses or if app version changes

---

### Capability 5 — Faster Issue Resolution Based on Prior Cases

**Requirement:** If the same error was seen before and resolved, the agent applies the known fix without HITL.

**How it works:**
```
Agent encounters: "Session expired — please log in again"
  → Query ChromaDB: "session expired error on LD Module"
  → Match found: {recovery_action: "re_login", success_rate: 0.97}
  → Execute re_login directly, no HITL
  → If re_login succeeds: increment success_rate, resume
  → If re_login fails: HITL escalation

First time agent sees a new error:
  → HITL: human resolves
  → Resolution stored in ChromaDB with tags
  → Next occurrence: automatic resolution
```

**Feasibility:** Yes — ChromaDB `error_recoveries` collection supports this directly.

**Design required:**
- `error_recoveries` collection schema: `{error_text_pattern, recovery_action, success_rate, agent_id_first_seen, timestamp}`
- Confidence threshold for automatic recovery: success_rate ≥ 0.90 required before bypassing HITL

---

### Capability 6 — Improved Decision-Making During Execution

**Requirement:** Agent makes better decisions over time using accumulated knowledge.

**How it works:**
- First execution of a task type: higher LLM dependency, more HITL
- After N successful executions: cached patterns cover 70%+ of steps, LLM used only for novel situations
- Confidence scores improve as patterns are validated across multiple runs

**Feasibility:** Yes — this is the natural outcome of ChromaDB pattern accumulation.

**Design required:**
- Pattern validation counter: track how many times a pattern was used successfully
- Decay mechanism: patterns not used in 30 days get re-validated on next use
- Confidence promotion: pattern starts at 0.75, promoted to 0.90 after 5 successful uses

---

## Part 3 — Technical Challenges

### Challenge 1 — Browser ↔ Desktop Handoff (High)

When control transitions from Playwright (browser) to pywinauto (File Explorer) and back, the agent must:
1. Detect the context change via ScreenState `app_type` field
2. Switch the ActionRouter to the correct executor
3. Verify the switch succeeded before proceeding

**Risk:** If the context switch is not detected, the agent tries to use Playwright selectors on a Win32 window — silent failure, no exception.

**Mitigation:** ActionRouter validates `app_type` before every action. If mismatch, perception layer is called again before proceeding.

---

### Challenge 2 — VLM Inference Latency on CPU (High)

MiniCPM-V 2.6 on CPU: 15–40 seconds per inference call. Case 4 has ~50 individual actions. At 40 seconds each with LLM calls for every action: 33 minutes per task. Unacceptable even for development.

**Mitigation strategy:**
```
Tier 1: Cached patterns (ChromaDB hit) → 0 LLM calls → ~0.1 seconds
Tier 2: Deterministic action (known selector) → 0 LLM calls → ~0.5 seconds
Tier 3: VLM perception only (state check) → 1 LLM call → 15–40 seconds
Tier 4: VLM perception + planning → 2 LLM calls → 30–80 seconds

Target: 80% Tier 1/2, 15% Tier 3, 5% Tier 4
Achievable after: 5+ full task runs to populate ChromaDB
```

**Expected per-task time with warm cache:** 5–10 minutes (development). 1–3 minutes (production with GPU).

---

### Challenge 3 — RDP Session Lifecycle (High)

The RDP session is a dependency for File Explorer and RemoteApp windows. Session disconnect mid-task is unrecoverable without explicit reconnect logic.

**Required implementation:**
```python
# Background thread — runs for entire task duration
class RDPKeepAlive(threading.Thread):
    def run(self):
        while self._active:
            try:
                # Move mouse by 1 pixel within RDP window bounds
                hwnd = self.find_rdp_window()
                if hwnd:
                    win32api.SetCursorPos((hwnd.rect.mid_point()))
                    time.sleep(240)  # every 4 minutes
                else:
                    self._on_disconnect()
            except Exception:
                self._on_disconnect()

    def _on_disconnect(self):
        # Try reconnect up to 3 times with 10-second backoff
        for attempt in range(3):
            time.sleep(10 * (attempt + 1))
            if self._reconnect():
                return
        # Signal agent loop to route to HITL
        self.agent_event_bus.emit("rdp_disconnected")
```

---

### Challenge 4 — Confidence Scoring for Financial Data (Critical)

Extraction of financial values (check amounts, RCV totals, loan numbers) from OCR requires confidence ≥ 0.90. Below this, action must not proceed.

**Implementation rule (non-negotiable):**
```python
# In planner.py — enforced for all financial fields
FINANCIAL_FIELDS = {"amount", "rcv", "check_number", "loan_number", "claim_id"}

if action.field_name in FINANCIAL_FIELDS and action.confidence < 0.90:
    return ActionPlan(action_type="flag_for_human",
                      reason=f"Financial field '{action.field_name}' confidence {action.confidence} < 0.90")
```

---

### Challenge 5 — Multi-Modal Dialog Sequencing (Medium)

Case 4 has up to 4 nested modal dialogs in sequence (Process Event → Select Docs → Assign → Confirm). Each modal must be:
1. Detected as open by perception layer
2. Interacted with fully
3. Detected as closed before proceeding to next

**Risk:** If modal close is not detected, agent tries to interact with an element that is now behind the modal.

**Mitigation:** After every modal close action, perception layer verifies `blocking_modal: false` before planning next action.

---

### Challenge 6 — Tab Coordination (Medium)

Cases 2, 3, and 4 open PDFs in new browser tabs. The agent must:
1. Detect the new tab opened event
2. Switch to it for PDF byte capture
3. Return to the original tab for continued workflow

**Mitigation:** Playwright `BrowserContext` tracks all pages. Tab registry (already in POC) is preserved in new architecture as a working memory entry.

---

## Part 4 — Risks and Limitations

### Risk 1 — DPI Scaling Incompatibility

pywinauto UIA element finding works correctly at 100% DPI. At 125% or 150%:
- Element bounding rectangles are scaled incorrectly
- Click coordinates (when used) land on wrong targets

**Limitation:** Development machines with 4K displays must use 100% DPI or run automation in a VM at 100% DPI.
**Mitigation:** Document clearly, enforce via setup checklist. No code workaround available.

---

### Risk 2 — JS Injection Dependency

Case 4 Notifications uses `page.evaluate("ldCdNotifToggle(...)")` to toggle notification checkboxes. This works in the simulation because the JS function is known.

**In the real application:** The function name may differ or not exist. Agent must detect this and fall back to direct element clicking.

**Mitigation:** Wrap JS injection in try/except. If JS call fails, fall back to element click via pywinauto or Playwright.

---

### Risk 3 — Model Hallucination on Financial Data

VLM models can hallucinate numbers. A model that reads "10,640" may output "10,640.00" or "10,046.00" — both plausible-looking but one is wrong.

**Mitigation:** Never trust VLM output for financial values without cross-validation:
1. Extract from native PDF text if available (pdfplumber)
2. If OCR-only, require two independent extractions to agree
3. If mismatch, route to HITL

---

### Risk 4 — Memory Grows Unboundedly

ChromaDB accumulates patterns over time. After 1000 task runs, the knowledge store may have conflicting or stale patterns that lead to wrong decisions.

**Mitigation:**
- Pattern age-out: mark patterns not validated in 60 days as "stale"
- App version tracking: invalidate patterns when app version changes
- Manual review: monthly review of top-used patterns for correctness

---

### Risk 5 — CPU Inference Too Slow for Complex Cases

Case 4 (12 stages, ~50 actions) at 40 seconds per LLM call = 33 minutes worst-case. With caching this reduces, but early runs before cache warm-up are slow.

**Limitation:** Development testing of full Case 4 pipeline will be slow on CPU. Accept this as a development constraint — use a subset of stages for rapid testing.
**Mitigation:** Build a "fast mode" that skips perception for known deterministic steps during development.

---

### Risk 6 — File Lock Deadlock

If an agent acquires a `.lock` file and crashes before releasing it, the lock persists indefinitely.

**Mitigation:**
```python
# Lock files include timestamp and agent_id
# lock_content = {"agent_id": "agent_01", "acquired_at": "2026-05-12T10:00:00", "ttl_minutes": 10}
# Any agent can steal a lock older than TTL minutes
```

---

## Part 5 — Blockers (Must Resolve Before Phase 1)

| # | Blocker | Resolution Required |
|---|---------|-------------------|
| B1 | RecoveryHandler not scheduled in roadmap | Add to Phase 0 — implement basic recovery before browser automation |
| B2 | Task assignment mechanism undefined | Define SQLite-based task queue before 3-agent testing (Phase 5) |
| B3 | RDP keep-alive implementation missing | Implement background thread in Phase 3 before any RDP testing |
| B4 | HITL pause/resume mechanism unspecified | Define checkpoint schema and pause protocol in Phase 0 |
| B5 | Ollama health check missing | Add startup validation — fail fast if model is not loaded |
| B6 | Model version pinning | Replace `:latest` tags with specific version tags in .env |
| B7 | SQLite checkpoint schema undefined | Define schema in Phase 0 — all phases depend on it |
| B8 | ChromaDB pattern retrieval workflow | Specify query logic before Phase 4 (memory completion) |

---

## Part 6 — Recommended Implementation Approach

### Principle 1 — Hybrid Deterministic + Dynamic

Do not replace all deterministic code with LLM decisions. Use LLM only for:
- Initial state assessment (what am I looking at?)
- Novel situation handling (never seen this before)
- Conflict resolution (multiple valid actions)

Use deterministic code for:
- All known selectors (from rdweb.py locators — reuse from POC)
- All form fills with known values
- All financial comparisons and validations

```
POC locators (rdweb.py) ─────────────────────────► New architecture
  data-testid selectors                              Playwright executor
  Case-specific constants                            config/task YAML
  Validation rules                                   deterministic validators
```

### Principle 2 — Migrate POC Cases Incrementally

```
Phase 2: Case 1 + Case 2 (browser only — no RDP/File Explorer)
Phase 3: Case 3 (adds File Explorer + IIM — cross-context handoff)
Phase 4: Case 4 (full complexity — all systems, all modal types)
```

Do not attempt Case 4 integration until Cases 1–3 are stable in the new architecture.

### Principle 3 — Reuse POC Locators Directly

The `rdweb.py` locators file from the POC is the most valuable artefact. Import it directly into the new project under `config/locators/`. Do not rewrite selectors — they are proven against the real application (or simulation).

### Principle 4 — Cache-First, LLM-Second

Every agent action must first query ChromaDB for a cached pattern. LLM is called only on cache miss. This is the single biggest lever for reducing inference latency on CPU.

### Principle 5 — Human Approval Gate on All Write Actions

No form submit, modal save, or financial write proceeds without HITL approval in the first 10 task runs of each case type. After 10 successful runs with consistent results, auto-approval can be enabled per action type.

---

## Part 7 — Required Architectural Changes for Future Scalability

### Change 1 — Task Queue (Required for Phase 5+)

Replace manual task assignment with a SQLite-based queue:

```sql
CREATE TABLE task_queue (
    id          INTEGER PRIMARY KEY,
    task_type   TEXT NOT NULL,
    payload     JSON NOT NULL,
    status      TEXT DEFAULT 'pending',  -- pending | running | done | failed
    agent_id    TEXT,
    claimed_at  TIMESTAMP,
    completed_at TIMESTAMP,
    result      JSON
);
```

Agent picks up work:
```python
# Atomic claim — SQLite WAL prevents double-assignment
with db.transaction():
    task = db.execute(
        "UPDATE task_queue SET status='running', agent_id=?, claimed_at=? "
        "WHERE id=(SELECT id FROM task_queue WHERE status='pending' LIMIT 1) "
        "RETURNING *", (agent_id, now())
    ).fetchone()
```

### Change 2 — Event Bus (Required for Phase 5+)

Inter-component communication inside the agent process needs a simple event bus:

```python
# Events: rdp_disconnected, hitl_pending, task_complete, confidence_low
# Components subscribe and react without tight coupling
agent_event_bus.on("rdp_disconnected", rdp_handler.on_disconnect)
agent_event_bus.on("hitl_pending", hitl_queue.enqueue)
```

### Change 3 — App Version Tracking (Required Before Real App Integration)

Before connecting to the real LD/IIM applications, implement version detection:

```python
# On every session start, extract app version from footer/header
# Store in session memory and use as ChromaDB pattern cache key
# If version changes: mark all cached patterns for that app as "needs_revalidation"
```

### Change 4 — Prometheus Metrics (Required for Phase 6+)

Expose per-agent metrics for production monitoring:

```
agent_task_duration_seconds{task_type, status}
agent_llm_calls_total{model, outcome}
agent_cache_hits_total{collection}
agent_hitl_escalations_total{reason}
agent_action_success_rate{action_type}
```

### Change 5 — Replace SQLite with PostgreSQL (Required for Scale Beyond MVP)

SQLite is correct for MVP (3 agents). For 10+ agents:
- WAL mode contention increases
- Cross-machine query is not supported
- Replace with PostgreSQL — schema migration is straightforward since tables are already normalized

---

## Appendix — Unsupported Scenarios (Documented Limitations)

| Scenario | Limitation | Workaround |
|----------|-----------|------------|
| Fully autonomous financial write without HITL | Not supported — by design | Maintain human approval gate |
| Mainframe/green-screen IIM (if confirmed) | pywinauto UIA cannot access mainframe UI | Use pyautogui + OCR as last resort, or mainframe API |
| Real-time collaborative editing of same claim by 2 agents | Not supported — race condition risk | Task queue ensures one agent per claim |
| Offline inference (no network to inference server) | Agent cannot perceive screen without VLM | Run Ollama locally as fallback |
| Chrome inside RDP via CDP | Unvalidated — may not work in all RDP configs | Fallback: use mss + pywinauto for browser-inside-RDP |
| Case 4 JS injection in real app | `ldCdNotifToggle` may not exist in real app | Detect + fallback to element click |
| Sub-second response time for development | Not achievable on CPU — 15–40s per LLM call | Accept as dev constraint; use GPU inference server |
