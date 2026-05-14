# Todo List

Current phase: **Phase 3 — Desktop + RDP**

Last updated: 2026-05-13

---

## Completed — Phase 0 (Project Setup)

- [x] Create project folder structure
- [x] Initialize Poetry project (`pyproject.toml`)
- [x] Create `.env.example` with all config keys
- [x] Create `config/settings.py` using Pydantic Settings
- [x] Create module stub files (all methods raise `NotImplementedError`)
- [x] Create `run_agent.py` entry point (loads task YAML, supports `--skip-preflight`)
- [x] Set up structlog + Rich logging (`config/logging_config.py`)
- [x] Set up pytest with smoke test
- [x] Initialize git repository (initial commit `73013b8`)
- [x] Add `.gitignore` for Python, `.env`, screenshots, downloads
- [ ] Verify all dependencies install cleanly on Windows dev machine — pending (current dev box is macOS)

---

## Completed — Phase 1 (Core Loop + Vision)

- [x] `memory/working.py` — typed in-process working memory
- [x] `agent/loop.py` — observe → reason → act → store cycle with stub executor
- [x] `agent/perception.py` — mss capture + Pillow preprocess + local VLM call
- [x] Local VLM integration — ScreenState JSON (Ollama/vLLM via OpenAI client; no external APIs per CLAUDE.md)
- [x] `agent/planner.py` — ActionPlan via local LLM
- [x] `agent/schemas.py` — ScreenState / ActionPlan / ActionResult Pydantic models
- [x] Confidence threshold check → HITL routing (`SessionMemory.write_hitl`)
- [x] Max loop step guard (`settings.max_loop_steps`)
- [x] Retry-limit guard → forced `flag_human`
- [x] Audit log writer (`agent/audit.py`, append-only NDJSON)
- [x] Unit tests for schemas / perception / planner / loop (mocked VLM)
- [ ] Real-screen end-to-end validation on Windows box w/ Ollama — pending hardware

Notes / assumptions:
- Roadmap originally said "Claude Vision API"; CLAUDE.md non-negotiable forbids external LLMs.
  Phase 1 is implemented against the local OpenAI-compatible endpoint (Ollama dev, vLLM prod).
- `executors/*` and `memory/knowledge.py` remain stubs; `StubExecutor` returns `deferred`.
- `mss` only imports inside `PerceptionLayer.capture()` — keeps unit tests runnable headless.

---

## Completed — Phase 2 (Browser)

- [x] `executors/browser.py` full Playwright implementation (`BrowserSession` + `BrowserExecutor`)
- [x] Selector resolution strategy (`executors/selectors.py`) — testid → aria → name → text → fallback
- [x] `ActionRouter` browser routing (`agent/router.py`)
- [x] `memory/session.py` extended (start/complete/log_action/log_extraction/get_actions)
- [x] Checkpoint + action-log write after every step
- [x] Task YAMLs: `login.yaml`, `claim_search.yaml`, `form_fill.yaml`
- [x] Deterministic step-list task mode (runs YAML without the LLM — used for CI)
- [x] Starter `config/locators/rdweb.py` (LOGIN + CLAIM_SEARCH + FORM + ALL aggregate)
- [x] Vendored sim HTML pages under `tests/sim/pages/` (login / claim_search / form)
- [x] Integration tests against the vendored sim — real Chromium, both LD-shaped and IIM-shaped flows
- [x] Full-loop deterministic e2e run via `run_agent.py` (`config/tasks/claim_search.yaml` returns `status=success`)

Notes / blockers carried forward:
- The full 120+ POC locator map lands when `insurance-agent-project` is available; current `rdweb.py` is a starter for the vendored sim only.
- "LD simulation" / "IIM simulation" external servers from the roadmap don't exist in this repo; vendored sim pages cover the same shape and unblock Phase 2 acceptance.
- Real-application URLs (production LD/IIM) still need confirmation — flagged in "Decisions Needed".

---

## Active — Phase 3 (Desktop + RDP)

- [ ] `executors/rdp.py` — mstsc launch + RemoteApp detection
- [ ] RDP keep-alive thread
- [ ] Disconnect detection + reconnect
- [ ] `executors/desktop.py` — pywinauto UIA
- [ ] `executors/file_ops.py` — File Explorer automation
- [ ] ActionRouter desktop / rdp routing
- [ ] Extend ActionPlan schema with desktop/rdp action types
- [ ] `agent/perception.py` — RDP window region capture (bbox-targeted mss grab)
- [ ] `agent/recovery.py` — RDP-specific recovery (session expired, reconnect, focus loss)
- [ ] Task YAML for browser→RDP handoff (`config/tasks/rdp_launch.yaml`)
- [ ] Mock-based unit tests for desktop/rdp/file_ops (Windows-only at runtime — see `docs/assumptions.md`)

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
