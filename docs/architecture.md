# System Architecture

## Core Concept

The agent runs a continuous observe → reason → act → store loop until the task is complete, blocked, or needs human input. It does not follow a fixed script — it reads the current screen state and decides what to do next at every step.

**Confirmed constraints (from feasibility analysis):**
- All LLM inference is on-premises only — Ollama (dev/CPU) or vLLM (prod/GPU)
- LD and IIM applications are browser-based — Playwright is the primary executor
- pywinauto scope is limited to: RDP window detection, File Explorer, native Win32 dialogs
- POC Cases 1–4 are all fully supportable — see [feasibility-analysis.md](feasibility-analysis.md)

**Design principles:**
- Cache-first, LLM-second: query ChromaDB for known pattern before calling VLM
- Deterministic code handles all known selectors and form fills — LLM handles only novel states
- Human approval gate required for all financial write actions in the first 10 runs per task type
- Every financial field extraction requires confidence ≥ 0.90 or routes to HITL

```
┌─────────────────────────────────────────────────────────────────┐
│                        AGENT LOOP                               │
│                                                                 │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌─────────┐  │
│   │  OBSERVE │───►│  REASON  │───►│   ACT    │───►│  STORE  │  │
│   └──────────┘    └──────────┘    └──────────┘    └────┬────┘  │
│        ▲                                               │        │
│        └───────────────────────────────────────────────┘        │
│                                                                 │
│  Loop exits when:                                               │
│    • Task marked complete                                       │
│    • Max steps exceeded (configurable, default 50)              │
│    • Unrecoverable error                                        │
│    • Confidence below threshold 3 times on same step            │
└─────────────────────────────────────────────────────────────────┘
```

---

## Full System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        AGENT PROCESS                                │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  PERCEPTION LAYER                                            │   │
│  │  mss (capture) → Pillow (preprocess) → Claude Vision (parse)│   │
│  │  Output: ScreenState JSON                                    │   │
│  └───────────────────────────┬──────────────────────────────────┘   │
│                              │ ScreenState                          │
│  ┌───────────────────────────▼──────────────────────────────────┐   │
│  │  REASONING LAYER                                             │   │
│  │  TaskGoal + ScreenState + Memory → Claude LLM → ActionPlan   │   │
│  │  Output: ActionPlan { type, target, value, confidence }      │   │
│  └───────────────────────────┬──────────────────────────────────┘   │
│                              │ ActionPlan                           │
│  ┌───────────────────────────▼──────────────────────────────────┐   │
│  │  EXECUTION LAYER (ActionRouter)                              │   │
│  │                                                              │   │
│  │  browser action  → Playwright executor                       │   │
│  │  desktop action  → pywinauto executor                        │   │
│  │  rdp action      → RDP handler + pywinauto                   │   │
│  │  file action     → File executor (pathlib / smb)             │   │
│  │  pdf action      → Extraction pipeline                       │   │
│  │  human_needed    → HITL queue                                │   │
│  └───────────────────────────┬──────────────────────────────────┘   │
│                              │ ActionResult                         │
│  ┌───────────────────────────▼──────────────────────────────────┐   │
│  │  MEMORY LAYER                                                │   │
│  │  Working (dict) + Session (SQLite) + Knowledge (ChromaDB)    │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
         │                          │                    │
         ▼                          ▼                    ▼
   HITL Dashboard             Audit Log            Observability
   (FastAPI local)         (NDJSON append)      (structlog + Rich)
```

---

## Module Breakdown

### agent/loop.py — Main Agent Loop

Responsibility: Orchestrate the observe → reason → act → store cycle.

```
AgentLoop
  ├── run(task: TaskGoal) → TaskResult
  ├── _observe() → ScreenState
  ├── _reason(screen: ScreenState) → ActionPlan
  ├── _act(plan: ActionPlan) → ActionResult
  ├── _store(plan, result) → None
  ├── _should_continue() → bool
  └── _handle_exit(reason: str) → TaskResult
