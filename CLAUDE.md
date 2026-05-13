# CLAUDE.md — Vision RPA Agent

## Project Purpose

On-premises AI agent that automates insurance claim workflows by observing the screen, reasoning about current state, and taking actions — across browser apps, RDP sessions, File Explorer, and PDF documents. No external API calls. All LLM inference runs locally.

## Architecture in One Sentence

`observe (mss+VLM) → reason (local LLM) → act (Playwright/pywinauto) → store (SQLite+ChromaDB) → loop`

## Critical Non-Negotiables

- **Zero external API calls** — no Anthropic, OpenAI, or any cloud LLM. Only Ollama (dev) or vLLM (prod)
- **No pixel coordinates as primary automation** — Playwright selectors for browser, pywinauto UIA for desktop
- **Confidence ≥ 0.90 for all financial fields** — below threshold always routes to HITL, no exceptions
- **Human approval gate** on every form submit / write action for first 10 runs per task type
- **Cache before LLM** — always query ChromaDB for known UI pattern before calling local VLM
- **Idempotent stages** — every stage must be safe to re-run; check checkpoint before executing

## Key Files and Modules

```
agent/loop.py           — observe → reason → act → store cycle
agent/perception.py     — mss screen capture + local VLM call
agent/planner.py        — ActionPlan decision (cache-first → LLM-second)
agent/recovery.py       — unexpected state handler (MUST exist before Phase 1)
executors/browser.py    — Playwright actions (primary executor — LD + IIM are browser-based)
executors/desktop.py    — pywinauto UIA (RDP window detection + File Explorer only)
executors/rdp.py        — mstsc.exe launch + keep-alive thread + reconnect
executors/file_ops.py   — Excel / PDF / network drive operations
executors/extraction.py — pdfplumber → Tesseract → local VLM → HITL pipeline
memory/working.py       — in-process task dict (lost on crash — always checkpoint to SQLite)
memory/session.py       — SQLite session store (tasks, actions, extractions, checkpoints, hitl_queue)
memory/knowledge.py     — ChromaDB long-term UI pattern + error recovery store
hitl/queue.py           — write HITL request, pause agent, poll for resolution
hitl/server.py          — FastAPI dashboard for human review
config/settings.py      — Pydantic Settings (reads from .env)
config/locators/rdweb.py — POC locators (120+ data-testid selectors) — DO NOT rewrite
```

## Dev Commands

```bash
# Start local LLM (must be running before any agent task)
ollama serve
ollama pull minicpm-v   # first time only

# Run agent on a task
poetry run python run_agent.py --task config/tasks/case2_claim_search.yaml

# Run HITL dashboard (separate terminal)
poetry run python -m hitl.server

# Lint and type check
poetry run ruff check --fix .
poetry run mypy .

# Tests
poetry run pytest
poetry run pytest tests/ -v
```

## Environment Switching

```env
# Development (CPU, Ollama)
INFERENCE_URL=http://localhost:11434/v1
MODEL_NAME=minicpm-v:latest

# Production (GPU, vLLM)
INFERENCE_URL=http://inference-server:8080/v1
MODEL_NAME=qwen2-vl-7b-instruct
```

Only these two lines change between environments. All agent code is identical.

## POC Case Support

All 4 POC cases from `insurance-agent-project` are supported. When implementing case logic:
- Copy `config/locators/rdweb.py` selectors directly — they are proven
- Reuse Pydantic result schemas from POC (Case1Result, Case2FullResult, etc.)
- Migration order: Case 1 → Case 2 → Case 3 → Case 4

## Skills Available

| Skill | When to Use |
|-------|-------------|
| `agent-loop` | Implementing observe/reason/act/store cycle, ScreenState, ActionPlan |
| `browser-rpa` | Playwright executor, selector strategy, multi-tab, toast detection |
| `on-prem-inference` | Ollama/vLLM client, prompt design, confidence scoring |
| `agent-memory` | SQLite schema, ChromaDB patterns, working memory, checkpoint/resume |
| `poc-cases` | Migrating POC case logic, locator reuse, Pydantic schemas |
| `hitl-recovery` | HITL queue design, recovery handler, pause/resume protocol |

## Module Boundaries (Do Not Cross)

```
perception.py    → only calls VLM API and mss — no business logic
planner.py       → only produces ActionPlan — never executes actions
browser.py       → only executes Playwright actions — no VLM calls
desktop.py       → only executes pywinauto actions — no VLM calls
knowledge.py     → only reads ChromaDB during task — writes only after task ends
```

## Selector Priority (Enforced in browser.py)

```
1. data-testid   ← always try first
2. aria-label
3. name attribute
4. LLM-generated CSS
5. flag_for_human ← never guess
```

## What Not to Do

- Never call `pyautogui.click(x, y)` — LD and IIM are browser-based; pywinauto handles the rest
- Never hardcode financial values — always extract and validate with confidence check
- Never skip the ChromaDB cache check before calling VLM
- Never modify `config/locators/rdweb.py` without testing against simulation first
- Never commit `.env` or any file containing credentials
- Never write to ChromaDB mid-task — only after task completes successfully
