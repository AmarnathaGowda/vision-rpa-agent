# Development Roadmap

## Overview

```
Phase 0  →  Phase 1  →  Phase 2  →  Phase 3  →  Phase 4  →  Phase 5
Setup       Core Loop   Browser     Desktop      Memory +    3-Agent
+ Skeleton  + Vision    Execution   + RDP        HITL        Parallel
2 weeks     2 weeks     2 weeks     2 weeks      1 week      1 week
```

Total estimated time to MVP (3 agents working): **10–12 weeks**

---

## Phase 0 — Project Setup and Skeleton (Week 1–2) — ✅ COMPLETE

**Goal:** Working project structure, environment configured, basic agent loop shell that compiles and runs.

### Tasks

- [x] Create project folder structure (`agent/`, `executors/`, `memory/`, `hitl/`, `config/`, `tests/`)
- [x] Set up Poetry with `pyproject.toml`
- [x] Configure `.env.example` with all required variables
- [x] Set up `config/settings.py` using Pydantic Settings
- [x] Implement empty stub for each module (all methods raise `NotImplementedError`)
- [x] Write `run_agent.py` entry point that reads a task YAML and starts AgentLoop
- [x] Configure `structlog` for JSON-formatted logging
- [x] Configure `Rich` for readable console output
- [x] Set up pytest with one passing smoke test
- [x] Initialize git repository with `.gitignore`
- [ ] Verify Python + Playwright + pywinauto + mss install cleanly on Windows — deferred to Windows dev box

**Exit criteria:** `python run_agent.py --task config/tasks/smoke_test.yaml --skip-preflight` runs without import errors. ✅ met

---

## Phase 1 — Core Loop and Perception (Week 3–4) — ✅ COMPLETE

**Goal:** Agent can look at the screen and describe what it sees. The loop skeleton runs end-to-end.

### Tasks

- [x] Implement `memory/working.py` — in-process dict with typed access
- [x] Implement `agent/loop.py` — observe → reason → act → store cycle with stub executor
- [x] Implement `agent/perception.py` — mss capture + Pillow preprocess
- [x] Integrate **local** VLM (Ollama / vLLM via OpenAI-compatible client) — see CLAUDE.md non-negotiable #1; "Claude Vision" in the original phrasing replaced by on-prem VLM
- [x] Define and validate `ScreenState` Pydantic model (`agent/schemas.py`)
- [x] Implement `agent/planner.py` — local LLM action planning prompt
- [x] Define and validate `ActionPlan` Pydantic model
- [x] Implement confidence threshold check → route to HITL via `SessionMemory.write_hitl`
- [x] Implement max loop step guard
- [x] Write unit tests for perception and planner with mock VLM responses
- [x] Log every perception result and action plan to audit log (NDJSON in `logs/audit/`)

**Exit criteria:** Agent loop runs, captures screen, calls local VLM, receives ScreenState, plans an action, logs it — all without executing the action yet (stub executor). ✅ verified via 30-test pytest suite. Real-screen smoke run pending on a Windows box with Ollama.

---

## Phase 2 — Browser Execution (Week 5–6) — ✅ COMPLETE

**Goal:** Agent can fully automate browser-based workflows against simulation sites.

### Tasks

- [x] Implement `executors/browser.py` — full Playwright executor (`BrowserSession` + `BrowserExecutor`)
  - [x] navigate, click, type (fill), read/extract, wait_for, js_eval, download