```

Key behaviors:
- Tracks retry count per step — flags for HITL after 3 failed attempts at same action
- Tracks total loop count — hard-stops at MAX_LOOP_STEPS
- Calls recovery handler on unexpected screen states
- Writes checkpoint to session memory after every action

---

### agent/perception.py — Screen Understanding

Responsibility: Capture screen and produce structured description of current state.

**On-prem VLM call (Ollama dev / vLLM prod) — not external API:**
```python
client = OpenAI(base_url=settings.inference_url, api_key="ignored")
response = client.chat.completions.create(
    model=settings.model_name,
    messages=[{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64img}"}},
            {"type": "text", "text": PERCEPTION_PROMPT.format(
                task_goal=goal, last_action=last_action, expected=expected
            )},
        ],
    }],
    max_tokens=1024,
)
```

**Startup health check (required before any task):**
```python
def check_inference_server():
    try:
        client.models.list()  # fails fast if Ollama/vLLM not running
    except Exception:
        raise RuntimeError(f"Inference server not reachable at {settings.inference_url}. "
                           "Start Ollama with: ollama serve")
```

**Cache-first pattern (skip VLM if state is known):**
```python
# Query ChromaDB before calling VLM
cached = knowledge.query_ui_pattern(app=screen_context, description=element_desc)
if cached and cached.confidence >= 0.85:
    return cached.screen_state   # no VLM call — ~0.1s vs 15–40s on CPU
# else: call VLM
```

```
PerceptionLayer
  ├── capture(target: CaptureTarget) → PIL.Image
  │     targets: full_screen | window(title) | region(rect)
  ├── preprocess(image) → PIL.Image
  │     operations: crop padding, annotate for VLM, resize if needed
  └── understand(image, context: dict) → ScreenState
        calls: local VLM via OpenAI-compatible API (Ollama/vLLM)
        returns: ScreenState(
          app_type,          # browser | desktop | rdp | file_explorer | dialog | unknown
          state_summary,     # one sentence
          visible_elements,  # list of interactive elements found
          current_url,       # if browser
          error_present,     # True if error/warning visible
          blocking_modal,    # True if modal dialog blocking workflow
          task_progress,     # not_started | in_progress | blocked | complete
          confidence         # 0.0 – 1.0
        )
```

---

### agent/planner.py — Action Decision

Responsibility: Given current screen state and task goal, decide the single next action.

**Action prioritisation when multiple valid actions exist:**
```python
# Priority order (highest wins):
# 1. Error dismissal — if error_present, always handle first
# 2. Cached pattern match — if ChromaDB hit with confidence ≥ 0.85
# 3. Deterministic rule — if current_step matches known task sequence
# 4. LLM planning — only if above three produce no result
PRIORITY_ORDER = ["error_dismissal", "cached_pattern", "deterministic_rule", "llm_plan"]
```

**Financial field guard (non-negotiable):**
```python
FINANCIAL_FIELDS = {"amount", "rcv", "check_number", "loan_number", "claim_id"}

if plan.field_name in FINANCIAL_FIELDS and plan.confidence < 0.90:
    return ActionPlan(action_type="flag_for_human",
                      reason=f"Financial field confidence {plan.confidence} < 0.90")
```

**State validation after each action:**
```python
# After every action, re-capture screen and verify expected state
post_state = perception.understand(capture())
if post_state.task_progress == "blocked" and last_action.action_type != "wait":
    recovery.recover(post_state, memory)
```

**Confidence thresholds by action type:**
```
action_type       perception_min   planning_min   auto_execute
─────────────     ──────────────   ────────────   ────────────
read / extract    0.70             0.70           Yes (non-financial)
click (known)     0.85             0.80           Yes
click (novel)     0.85             0.75           HITL first time
type (text)       0.80             0.80           Yes
type (financial)  0.90             0.90           HITL always (first 10 runs)
submit / save     0.90             0.85           HITL always (first 10 runs)
```

```
ActionPlanner
  └── decide(screen: ScreenState, memory: AgentMemory, goal: TaskGoal) → ActionPlan
        ActionPlan(
          action_type,    # click | type | navigate | read | extract | wait | flag_human
          target,         # element description or selector
          value,          # text to type or value to extract
          reason,         # why this action was chosen
          confidence,     # 0.0 – 1.0
          fallback,       # alternative action if primary fails
          is_financial,   # bool — triggers confidence ≥ 0.90 requirement
          requires_hitl   # bool — human approval before execution
        )
