# Technology Stack

## Decision Principles

1. Every tool must work fully on-premises — no data leaves the client network, no external API calls
2. Prefer tools with Windows support (agent VMs are Windows)
3. Prefer accessibility-tree-based automation over pixel coordinates
4. Use AI only where deterministic code cannot work reliably
5. Keep the stack Python-first for maintainability

## Confirmed Constraints

| Constraint | Impact |
|-----------|--------|
| Fully on-premises — no external APIs | Claude/OpenAI/Gemini APIs cannot be used. LLM must run locally |
| Development machine: CPU only, no GPU | Use lighter quantized model (MiniCPM-V 2.6 Q4_K_M) via Ollama |
| Production: GPU available | Use stronger model (Qwen2-VL-7B Q4_K_M) via vLLM on inference server |
| LD and IIM apps are browser-based | Playwright is the primary executor — pywinauto scope is minimal |
| POC Cases 1–4 must be supported | Architecture validated against all 4 cases — see [feasibility-analysis.md](feasibility-analysis.md) |

## Known Limitations (from Feasibility Analysis)

| Limitation | Severity | Mitigation |
|-----------|---------|------------|
| CPU inference 15–40s per VLM call | High | Cache-first strategy — 80% of actions served from ChromaDB after warm-up |
| DPI scaling must be 100% for pywinauto | High | Document in setup checklist; use VM at 100% DPI |
| MiniCPM-V needs 16 GB RAM minimum | High | Dev machine must have 16 GB; fallback to ShowUI-2B (4 GB) if constrained |
| Ollama must be running before agent starts | High | Startup health check — fail fast with clear error message |
| JS injection (`ldCdNotifToggle`) may differ in real app | Medium | Detect + fallback to element click |
| ChromaDB needs manual Windows install workaround | Medium | Document steps; automate in setup script |
| Model `:latest` tag silently upgrades | Medium | Pin specific model digest in .env |

---

## Layer-by-Layer Decisions

### Layer 1 — Screen Capture

| Tool | Decision | Reason |
|------|----------|--------|
| **mss** | PRIMARY | Fastest screenshot library (~50ms), captures full screen or specific window region |
| **Pillow (PIL)** | SUPPORT | Image preprocessing — crop, resize, annotate before sending to VLM |
| **pygetwindow** | SUPPORT | Find window handles, get bounding rectangles for targeted region capture |

---

### Layer 2 — Vision and Reasoning (On-Premises LLM)

No external API calls. Model runs locally via Ollama (dev) or vLLM (production).

#### Development — CPU only

| Tool | Decision | Reason |
|------|----------|--------|
| **MiniCPM-V 2.6 (Q4_K_M GGUF)** | PRIMARY | 8B params, ~8 GB RAM, strong UI element detection and dense text OCR, runs on CPU |
| **Ollama** | RUNNER | Simple local model server, API on localhost:11434, easy model switching |

#### Production — GPU available

| Tool | Decision | Reason |
|------|----------|--------|
| **Qwen2-VL-7B-Instruct (Q4_K_M GGUF)** | PRIMARY | Best open-weights model for UI reasoning and sequential step logic, 8 GB VRAM |
| **MiniCPM-V 2.6 (Q4_K_M GGUF)** | FALLBACK | Faster inference for simple perception tasks where 7B is overkill |
| **vLLM** | RUNNER | High-throughput OpenAI-compatible inference server, shared across all agents over LAN |

#### Environment switching — one config change only

```env
# Development (.env)
INFERENCE_URL=http://localhost:11434/v1
MODEL_NAME=minicpm-v:latest

# Production (.env)
INFERENCE_URL=http://inference-server:8080/v1
MODEL_NAME=qwen2-vl-7b-instruct
```

Agent code is identical in both environments — no code changes needed to switch.

#### When to use AI vs deterministic code

