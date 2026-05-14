# Todo List

Current phase: **Phase 6 — Hardening** (Phase 5 complete)

Last updated: 2026-05-14

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

## Completed — Phase 3 (Desktop + RDP)

- [x] `executors/rdp.py` — mstsc launch via `subprocess.Popen`, RemoteApp window detection via `DesktopExecutor.attach()`
- [x] RDP keep-alive thread (daemon, mouse-nudge every `keepalive_seconds`)
- [x] Disconnect detection (`detect_disconnect`) + reconnect with attempt counter and `MAX_RECONNECTS=3`
- [x] `executors/desktop.py` — `DesktopExecutor` with `attach / click / type_text / select_option / read_text / wait_for`, pywinauto-UIA backend, lazy import (loads cleanly on macOS)
- [x] `executors/file_ops.py` — `FileExecutor` with `copy_to_local / find_latest_file / acquire_lock / release_lock / open_in_explorer / open_file / with_temp_copy`
- [x] ActionRouter desktop / rdp / file routing (`ROUTING_TABLE` + `plan.app` override)
- [x] `ActionPlan` extended with `select_option / file_navigate / file_open / rdp_launch / rdp_reconnect / rdp_disconnect` action types and an `app` scope field
- [x] `agent/perception.py` — `capture(target=bbox)` already accepts an mss bbox dict; RDP window bbox surfaced via `RDPHandler.window_bbox()`
- [x] `agent/recovery.py` — `RecoveryHandler` returns `RecoveryDirective(retry / skip / rdp_reconnect / hitl / abort)` for blocking modals, error screens, RDP disconnects, transient errors, reconnect-limit, and unknown failures
- [x] Hard retry cap added to `AgentLoop._store` — deterministic-mode steps now route to HITL after `RETRY_LIMIT=3` repeated failures (closes the infinite-retry hole found during validation)
- [x] Task YAML for browser→RDP handoff (`config/tasks/rdp_launch.yaml`)
- [x] Mock-based unit tests for desktop/rdp/file_ops (44 new tests; pywinauto + subprocess fully mocked — Windows runtime validation tracked in `docs/assumptions.md` A-06 / A-08 / A-09 / A-10 / A-11)

End-to-end validation on macOS dev box:
- `pytest tests/` → **94 passed** (50 from Phases 0-2 + 44 new).
- Phase 2 regression: `LD_BASE_URL=file://.../sim/pages python run_agent.py --task config/tasks/claim_search.yaml --skip-preflight` → `agent_complete status=success exit_reason=task_complete steps=6`.
- Phase 3 browser→RDP handoff: 4 browser steps succeed, `rdp_launch` raises the documented "mstsc is Windows-only" error (A-06), retry cap fires after 3 attempts, HITL routed cleanly, agent exits.

---

## Completed — Phase 4 (PDF + Memory)