```

Planning rules (enforced in prompt):
- One action per decision
- Never guess financial values — extract or flag_for_human
- If confidence < per-action threshold → flag_for_human
- If same step failed 3 times → flag_for_human
- If error_present on screen → error_dismissal before any other action
- After action: validate expected state before planning next

---

### agent/recovery.py — Unexpected State Handler

Responsibility: Detect and recover from non-happy-path screen states.

**Recovery is scheduled in Phase 0 — must be implemented before browser automation begins.**

**Recovery trigger conditions:**
```python
RECOVERY_TRIGGERS = {
    "session_expired":      re_login,          # detected by "Please log in" text
    "unexpected_dialog":    dismiss_dialog,     # blocking_modal=True, unrecognised
    "element_not_found":    scroll_and_retry,   # 3 consecutive selector failures
    "page_still_loading":   wait_and_retry,     # task_progress="not_started" after action
    "wrong_page":           navigate_back,      # current_url not matching expected
    "rdp_disconnected":     reconnect_rdp,      # RDP window HWND invalid
    "repeated_failure":     flag_for_human,     # same step failed 3+ times
}
```

**Recovery timeout and backoff:**
```python
RECOVERY_TIMEOUTS = {
    "dismiss_dialog":   2,    # seconds
    "scroll_retry":     3,
    "wait_retry":       5,
    "navigate_back":    5,
    "re_login":        30,
    "reconnect_rdp":   60,
}
# Exponential backoff: attempt 1 = base, attempt 2 = base×2, attempt 3 = base×4
```

**Session expiry detection:**
```python
SESSION_EXPIRED_INDICATORS = [
    "please log in", "session has expired", "your session",
    "401", "403", "authentication required"
]
# Check: screen_state.state_summary or current_url contains any indicator
```

```
RecoveryHandler
  ├── detect(screen: ScreenState) → RecoveryNeeded | None
  └── recover(issue: RecoveryNeeded, memory: AgentMemory) → RecoveryResult

Recovery sequence (tried in order):
  1. dismiss_dialog       — close unexpected popup / modal
  2. scroll_into_view     — element may be off screen
  3. wait_and_retry       — page still loading (up to 5s wait)
  4. navigate_back        — return to last known good URL/state
  5. re_login             — session expired (use stored credentials)
  6. reconnect_rdp        — RDP session disconnected
  7. flag_for_human       — cannot recover automatically after all attempts
```

---

### executors/browser.py — Playwright Actions

Responsibility: Execute browser-based actions using Playwright.

```
BrowserExecutor
  ├── navigate(url: str) → ActionResult
  ├── click(selector: str | description: str) → ActionResult
  ├── type_text(selector: str, value: str) → ActionResult
  ├── select_option(selector: str, value: str) → ActionResult
  ├── read_table(selector: str) → list[dict]
  ├── extract_text(selector: str) → str
  ├── wait_for(selector: str, state: str, timeout: int) → ActionResult
  ├── download_file(trigger_selector: str) → Path
  └── screenshot(name: str) → Path

Selector strategy (tried in order):
  1. data-testid attribute
  2. aria-label
  3. name attribute
  4. LLM-generated CSS selector from element description
  5. Flag for human if none found
```

---

### executors/desktop.py — pywinauto Actions

Responsibility: Execute Windows desktop app actions via accessibility tree.

```
DesktopExecutor
  ├── find_window(title: str | class_name: str) → Window
  ├── click_element(window, control_type, name, automation_id) → ActionResult
  ├── type_text(window, control_type, name, value) → ActionResult
  ├── read_element(window, control_type, name) → str
  ├── select_item(window, control_type, name, item) → ActionResult
  └── get_window_screenshot(window) → PIL.Image

Element finding strategy:
  Primary: AutomationId (most stable, survives app updates)
  Fallback: Name property + ControlType combination
  Last resort: ClassName (fragile, avoid)
