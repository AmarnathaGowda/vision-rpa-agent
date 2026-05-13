# Dependencies and Installation

## Runtime Requirements

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.11.x (64-bit) | 3.12 supported; avoid 3.13 (ecosystem lag) |
| Windows OS | 10 Pro 22H2+ or 11 Pro | Required for pywinauto + mstsc.exe |
| Google Chrome | Latest stable | For Playwright browser automation |
| Tesseract OCR | 5.x | Windows installer from UB-Mannheim |
| Ollama | Latest | Development LLM runner (CPU, localhost) |
| Poetry | Latest | Python dependency management |

---

## Python Packages

### Core Agent

```toml
[tool.poetry.dependencies]
python = "^3.11"

# ── On-Prem LLM client ────────────────────────────────────────────
# vLLM and Ollama both expose an OpenAI-compatible API.
# The openai SDK works for both — no Anthropic SDK needed.
openai = "^1.30"                 # client for Ollama (dev) and vLLM (prod)

# ── Browser automation (primary executor) ─────────────────────────
# LD and IIM are browser-based — Playwright handles 90% of automation.
playwright = "^1.44"

# ── Desktop / Windows automation (minimal scope) ──────────────────
# Used only for: RemoteApp window detection, File Explorer, native dialogs.
# Not used for LD or IIM — those are handled by Playwright.
pywinauto = "^0.6.8"             # Windows UIA backend — resolution-independent
pygetwindow = "^0.0.9"           # window bounding rect utilities

# ── Screen capture ────────────────────────────────────────────────
mss = "^9.0"                     # fast screenshot (~50ms), region or full screen
Pillow = "^10.0"                 # image preprocessing before VLM call

# ── PDF and OCR ───────────────────────────────────────────────────
pdfplumber = "^0.11"             # step 1: native PDF text extraction
PyMuPDF = "^1.24"                # step 2: render PDF page to image (fitz)
pytesseract = "^0.3.10"          # step 2: Tesseract OCR wrapper

# ── Data handling ─────────────────────────────────────────────────
openpyxl = "^3.1"                # Excel read (shared network drive files)
pandas = "^2.0"                  # tabular data processing

# ── Memory / storage ──────────────────────────────────────────────
chromadb = "^0.5"                # local vector store for UI pattern knowledge

# ── Config / validation ───────────────────────────────────────────
pydantic = "^2.0"
pydantic-settings = "^2.0"
python-dotenv = "^1.0"
PyYAML = "^6.0"                  # task goal definition files

# ── HITL web server ───────────────────────────────────────────────
fastapi = "^0.111"
uvicorn = {extras = ["standard"], version = "^0.30"}
jinja2 = "^3.1"
python-multipart = "^0.0.9"

# ── Logging ───────────────────────────────────────────────────────
structlog = "^24.0"
rich = "^13.0"

# ── Utilities ─────────────────────────────────────────────────────
tenacity = "^8.0"                # retry with exponential backoff
httpx = "^0.27"                  # HTTP client for HITL polling and inference calls
```

### Development Only

```toml
[tool.poetry.group.dev.dependencies]
pytest = "^8.0"
pytest-asyncio = "^0.23"
pytest-mock = "^3.12"
black = "^24.0"
ruff = "^0.4"
mypy = "^1.10"
```

> **Not included:** `anthropic` SDK — no external API calls. The `openai` SDK is used as a universal client for both Ollama and vLLM since both expose an OpenAI-compatible API endpoint.

> **Not included:** `pyautogui` — LD and IIM are browser-based (Playwright handles them). pywinauto UIA covers all remaining desktop interactions without coordinates.

---

## Installation Steps

### Step 1 — Install Python 3.11

Download from python.org (Windows x86-64 installer).
During installation: check **"Add Python to PATH"** and **"Install pip"**.

```powershell
python --version    # Python 3.11.x
pip --version
```

### Step 2 — Install Poetry

```powershell
(Invoke-WebRequest -Uri https://install.python-poetry.org -UseBasicParsing).Content | python -
```

Add to PATH (typically `%APPDATA%\Python\Scripts`). Then verify:

```powershell
poetry --version
```

### Step 3 — Install Ollama (Development only)

Download from: https://ollama.com/download/windows

```powershell
# After install, pull the development model (~5 GB download)
ollama pull minicpm-v

# Verify the API is running
curl http://localhost:11434/v1/models
```

Ollama starts automatically as a background service after install.
It exposes an OpenAI-compatible API at `http://localhost:11434/v1`.

### Step 4 — Install Tesseract OCR

1. Download from: https://github.com/UB-Mannheim/tesseract/wiki
2. Install to default: `C:\Program Files\Tesseract-OCR`
3. Add to PATH: `C:\Program Files\Tesseract-OCR`

```powershell
tesseract --version    # Tesseract 5.x.x
```

### Step 5 — Clone Project and Install Dependencies

```powershell
cd C:\agents
git clone <repo-url> vision-rpa-agent
cd vision-rpa-agent

poetry install
```

### Step 6 — Install Playwright Browser

```powershell
poetry run playwright install chromium
poetry run playwright install-deps
```

### Step 7 — Configure Environment

