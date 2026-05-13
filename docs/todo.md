# Todo List

Current phase: **Phase 0 — Project Setup**

Last updated: 2026-05-12

---

## Active — Phase 0 (Do Now)

- [ ] Create project folder structure
- [ ] Initialize Poetry project (`pyproject.toml`)
- [ ] Create `.env.example` with all config keys
- [ ] Create `config/settings.py` using Pydantic Settings
- [ ] Create module stub files (all methods raise `NotImplementedError`)
- [ ] Create `run_agent.py` entry point
- [ ] Set up structlog + Rich logging
- [ ] Set up pytest with smoke test
- [ ] Initialize git repository
- [ ] Add `.gitignore` for Python, `.env`, screenshots, downloads
- [ ] Verify all dependencies install cleanly on Windows dev machine

---

## Up Next — Phase 1 (Core Loop + Vision)

- [ ] `memory/working.py` — typed in-process working memory
- [ ] `agent/loop.py` — observe → reason → act → store skeleton
- [ ] `agent/perception.py` — mss capture + Pillow preprocess
- [ ] Claude Vision integration — ScreenState JSON
- [ ] `agent/planner.py` — ActionPlan via Claude LLM
- [ ] Confidence threshold check → HITL routing
- [ ] Max loop step guard
- [ ] Audit log writer
- [ ] Unit tests for perception and planner

---

## Queued — Phase 2 (Browser)

- [ ] `executors/browser.py` full Playwright implementation
- [ ] Selector resolution strategy
- [ ] `ActionRouter` browser routing
- [ ] `memory/session.py` SQLite implementation
- [ ] Checkpoint write after every action
- [ ] Task YAMLs: login, claim search, form fill
- [ ] Integration test against LD simulation
- [ ] Integration test against IIM simulation

---

## Queued — Phase 3 (Desktop + RDP)

- [ ] `executors/rdp.py` — mstsc launch + RemoteApp detection
- [ ] RDP keep-alive thread
- [ ] Disconnect detection + reconnect
- [ ] `executors/desktop.py` — pywinauto UIA
- [ ] File Explorer automation
- [ ] ActionRouter desktop routing
- [ ] RDP perception (window region capture)
- [ ] Recovery for RDP-specific failures

---

## Queued — Phase 4 (PDF + Memory)

- [ ] `executors/extraction.py` — full pipeline
- [ ] `memory/knowledge.py` — ChromaDB
- [ ] `executors/file_ops.py` — file locking, Excel, copy-to-local

---

## Queued — Phase 5 (HITL + 3 Agents)

- [ ] `hitl/queue.py`
- [ ] `hitl/server.py` dashboard
- [ ] Agent checkpoint resume after HITL
- [ ] 3-agent parallel test
- [ ] HITL dashboard multi-agent view

---

## Queued — Phase 6 (Hardening)

- [ ] Full end-to-end test
- [ ] Error injection tests
- [ ] Performance tuning
- [ ] `docs/runbook.md`
- [ ] `docs/demo-script.md`
- [ ] Demo recording

---

## Decisions Needed

- [ ] Confirm whether real LD application is browser-based or desktop app
- [ ] Confirm whether real IIM application type (web / WinForms / Java / mainframe)
- [ ] Confirm App VM IP and RD Web URL for integration testing
- [ ] Confirm whether Claude API can be used during demo, or needs on-prem LLM
- [ ] Confirm ANTHROPIC_API_KEY availability for dev environment

---

## Known Risks to Monitor

- pywinauto UIA backend requires Windows — verify dev machine is Windows before Phase 3
- mss screenshot speed degrades if display scaling is not 100% — configure before Phase 1
- ChromaDB may need `chroma_db_impl=duckdb+parquet` config on Windows — test in Phase 4
- FastAPI HITL server port 8080 may conflict with other services — confirm early