```

---

### executors/rdp.py — RDP Session Management

Responsibility: Launch and manage RDP/RemoteApp sessions.

**Keep-alive implementation (background thread):**
```python
class RDPKeepAlive(threading.Thread):
    INTERVAL_SECONDS = 240   # 4 minutes

    def run(self):
        while self._active:
            time.sleep(self.INTERVAL_SECONDS)
            hwnd = self._find_rdp_window()
            if hwnd:
                # Move mouse 1px within RDP window bounds — keeps session alive
                rect = hwnd.rectangle()
                mid = (rect.left + rect.width() // 2, rect.top + 10)
                win32api.SetCursorPos(mid)
            else:
                self.agent_event_bus.emit("rdp_disconnected")
```

**Disconnect detection:**
```python
def detect_disconnect(self) -> bool:
    hwnd = self._find_rdp_window()
    if hwnd is None:
        return True
    # Check window is not a zombie (has valid title, not "Disconnected")
    title = hwnd.window_text()
    return "disconnected" in title.lower() or "remote desktop connection" in title.lower()
```

**Reconnect with backoff:**
```python
def reconnect(self, session: RDPSession, max_attempts: int = 3) -> bool:
    for attempt in range(max_attempts):
        time.sleep(10 * (2 ** attempt))   # 10s, 20s, 40s
        subprocess.Popen(["mstsc.exe", str(session.rdp_file)])
        if self.wait_for_connection(timeout=30):
            return True
    return False  # escalate to HITL
```

**Connection timeout:**
```python
DEFAULT_CONNECTION_TIMEOUT = 30   # seconds to wait for RemoteApp window to appear
```

```
RDPHandler
  ├── launch(rdp_file: Path | host: str) → RDPSession
  ├── wait_for_connection(timeout: int = 30) → bool
  ├── get_active_windows() → list[Window]
  ├── find_remoteapp_window(app_name: str) → Window
  ├── capture_rdp_screen(window: Window) → PIL.Image
  ├── start_keep_alive() → RDPKeepAlive   # starts background thread
  ├── detect_disconnect() → bool          # checks HWND validity + window title
  ├── reconnect(session, max_attempts=3) → bool   # exponential backoff
  └── close(session: RDPSession) → None

Keep-alive: background thread, mouse move every 4 minutes
Disconnect: checked every 30 seconds via HWND + window title
Reconnect: max 3 attempts, 10s/20s/40s backoff, HITL if all fail
```

---

### executors/file_ops.py — File System Operations

Responsibility: Handle file and folder operations on local and network paths.

```
FileExecutor
  ├── read_text(path: Path) → str
  ├── read_excel(path: Path, sheet: str) → list[dict]
  ├── read_pdf(path: Path) → ExtractionResult
  ├── copy_to_local(network_path: Path) → Path     # copy before processing
  ├── write_file(path: Path, content: bytes) → ActionResult
  ├── list_directory(path: Path) → list[Path]
  ├── find_latest_file(directory: Path, pattern: str) → Path
  ├── acquire_lock(path: Path) -> bool              # .lock sentinel pattern
  └── release_lock(path: Path) -> None

File locking: write {filename}.lock before access, delete after.
Copy strategy: always copy network files to local temp before processing.
```

---

### executors/extraction.py — PDF and Document Pipeline

Responsibility: Extract structured data from documents with confidence scoring.

```
ExtractionPipeline
  └── extract(document: Path | bytes, fields: list[str]) → ExtractionResult
        ExtractionResult(
          values: dict[str, str],
          confidence: dict[str, float],
          method_used: str,       # native | tesseract | paddleocr | vision
          needs_review: bool
        )

Pipeline (each step only runs if previous confidence insufficient):
  Step 1: pdfplumber native text extraction
  Step 2: PyMuPDF render + Tesseract OCR
  Step 3: PaddleOCR (complex layouts)
  Step 4: Claude Vision (final attempt)
  Step 5: HITL (if still insufficient)
```

---

### memory/working.py — Task-Scoped Memory

```python
# In-process Python dict — zero latency
# NOTE: lost on crash — always paired with SQLite checkpoint after every action
working = {
    "task_id": "...",
    "task_type": "...",            # case1 | case2 | case3 | case4
    "goal": "...",
    "step": 0,
    "current_app": "browser",     # browser | desktop | rdp | file_explorer
    "current_url": "",             # last known browser URL
    "extracted_values": {},        # loan_no, borrower, amounts, etc.
    "open_tabs": [],               # Playwright page references (pdf tabs etc.)
    "rdp_session": None,           # active RDPSession reference
    "last_action": None,
    "last_result": None,
    "retry_counts": {},            # per-step retry tracking
    "decisions_log": [],           # every action + reason this task
    "hitl_pending": False,         # True while awaiting human review
}
```

**Crash recovery:** On agent startup, check SQLite `checkpoints` table for any `status='running'` task matching this `agent_id`. If found, restore `working` from last checkpoint JSON and resume from `step + 1`.

---

### memory/session.py — Agent Session Memory (SQLite)

**SQLite configuration:** WAL mode enabled — `PRAGMA journal_mode=WAL` — for concurrent read/write without blocking.

**Schema (versioned — increment `schema_version` on any change):**

```sql
CREATE TABLE schema_version (version INTEGER);

CREATE TABLE tasks (
    task_id       TEXT PRIMARY KEY,
    task_type     TEXT NOT NULL,          -- case1 | case2 | case3 | case4
    goal          TEXT,
    status        TEXT DEFAULT 'pending', -- pending | running | done | failed | hitl_wait
    agent_id      TEXT,
    started_at    TIMESTAMP,
    completed_at  TIMESTAMP,
    result_json   JSON
);

CREATE TABLE actions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT NOT NULL,
    step          INTEGER,
    action_type   TEXT,                   -- click | type | navigate | extract | flag_human
    target        TEXT,                   -- element description or selector used
    value         TEXT,                   -- value typed or extracted
    result_status TEXT,                   -- success | fail | skipped
    error_msg     TEXT,
    duration_ms   INTEGER,
    screenshot    TEXT,                   -- relative path to screenshot file
    timestamp     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE extractions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT NOT NULL,
    field_name    TEXT,
    raw_value     TEXT,
    normalized    TEXT,
    confidence    REAL,
    method        TEXT,                   -- native | tesseract | paddleocr | vlm | hitl
    source_doc    TEXT,
    is_financial  INTEGER DEFAULT 0,      -- 1 if financial field (higher confidence required)
    timestamp     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE checkpoints (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT NOT NULL,
    step          INTEGER,
    working_json  JSON,                   -- full working memory snapshot
    timestamp     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE hitl_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT NOT NULL,
    agent_id      TEXT,
    reason        TEXT,
    screenshot    TEXT,
    context_json  JSON,
    status        TEXT DEFAULT 'pending', -- pending | resolved | timeout
    resolution    JSON,                   -- human's correction/approval
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at   TIMESTAMP,
    timeout_at    TIMESTAMP               -- auto-escalate if not resolved by this time
);

CREATE TABLE task_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type     TEXT NOT NULL,
    payload       JSON NOT NULL,
    status        TEXT DEFAULT 'pending', -- pending | running | done | failed
    agent_id      TEXT,
    claimed_at    TIMESTAMP,
    completed_at  TIMESTAMP,
    result        JSON
);
```

---

### memory/knowledge.py — Long-term Pattern Store (ChromaDB)

**One ChromaDB instance per agent process — not thread-safe for concurrent writes.**
**Shared read access across agents is safe** (multiple readers, no writers during task).
**New patterns written only after task completion** — never mid-task.

**Collections and schemas:**

```python
# ui_patterns — known selectors per app element
# Document format:
{
    "app_name": "LD_Module",
    "app_version": "2.4",           # from footer/title bar detection
    "element_description": "claim search input field",
    "selector": "[data-testid='ld-search-input']",
    "action_type": "fill",
    "confidence": 0.92,
    "success_count": 14,
    "last_validated": "2026-05-12"
}
# Query: similarity search on element_description + app_name filter

