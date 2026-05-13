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

## Phase 0 — Project Setup and Skeleton (Week 1–2)

**Goal:** Working project structure, environment configured, basic agent loop shell that compiles and runs.

### Tasks

- [ ] Create project folder structure (`agent/`, `executors/`, `memory/`, `hitl/`, `config/`, `tests/`)
- [ ] Set up Poetry with `pyproject.toml`
- [ ] Configure `.env.example` with all required variables
- [ ] Set up `config/settings.py` using Pydantic Settings
- [ ] Implement empty stub for each module (all methods raise `NotImplementedError`)
- [ ] Write `run_agent.py` entry point that reads a task YAML and starts AgentLoop
- [ ] Configure `structlog` for JSON-formatted logging
- [ ] Configure `Rich` for readable console output
- [ ] Set up pytest with one passing smoke test
- [ ] Initialize git repository with `.gitignore`
- [ ] Verify Python + Playwright + pywinauto + mss install cleanly on Windows

**Exit criteria:** `python run_agent.py --task config/tasks/smoke_test.yaml` runs without import errors.

---

## Phase 1 — Core Loop and Perception (Week 3–4)

**Goal:** Agent can look at the screen and describe what it sees. The loop skeleton runs end-to-end.

### Tasks

- [ ] Implement `memory/working.py` — in-process dict with typed access
- [ ] Implement `agent/loop.py` — observe → reason → act → store cycle with stub executors
- [ ] Implement `agent/perception.py` — mss capture + Pillow preprocess
- [ ] Integrate Claude Vision API — send screenshot, receive ScreenState JSON
- [ ] Define and validate `ScreenState` Pydantic model
- [ ] Implement `agent/planner.py` — Claude LLM action planning prompt
- [ ] Define and validate `ActionPlan` Pydantic model
- [ ] Implement confidence threshold check → route to HITL stub if below threshold
- [ ] Implement max loop step guard
- [ ] Write unit tests for perception and planner with mock Claude responses
- [ ] Log every perception result and action plan to audit log

**Exit criteria:** Agent loop runs, captures screen, calls Claude Vision, receives ScreenState, plans an action, logs it — all without executing the action yet (stub executor).

---

## Phase 2 — Browser Execution (Week 5–6)

**Goal:** Agent can fully automate browser-based workflows against simulation sites.

### Tasks

- [ ] Implement `executors/browser.py` — full Playwright executor
  - [ ] navigate, click, type, select, read_table, extract_text, wait_for, download
- [ ] Implement selector resolution strategy (data-testid → aria-label → LLM-generated)
- [ ] Implement `ActionRouter` — routes browser actions to BrowserExecutor
- [ ] Connect Playwright page lifecycle to AgentLoop (launch, reuse, close)
- [ ] Implement `memory/session.py` — SQLite with tasks, actions, checkpoints tables
- [ ] Implement checkpoint write after every action
- [ ] Write task YAML for: login flow, claim search, form fill
- [ ] Test end-to-end against LD simulation site (localhost)
- [ ] Test end-to-end against IIM simulation site (localhost)
- [ ] Implement screenshot capture on every action (stored to SCREENSHOT_DIR)
- [ ] Write integration tests for browser executor

**Exit criteria:** Agent completes a login → search → extract → validate workflow on simulation sites autonomously, with full audit log and session checkpoint.

---

## Phase 3 — Desktop and RDP Execution (Week 7–8)

**Goal:** Agent can launch RDP sessions and automate desktop apps and File Explorer.

### Tasks

- [ ] Implement `executors/rdp.py`
  - [ ] Launch mstsc.exe with .rdp file
  - [ ] Wait for connection and detect RemoteApp windows
  - [ ] Keep-alive thread (every 4 minutes)
  - [ ] Disconnect detection and reconnect logic
- [ ] Implement `executors/desktop.py` — pywinauto UIA executor
  - [ ] find_window, click_element, type_text, read_element, select_item
- [ ] Implement File Explorer automation (navigate folders, open files)
- [ ] Extend `ActionRouter` to route desktop and RDP actions
- [ ] Extend `agent/perception.py` to capture RDP window region
- [ ] Extend `agent/recovery.py` with RDP-specific recovery (session expired, reconnect)
- [ ] Test full flow: browser login → .rdp download → RDP launch → RemoteApp window → interact
- [ ] Write integration test: agent navigates File Explorer to a target folder

**Exit criteria:** Agent performs an end-to-end flow that crosses the browser → RDP boundary without human intervention.

---

## Phase 4 — PDF Extraction and Memory Completion (Week 9)

**Goal:** Agent extracts structured data from documents with confidence scoring. Memory tiers complete.

### Tasks

- [ ] Implement `executors/extraction.py`
  - [ ] pdfplumber native extraction
  - [ ] PyMuPDF + Tesseract OCR fallback
  - [ ] Claude Vision final pass
  - [ ] Confidence scoring per field
- [ ] Implement `memory/knowledge.py` — ChromaDB long-term store
  - [ ] Store successful UI patterns after each task
  - [ ] Query known patterns before calling Claude (cache hit = faster, cheaper)
- [ ] Implement `executors/file_ops.py` — complete file handler
  - [ ] read_excel, read_pdf, copy_to_local, find_latest_file
  - [ ] Lock/release pattern for network files
- [ ] Integrate extraction into agent loop — when PDF encountered, route to pipeline
- [ ] Write unit tests for each extraction step (mock documents)

**Exit criteria:** Agent reads an Excel file from a network path, reads a PDF, extracts fields with confidence scores, routes low-confidence results to HITL stub.

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