```powershell
copy .env.example .env
notepad .env    # set INFERENCE_URL, MODEL_NAME, app URLs, agent ID
```

### Step 8 — Verify Full Installation

```powershell
poetry run python -c "
import openai
import playwright
import pywinauto
import mss
import pdfplumber
import chromadb
import structlog
print('All imports OK')
"

# Verify Ollama model responds
poetry run python -c "
from openai import OpenAI
client = OpenAI(base_url='http://localhost:11434/v1', api_key='ollama')
resp = client.chat.completions.create(
    model='minicpm-v:latest',
    messages=[{'role': 'user', 'content': 'Reply with: OK'}],
    max_tokens=10,
)
print('LLM response:', resp.choices[0].message.content)
"
```

---

## External Tools (non-Python)

| Tool | Purpose | Where to Get |
|------|---------|-------------|
| **Ollama** | Local LLM server (development, CPU) | ollama.com/download/windows |
| **MiniCPM-V 2.6** | Dev vision model via Ollama | `ollama pull minicpm-v` |
| **Qwen2-VL-7B** | Production vision model via vLLM | HuggingFace (production server) |
| **Tesseract OCR 5.x** | PDF/image OCR fallback | UB-Mannheim Windows installer |
| **WinAppDriver** (optional) | Desktop UIA WebDriver fallback | Microsoft GitHub releases |
| **Accessibility Insights for Windows** | Inspect UIA accessibility tree | Microsoft Store (free) |
| **Chrome** (latest) | Browser for Playwright | google.com/chrome |
| **mstsc.exe** | RDP client | Built into Windows |
| **Git** | Version control | git-scm.com |

---

## Model Files Reference

| Model | Format | Size (Q4_K_M) | RAM Needed | Use |
|-------|--------|--------------|-----------|-----|
| MiniCPM-V 2.6 | GGUF via Ollama | ~5 GB | ~8 GB RAM | Development (CPU) |
| Qwen2-VL-7B-Instruct | GGUF via vLLM | ~5 GB | ~8 GB VRAM | Production (GPU) |

---

## Package Notes

### openai (used as universal LLM client)

```python
# Development — points to Ollama
from openai import OpenAI
client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama",    # Ollama ignores the key but requires a non-empty value
)

# Production — points to vLLM (same code, different env var)
client = OpenAI(
    base_url="http://inference-server:8080/v1",
    api_key="ignored",
)

# Vision call (same for both environments)
response = client.chat.completions.create(
    model=settings.model_name,
    messages=[{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            {"type": "text", "text": "Describe what is on screen. Return JSON."},
        ],
    }],
    max_tokens=1024,
)
```

### pywinauto

- Always use `backend="uia"` — never `backend="win32"`
- UIA is resolution-independent and survives DPI/window changes
- Use `AutomationId` as primary locator — most stable across app versions
- Does not work on Linux/Mac — Windows only
- Scope in this project: RemoteApp window detection, File Explorer, native dialogs only

### mss

```python
import mss
from PIL import Image

with mss.mss() as sct:
    # Full screen
    shot = sct.grab(sct.monitors[1])
    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    # Specific window region (get bounds from pygetwindow first)
    region = {"left": 100, "top": 100, "width": 1200, "height": 800}
    shot = sct.grab(region)
```

### chromadb

```python
import chromadb

# Always use persistent client — keeps data across restarts
client = chromadb.PersistentClient(path="./data/chroma")

# One collection per knowledge type
ui_patterns = client.get_or_create_collection("ui_patterns")
```

- Keep one ChromaDB instance per agent process (not thread-safe for concurrent writes)
- Shared read-only across agents is safe
- On Windows, if import fails: `pip install chromadb --no-deps` then install deps individually

### pdfplumber (extraction step 1)

```python
import pdfplumber

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[0]
    text = page.extract_text()
    tables = page.extract_tables()
```

### pytesseract (extraction step 2)

```python
import pytesseract
from PIL import Image
import fitz  # PyMuPDF

# Render PDF page to image first
doc = fitz.open(pdf_path)
page = doc[0]
pix = page.get_pixmap(dpi=300)
img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

# OCR
text = pytesseract.image_to_string(img, config="--psm 6")
```

---

## Dependency Update Policy

- Lock all versions in `pyproject.toml` — never use `*` or unbounded `>=`
- Update dependencies only at the start of a new development phase
- Run full test suite after any update: `poetry run pytest`
- Never update during active demo or integration testing windows
- Pin Ollama model tags in `.env` — `minicpm-v:latest` can pull a new version silently

---

## Quick Reference — Key Import Paths

```python
# LLM client (works for both Ollama dev and vLLM production)
from openai import OpenAI

# Browser
from playwright.sync_api import sync_playwright, Page, BrowserContext

# Desktop (minimal scope — RDP windows + File Explorer only)
from pywinauto import Desktop, Application
from pywinauto.findwindows import ElementNotFoundError

# Screen capture
import mss
from PIL import Image

# PDF
import pdfplumber
import fitz              # PyMuPDF
import pytesseract

# Config
from config.settings import settings

# Logging
import structlog
log = structlog.get_logger()
```