# error_recoveries — known error states and resolutions
{
    "error_pattern": "session has expired",
    "app_name": "LD_Module",
    "recovery_action": "re_login",
    "success_rate": 0.97,
    "times_seen": 8,
    "last_seen": "2026-05-10"
}

# extraction_templates — document field locations
{
    "doc_type": "hold_check_pdf",
    "field": "check_amount",
    "extraction_hint": "look for $ symbol followed by number on line starting with 'Amount'",
    "method": "pdfplumber",
    "confidence_typical": 0.94
}

# task_templates — successful action sequences (after warm-up)
{
    "task_type": "case4_full",
    "stage": "claim_search",
    "action_sequence": ["navigate_to_search", "fill_loan_number", "click_search", "wait_results"],
    "average_steps": 4,
    "success_rate": 0.98
}
```

**Pattern lifecycle:**
```
New pattern inserted: confidence = 0.75
After 5 successful uses: promoted to 0.90
After 30 days unused: marked stale (re-validate on next use)
After app version change: all patterns for that app flagged needs_revalidation
```

**Retrieval flow:**
```python
def query_ui_pattern(app: str, element_desc: str) -> Pattern | None:
    results = collection.query(
        query_texts=[element_desc],
        where={"app_name": app, "confidence": {"$gte": 0.85}},
        n_results=1
    )
    if results and results[0].distance < 0.2:   # similarity threshold
        return results[0]
    return None   # cache miss → call VLM