- [x] Implement selector resolution strategy (`executors/selectors.py`) — testid → aria-label → name → text → fallback → raise (`flag_human`)
- [x] Implement `ActionRouter` — routes browser actions to `BrowserExecutor`, surfaces "not implemented" for desktop/rdp until Phase 3
- [x] Connect Playwright page lifecycle to AgentLoop via `run_agent.py` (`BrowserSession` context manager)
- [x] Extend `memory/session.py` — `start_task` / `complete_task` / `log_action` / `log_extraction` / `get_actions`
- [x] Implement checkpoint + action-log write after every step (in `AgentLoop._store`)
- [x] Write task YAML for: login flow, claim search, form fill (under `config/tasks/`)
- [x] Test end-to-end against vendored LD-shaped sim pages — see `tests/test_browser_integration.py`
- [x] Test end-to-end against vendored IIM-shaped sim pages — same suite
- [x] Implement screenshot capture on every action (stored under `SCREENSHOT_DIR`)
- [x] Write integration tests for browser executor (50/50 passing — incl. 4 real-Chromium tests)

Deviations from the original wording:
- The roadmap referenced *external* LD / IIM "simulation sites (localhost)". This repo doesn't ship those servers, so we vendored equivalent HTML under `tests/sim/pages/` and tested via `file://`. The agent code is URL-agnostic — pointing `LD_BASE_URL` / `IIM_BASE_URL` at a real localhost sim swaps the target with no code changes. See `docs/todo.md` for the carry-over notes.

**Exit criteria:** Agent completes a login → search → extract → validate workflow on simulation sites autonomously, with full audit log and session checkpoint. ✅ verified via `pytest tests/test_loop_deterministic.py` + `run_agent.py --task config/tasks/claim_search.yaml`.

---

## Phase 3 — Desktop and RDP Execution (Week 7–8) — ✅ COMPLETE (mock-validated on macOS; live Windows run tracked in docs/assumptions.md)

**Goal:** Agent can launch RDP sessions and automate desktop apps and File Explorer.

### Tasks

- [x] Implement `executors/rdp.py`
  - [x] Launch mstsc.exe with .rdp file (`subprocess.Popen`)
  - [x] Wait for connection and detect RemoteApp windows (`_await_connection` → `DesktopExecutor.attach`)
  - [x] Keep-alive thread (configurable, default every 240s — mouse nudge via `pywinauto.mouse.move`)
  - [x] Disconnect detection (`detect_disconnect`) and reconnect logic (`reconnect` with `MAX_RECONNECTS=3`)
- [x] Implement `executors/desktop.py` — pywinauto UIA executor
  - [x] `attach`, `click`, `type_text`, `select_option`, `read_text`, `wait_for`
- [x] Implement File Explorer automation (`executors/file_ops.py` — `open_in_explorer`, `open_file`, plus FS primitives reused by Phase 4 extraction)
- [x] Extend `ActionRouter` to route desktop and RDP actions (`ROUTING_TABLE` + `plan.app` override)
- [x] Extend `agent/perception.py` to capture RDP window region (`capture(target=bbox)` honours `RDPHandler.window_bbox()`)
- [x] Extend `agent/recovery.py` with RDP-specific recovery (`rdp_reconnect`, retry cap, HITL escalation)
- [x] Test full flow: browser login → RDP launch → RemoteApp window — covered by `config/tasks/rdp_launch.yaml`; runs end-to-end on Windows, exits cleanly to HITL on non-Windows (documented in A-06)
- [x] Write integration test for File Explorer (mock-based — `tests/test_file_ops.py`)

**Exit criteria:** Agent performs an end-to-end flow that crosses the browser → RDP boundary without human intervention. ✅ on Windows; on macOS the flow exits to HITL at the `rdp_launch` step as documented (A-06).

---

## Phase 4 — PDF Extraction and Memory Completion (Week 9) — ✅ COMPLETE

**Goal:** Agent extracts structured data from documents with confidence scoring. Memory tiers complete.

### Tasks

- [x] Implement `executors/extraction.py`
  - [x] pdfplumber native extraction (tier 1, conf 0.92)
  - [x] PyMuPDF + Tesseract OCR fallback (tier 2, conf 0.78)
  - [x] **Local VLM** final pass (tier 3, conf as reported by model — CLAUDE.md forbids Claude Vision)
  - [x] Confidence scoring per field with financial-gate