```
USE AI (model call):                 USE DETERMINISTIC CODE:
────────────────────────────         ──────────────────────────────
Understand current screen state      Click a button by Playwright selector
Decide next action from context      Fill a form field with known value
Extract values from PDFs/docs        Navigate to a known URL
Classify document type               Compare two extracted values
Handle unexpected UI state           File read / write operations
Resolve ambiguous field values       Wait for page load / element visible
```

---

### Layer 3 — Browser Automation (Primary Executor)

LD and IIM are confirmed browser-based. Playwright handles 90% of all automation.

| Tool | Decision | Reason |
|------|----------|--------|
| **Playwright (Python sync_api)** | PRIMARY | Handles RD Web login, LD app, IIM app — all browser interactions |
| **Selenium** | NOT USED | Superseded by Playwright in every measurable way |

Notes:
- Use `playwright.sync_api` for MVP — simpler to debug than async
- Switch to `playwright.async_api` only if 3-agent concurrency causes thread blocking
- Playwright can connect to Chrome inside RDP session via `connect_over_cdp` if Chrome's remote debugging port is exposed on the App VM

**Selector priority — never use coordinates in browser:**
```
1. data-testid     ← most stable, survives app updates
2. aria-label
3. name attribute
4. Model-suggested CSS selector from element description
5. Flag for human  ← if nothing resolves
```

---

### Layer 4 — Desktop and RDP Automation (Minimal Scope)

Because LD and IIM are browser-based, pywinauto is only needed for:
- Detecting that a RemoteApp window has appeared after RDP launch
- File Explorer navigation (network shared folders)
- Any native Windows dialog (file open/save, authentication prompts)

| Tool | Decision | Scope |
|------|----------|-------|
| **pywinauto (UIA backend)** | PRIMARY | RemoteApp window detection, File Explorer, native dialogs |
| **WinAppDriver** | SECONDARY | Only if pywinauto cannot locate an element via UIA |
| **pyautogui** | NOT USED | No coordinate-based automation in this project |

**pyautogui is explicitly excluded.** LD and IIM are browser-based (Playwright handles them). File Explorer and RDP window management use pywinauto UIA which is resolution-independent.

---

### Layer 5 — RDP Session Management

| Tool | Role |
|------|------|
| **mstsc.exe** (built-in Windows) | Launch RDP / RemoteApp session from .rdp file |
| **pywinauto** | Detect RemoteApp windows appearing on Agent VM after launch |
| **mss** | Capture RDP window region for perception if needed |
| **Background thread (Python)** | Keep-alive heartbeat every 4 minutes — prevents session disconnect |

---

### Layer 6 — PDF and Document Extraction

Pipeline runs in order. Stops at first step that meets confidence threshold.

| Step | Tool | Threshold |
|------|------|-----------|
| 1 — Native text | **pdfplumber** | > 0.90 → done |
| 2 — OCR | **PyMuPDF + Tesseract 5** | > 0.80 → done |
| 3 — Vision extraction | **Local VLM (MiniCPM-V or Qwen2-VL)** | > 0.70 → done |
| 4 — Human review | **HITL queue** | Always if below threshold |

| Tool | Decision | Reason |
|------|----------|--------|
| **pdfplumber** | FIRST PASS | Native PDF text — no OCR overhead if text layer present |
| **PyMuPDF (fitz)** | RENDERING | Render PDF page to image for OCR |
| **pytesseract + Tesseract 5** | OCR FALLBACK | Preprocessed image OCR — deskew and denoise before passing |
| **Local VLM** | FINAL PASS | Same Ollama/vLLM model already running — no additional service needed |

---

### Layer 7 — Agent Orchestration

| Tool | Decision | Reason |
|------|----------|--------|
| **Custom Python agent loop** | PRIMARY | Full control, minimal abstraction, easy to debug and extend |
| **LangGraph** | OPTIONAL — Phase 3+ | Adds visual state machine graph; useful when workflow branching grows complex |
| **LangChain / CrewAI / AutoGen** | NOT USED | Over-engineered for deterministic RPA workflows |

---

### Layer 8 — Memory and State