```

---

### hitl/queue.py + hitl/server.py — Human Review

**HITL pause mechanism:**
```python
def flag_for_human(task_id, reason, screenshot, context) -> None:
    # 1. Write to hitl_queue with timeout_at = now + 30 minutes
    db.execute(
        "INSERT INTO hitl_queue (task_id, agent_id, reason, screenshot, context_json, timeout_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (task_id, agent_id, reason, screenshot_path, json.dumps(context),
         now() + timedelta(minutes=30))
    )
    # 2. Update task status
    db.execute("UPDATE tasks SET status='hitl_wait' WHERE task_id=?", (task_id,))

    # 3. Print to console (primary notification for MVP)
    print(f"\n[HITL REQUIRED] Task {task_id} needs human review.")
    print(f"  Reason: {reason}")
    print(f"  Open: http://localhost:8080/review/{task_id}")
    print(f"  Timeout: 30 minutes\n")

    # 4. Hold browser page open — keep Playwright page alive
    # Agent loop polls hitl_queue every 10 seconds
    while True:
        row = db.execute(
            "SELECT status, resolution FROM hitl_queue WHERE task_id=? ORDER BY id DESC LIMIT 1",
            (task_id,)
        ).fetchone()
        if row and row["status"] == "resolved":
            return json.loads(row["resolution"])
        if row and row["status"] == "timeout":
            raise HITLTimeoutError(f"Task {task_id} HITL timed out after 30 minutes")
        time.sleep(10)
```

**HITL resume from checkpoint:**
```python
def resume_from_hitl(task_id, resolution) -> None:
    # Load last checkpoint
    checkpoint = db.execute(
        "SELECT working_json FROM checkpoints WHERE task_id=? ORDER BY id DESC LIMIT 1",
        (task_id,)
    ).fetchone()
    working = json.loads(checkpoint["working_json"])
    # Apply human correction to working memory
    working["extracted_values"].update(resolution.get("corrections", {}))
    working["hitl_pending"] = False
    # Resume agent loop from working["step"] + 1
```

**Human input validation:**
```python
# Validate human correction before applying
# Financial fields must parse as valid decimal numbers
# Selector overrides must match known CSS selector format
# Approval without correction is always valid
```

**HITL timeout:** 30 minutes default. If human does not respond, task is marked `failed` with reason `hitl_timeout`. Operator must manually restart.

```
HITL Flow:
  Agent: low confidence / write action / 3 consecutive failures
    → hitl_queue INSERT (screenshot + context + reason + timeout_at)
    → task status = "hitl_wait"
    → browser page stays open (Playwright session held)
    → RDP session kept alive (keep-alive thread continues)
    → agent loop polls every 10s for resolution

  Human: opens http://localhost:8080
    → sees pending reviews (all agents) with screenshot + context + reason
    → provides approval or field corrections
    → submits resolution form

  Agent: resolution detected in poll
    → validates human input
    → applies corrections to working memory
    → resumes from last SQLite checkpoint (step + 1)
    → logs resolution to audit log