- [x] `executors/extraction.py` — `ExtractionPipeline` with three tiers (pdfplumber 0.92 conf → Tesseract OCR 0.78 → local VLM ≤0.81). `FieldSpec` carries aliases + regex + financial flag. Per-field gating: failed fields land in `result.fields` with `confidence=0.0, hitl_required=True`.
- [x] `memory/knowledge.py` — `KnowledgeStore` Protocol + `ChromaKnowledgeStore` (real, persistent, buffered writes flushed at end-of-task per CLAUDE.md) + `NullKnowledgeStore` (no-op fallback when `chromadb` isn't installed). `get_knowledge_store()` picks at runtime.
- [x] `executors/file_ops.py` Excel ops — `read_excel` (list-of-dicts, blank-row skip, retry on `OSError`), `write_excel` (atomic via `.tmp` + replace, overwrite guard), `update_excel_cell` (column letter or 1-based index).
- [x] `extract_pdf` + `read_excel` action types routed to `FileExecutor` via `ActionRouter`.
- [x] `config/tasks/extract_pdf.yaml` task YAML.
- [x] Tests: 9 extraction, 10 knowledge, 7 Excel — all passing without a live VLM/chromadb (real openpyxl + Pillow used; chromadb behind a faithful fake client).

---

## Completed — Phase 4.5 (Critical Hardening)

All four architectural defects from the senior-architect review closed before Phase 5 began. Phase 5's HITL dashboard now receives typed `RecoveryDirective.reason` values for blocking modals, RDP disconnects, transient errors, and reconnect-limit escalations.

- [x] **Wired `RecoveryHandler` into `AgentLoop._run_loop()`** — `_apply_directive()` runs both before `_reason()` (detect) and after a failed `_act()` (recover). Bounded by `RETRY_LIMIT=3` recovery attempts per step (`recovery_<step>` counter); exceeding the cap escalates to HITL with the recovery action name in the reason. 6 new integration tests in `tests/test_loop_recovery.py` covering modal-dismiss, RDP-reconnect, transient-retry-then-HITL, unknown-failure-to-HITL, error_present-to-HITL, and the happy-path-no-recovery case.
- [x] **Consolidated RDP reconnect counters** — `RDPHandler.MAX_RECONNECTS` is now the single source of truth. `RecoveryHandler.max_rdp_reconnects` reads it via a property (with type-check guard to defeat MagicMock auto-attributes in tests), falling back to `DEFAULT_MAX_RDP_RECONNECTS=3` when no handler is provided.
- [x] **`DesktopExecutor.attach` short-circuits on `DesktopError`** — pywinauto import failures (deterministic on non-Windows) now raise immediately instead of busy-looping for `DEFAULT_TIMEOUT_S=10s`. Regression test asserts the call returns in < 1 second.
- [x] **`BrowserSession.__exit__` nested try/finally** — every cleanup step (context.close → browser.close → playwright.stop) runs in its own try/finally; a failure in one does not prevent the next. Regression test asserts all three are called even when `context.close()` raises.

Result: 133 tests passing (was 120, +13 from this phase). Live e2e regression of `claim_search.yaml` still completes with `status=success exit_reason=task_complete steps=6`.

---

## Known Gaps — Carry-forward (not blocking Phase 5)

- [x] **`ExtractionPipeline` VLM tier multi-page** — RESOLVED 2026-05-14. `_tier_vlm` now iterates up to `settings.vlm_max_pages` pages, batches all pending specs per page, and stops early as specs are found. `FieldSpec.pages` accepts explicit hints. 5 new integration tests. (A-16 closed.)
- [ ] **Windows runtime validation pending** — RDP/desktop/mstsc paths require a Windows host with `pywinauto`, `mss`, and the App VM accessible. Tracked in `docs/assumptions.md` A-06 / A-08 / A-09 / A-10. Gating step for Phase 3 production, not a code defect.

---

## Completed — Phase 5 (HITL + 3 Agents)

- [x] `hitl/queue.py` — `HITLQueue` with `flag()`, `wait_for_resolution()` (injectable sleep, `HITLTimeoutError`), and `apply_resolution()` covering approve / correct / skip / abort
- [x] `hitl/server.py` — FastAPI dashboard with multi-agent view: discovers every `data/db/*.db` under `settings.db_dir`, aggregates pending counts, exposes HTML review form + JSON API (`/api/agents`, `/api/agent/{id}/hitl`, `/api/agent/{id}/resolve/{hid}`); binds to 127.0.0.1
- [x] `hitl/runner.py` — `HITLRunner` supervisor that runs `AgentLoop.run`, blocks on `HITLQueue.wait_for_resolution()` when the loop pauses, applies the resolution to `WorkingMemory`, and calls `AgentLoop.resume()` until task completes or `MAX_RESUMES=10`
- [x] Agent checkpoint resume after HITL — `AgentLoop.resume()` re-uses the in-process `WorkingMemory`; resolution mutates step/retry counters before resume; SQLite `tasks.status` flips `hitl_wait` → `running` on `SessionMemory.resolve_hitl`
- [x] 3-agent parallel test — `tests/test_multi_agent.py::test_three_agents_run_in_parallel` spawns 3 threads with isolated `SessionMemory(agent_id=…)`, verifies each completes its own task and that DB + audit dirs are per-agent (`agent_1.db`, `agent_2.db`, `agent_3.db`; same for `.ndjson`)
- [x] HITL dashboard multi-agent view — `tests/test_multi_agent.py::test_dashboard_aggregates_three_agents` asserts FastAPI dashboard sees all 3 agents and only the targeted agent's pending count drops on resolution

`memory/session.py` gained `list_hitl`, `get_hitl`, `resolve_hitl` to back the dashboard. `tasks.status` returns to `running` automatically when a HITL row is resolved so the runner knows to resume.

Result: **158 tests collected (151 passed, 7 skipped)** — +20 from this phase. Live e2e regression (`config/tasks/claim_search.yaml --skip-preflight`) still completes with `status=success exit_reason=task_complete steps=6`.

---

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