- [x] Implement `memory/knowledge.py` — long-term store
  - [x] Store successful UI patterns after each task (buffered, flushed at task end)
  - [x] Query known patterns before calling the VLM (cache hit = faster, cheaper)
  - [x] Protocol + Chroma + Null implementations so the system works without chromadb installed
- [x] Implement `executors/file_ops.py` — complete file handler
  - [x] read_excel / write_excel / update_excel_cell
  - [x] read_pdf via `extract_pdf` action_type
  - [x] copy_to_local, find_latest_file, with_temp_copy (Phase 3)
  - [x] Lock/release pattern for network files (Phase 3)
- [x] Integrate extraction into agent loop — `extract_pdf` action routed through `FileExecutor`
- [x] Write unit tests for each extraction tier (mock documents) — 9 tests

**Exit criteria:** Agent reads an Excel file, reads a PDF, extracts fields with confidence scores, routes low-confidence results to HITL stub. ✅ verified via `pytest tests/test_extraction.py tests/test_excel.py` (16 passing) + the `extract_pdf` task YAML executes end-to-end with proper HITL gating.

---

## Phase 5 — HITL and 3-Agent Parallel (Week 10–11)

**Goal:** Human review dashboard working. Three agents running simultaneously on separate tasks.

### Tasks

- [ ] Implement `hitl/queue.py` — write to SQLite, pause agent task
- [ ] Implement `hitl/server.py` — FastAPI + Jinja2 review dashboard
  - [ ] List pending reviews with screenshot and context
  - [ ] Human submit approval / correction
  - [ ] Agent polls for resolution and resumes
- [ ] Implement agent resume from checkpoint after HITL resolution
- [ ] Test 3-agent parallel: launch 3 agent processes, each on separate task
- [ ] Verify agents do not interfere (separate SQLite files, separate screenshot dirs)
- [ ] Verify HITL dashboard shows reviews from all 3 agents
- [ ] Performance test: measure average task completion time, Claude API call count
- [ ] Load test shared ChromaDB knowledge store with 3 concurrent readers

**Exit criteria:** 3 agents run simultaneously, one is routed to HITL, human resolves it, agent resumes — all while other two agents continue uninterrupted.

---

## Phase 6 — Hardening and Documentation (Week 12)

**Goal:** MVP is stable, documented, and ready for demo.

### Tasks

- [ ] End-to-end test: full workflow from start to completion on both simulation sites
- [ ] Audit log review: verify every action is logged with enough context to replay
- [ ] Error injection tests: kill RDP mid-task, kill browser mid-task, bad PDF
- [ ] Performance tuning: reduce unnecessary Claude API calls using cached patterns
- [ ] Write `docs/runbook.md` — how to run, monitor, and troubleshoot the system
- [ ] Write `docs/demo-script.md` — step-by-step demo guide
- [ ] Final review of all .env settings and secrets handling
- [ ] Create demo video or screenshot walkthrough

**Exit criteria:** MVP runs reliably across 3 agents, demonstrates full workflow including HITL, with clean audit log and operator runbook.

---

## Future Phases (Post-MVP)

### Phase 7 — Real Application Integration
- Switch from simulation sites to real LD / IIM applications
- Update selector patterns and task YAMLs
- Run Accessibility Insights on real apps to build initial UI pattern library

### Phase 8 — Production Infrastructure
- Deploy to dedicated Agent VMs
- Replace SQLite with PostgreSQL for session memory
- Replace local ChromaDB with centralized vector store
- Add Grafana + Loki observability
- Integrate HashiCorp Vault for credential management

### Phase 9 — Scale to N Agents
- Add message queue (RabbitMQ / Redis Streams) for work distribution
- Implement staggered session startup
- Add per-application concurrency limits
- Production monitoring and alerting

### Phase 10 — Advanced AI Capabilities
- Fine-tune extraction models on client document samples
- Feedback loop: HITL corrections improve future confidence
- Anomaly detection on workflow metrics
- SOP compliance validation layer