```

---

### config/ — Settings and Task Definitions

```
config/
├── settings.py          # Pydantic Settings model (reads from .env)
├── tasks/
│   ├── claim_search.yaml    # goal: search claim by loan number
│   ├── document_review.yaml # goal: review and accept claim documents
│   └── ...
```

Task definition format (YAML):
```yaml
task_id: claim_search
description: Search for a claim by loan number and validate claim details
success_criteria:
  - loan_number_matches: true
  - claim_status_visible: true
hitl_on_confidence_below: 0.75
max_steps: 30
requires_human_approval_for:
  - any_form_submit
  - any_financial_value_write
```

---

### Audit Log Schema (NDJSON — append-only)

One JSON object per line. Never modified after write.

```json
{
  "ts": "2026-05-12T10:23:45.123Z",
  "agent_id": "agent_01",
  "task_id": "case4-abc123",
  "task_type": "case4",
  "step": 7,
  "action_type": "type",
  "target": "loan number search field [data-testid='ld-search-input']",
  "value": "0156312522",
  "result": "success",
  "duration_ms": 312,
  "confidence": 0.94,
  "cache_hit": false,
  "llm_calls": 1,
  "screenshot": "screenshots/case4-abc123/07_claim_search.png",
  "error": null
}
```

Query examples:
```bash
# All failed actions for a task
jq 'select(.task_id=="case4-abc123" and .result=="fail")' audit/agent_01.ndjson

# All HITL escalations today
jq 'select(.action_type=="flag_for_human")' audit/agent_01.ndjson
```

---

## POC Case Compatibility (Confirmed)

All 4 existing POC cases are fully supported by this architecture. Migration uses existing rdweb.py locators directly — they are imported as `config/locators/rdweb.py`.

| Case | Browser | pywinauto | File Ops | OCR | Migration Complexity |
|------|---------|-----------|----------|-----|---------------------|
| Case 1 | None | None | PDF read | Yes | Low — backend only |
| Case 2 | Primary | None | None | Yes | Medium — 8 stages |
| Case 3 | Primary + IIM | File Explorer | PDF via network | Yes | High — cross-context |
| Case 4 | Primary | File Explorer | Excel + PDF | Yes | High — 12 stages |

**Migration order (recommended):** Case 1 → Case 2 → Case 3 → Case 4

**Key reuse from POC:**
- `automation/locators/rdweb.py` → `config/locators/rdweb.py` (copy directly)
- All Pydantic result schemas → `cases/` directory (copy and extend)
- OCR pipeline (`pdfplumber + Tesseract`) → `executors/extraction.py`
- rapidfuzz fuzzy matching → `executors/matchers.py`
- openpyxl Excel reader → `executors/file_ops.py`

---

## Data Flow for a Typical Task

```
1. Task loaded from YAML
        ↓
2. Agent loop starts
        ↓
3. Screen captured (mss)
        ↓
4. Claude Vision analyzes screenshot → ScreenState JSON
        ↓
5. Claude LLM plans next action given ScreenState + memory + goal → ActionPlan
        ↓
6. ActionRouter routes to correct executor (Playwright / pywinauto / file)
        ↓
7. Action executed → ActionResult (success/fail + new state data)
        ↓
8. Working memory updated, session memory checkpointed
        ↓
9. Audit log entry written (append-only)
        ↓
10. Back to step 3
```

---

## 3-Agent Parallel Operation (MVP)

Each agent is an independent process with its own:
- Working memory (in-process)
- SQLite database file (`agent_01.db`, `agent_02.db`, `agent_03.db`)
- Screenshot directory
- Log file
- HITL queue entries (same SQLite, different agent_id)

Shared (read-only during task):
- ChromaDB knowledge store
- Task YAML definitions
- .env configuration

No inter-agent coordination required for MVP. Each agent works on a separate task.