| Memory Type | Tool | Scope |
|-------------|------|-------|
| Working memory | Python dict in RAM | Current task only — lost when task ends |
| Session memory | **SQLite** (WAL mode) | Per agent — survives crash, enables checkpoint resume |
| Long-term knowledge | **ChromaDB (local file)** | Known UI patterns, error recoveries — read-only during task |
| Audit log | **Append-only NDJSON** | Every action and decision — never modified after write |

---

### Layer 9 — Human-in-the-Loop (HITL)

| Tool | Decision | Reason |
|------|----------|--------|
| **SQLite table** | QUEUE | No infrastructure needed, works across 3 agents via shared file |
| **FastAPI + Jinja2** | REVIEW UI | Lightweight local dashboard — human sees screenshot + context |
| **Pydantic** | VALIDATION | Type-safe HITL request and response models |

HITL triggers (non-negotiable — agent must pause):
- Model confidence below 0.75
- Same action failed 3 consecutive times
- Any form submit or write action touching financial data
- Screen state does not match any known pattern after 2 recovery attempts

---

### Layer 10 — Configuration and Secrets

| Tool | Decision | Reason |
|------|----------|--------|
| **Pydantic Settings** | APP CONFIG | Type-safe, reads from .env, validated at startup |
| **python-dotenv** | LOCAL SECRETS | .env file for development — never committed to git |
| **HashiCorp Vault** | PRODUCTION SECRETS | On-prem credential management for VM deployment |
| **YAML** | TASK DEFINITIONS | Human-readable task goal files, version-controlled |

---

### Layer 11 — Logging and Observability

| Tool | Decision | Reason |
|------|----------|--------|
| **structlog** | STRUCTURED LOGGING | JSON-formatted per-agent logs, queryable with jq |
| **Rich** | CONSOLE OUTPUT | Readable terminal output during development |
| **Grafana + Loki** | PRODUCTION MONITORING | Log aggregation and dashboards — Phase 3+ |

---

## Full Stack Summary

```
┌────────────────────────────────────────────────────────────────┐
│  Python 3.11  on  Windows 10/11                                │
│                                                                │
│  ON-PREM LLM (dev)   : MiniCPM-V 2.6 Q4_K_M via Ollama       │
│  ON-PREM LLM (prod)  : Qwen2-VL-7B Q4_K_M via vLLM (GPU)     │
│                                                                │
│  BROWSER             : Playwright  ← primary executor          │
│                        (RD Web + LD app + IIM app)             │
│                                                                │
│  DESKTOP / RDP       : pywinauto UIA  ← minimal scope         │
│                        (RemoteApp detection + File Explorer)   │
│                                                                │
│  SCREEN CAPTURE      : mss + Pillow                            │
│  PDF / OCR           : pdfplumber → PyMuPDF + Tesseract        │
│                        → local VLM → HITL                      │
│                                                                │
│  ORCHESTRATION       : Custom agent loop                       │
│  MEMORY              : dict + SQLite + ChromaDB + NDJSON       │
│  HITL                : SQLite queue + FastAPI dashboard        │
│  CONFIG              : Pydantic Settings + dotenv + YAML       │
│  LOGGING             : structlog + Rich                        │
│  PACKAGING           : Poetry                                  │
└────────────────────────────────────────────────────────────────┘
```

---

## What to Avoid

| Tool | Why Avoid |
|------|-----------|
| **Any cloud LLM API** (Claude, OpenAI, Gemini) | Requires internet — violates on-prem constraint |
| **PyAutoGUI** | Coordinate-based, breaks on resolution/DPI/window change — not needed since LD/IIM are browser-based |
| **SikuliX** | Image-matching only, high maintenance, same problems as PyAutoGUI |
| **Selenium** | Superseded by Playwright in every way |
| **LangChain** | Overhead not justified for RPA workflows |
| **Docker** | Cannot run Windows GUI automation (pywinauto, mstsc) reliably |
| **PyAutoGUI as RDP automation** | RemoteApp windows are handled by pywinauto UIA — no coordinates needed |
