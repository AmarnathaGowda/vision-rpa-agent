# VM Setup and Configuration

## Do You Need a VM for Development?

**Short answer: Preferred but not mandatory for early development.**

| Environment | VM Required? | Notes |
|-------------|-------------|-------|
| Development (building + unit testing) | No — local Windows laptop works | Ollama runs locally, pywinauto and mstsc.exe are built into Windows |
| Integration testing (real RDP flows) | Yes — App VM needed | Need a second VM to host LD/IIM apps |
| Demo environment | Yes — both Agent VM + App VM | Mirrors production setup |
| Production | Yes — Agent VMs + Inference Server VM | Inference server shared across all agents |

**If your development machine is Windows:** Start locally with Ollama + MiniCPM-V 2.6. Add the App VM when you reach integration testing (Phase 2+).

**If your development machine is Mac/Linux:** You need a Windows VM immediately — pywinauto and mstsc.exe are Windows-only.

---

## VM Roles — Full Picture

```
┌─────────────────────┐     RDP / RemoteApp    ┌──────────────────────┐
│    AGENT VM         │ ──────────────────────► │    APP VM            │
│                     │                         │                      │
│  Runs:              │                         │  Hosts:              │
│  • Python agent     │                         │  • LD Module (IIS)   │
│  • Playwright       │                         │  • IIM app (IIS)     │
│  • pywinauto        │                         │  • RD Web portal     │
│  • mss screen cap   │                         │  • Shared folders    │
│  • Ollama (dev only)│                         │  • Excel files       │
└─────────────────────┘                         └──────────────────────┘
         │
         │ HTTP (LAN) — production only
         ▼
┌─────────────────────┐
│  INFERENCE SERVER   │
│  (production only)  │
│                     │
│  vLLM + GPU         │
│  Qwen2-VL-7B        │
│  Serves all agents  │
└─────────────────────┘
```

In development, Ollama runs directly on the Agent VM (no separate inference server needed).
In production, the inference server is a separate GPU VM shared by all Agent VMs.

---

## Agent VM Specification

### Development (local laptop / single dev machine, CPU only)

| Component | Specification |
|-----------|--------------|
| OS | Windows 10 Pro (22H2+) or Windows 11 Pro |
| CPU | 4+ cores (Intel i5 / AMD Ryzen 5 or better) |
| RAM | 16 GB minimum (8 GB OS + agent + 8 GB MiniCPM-V model) |
| Storage | 60 GB free SSD (model files ~5 GB, screenshots, logs) |
| GPU | Not required — CPU inference via Ollama |
| Display | 1920 × 1080 at 100% DPI scaling |
| Network | Access to App VM IP / RD Web URL |
| Python | 3.11.x (64-bit) |
| Browser | Google Chrome (latest stable) |

### Testing / MVP (3 agents, CPU inference)

| Component | Specification |
|-----------|--------------|
| OS | Windows 11 Pro |
| CPU | 8 cores |
| RAM | 16 GB (agents are lightweight — inference runs on Ollama locally or inference server) |
| Storage | 200 GB SSD (NVMe preferred) |
| Display | 1920 × 1080 at 100% DPI scaling (no fractional scaling) |
| Network | 100 Mbps LAN to App VM |
| Python | 3.11.x (64-bit) |
| Browser | Google Chrome (latest stable) |

### Production Agent VM (per VM, inference offloaded to inference server)

| Component | Specification |
|-----------|--------------|
| OS | Windows Server 2022 or Windows 11 Pro |
| CPU | 4 cores dedicated per agent |
| RAM | 8 GB (model not loaded here — calls inference server over LAN) |
| Storage | 100 GB SSD |
| GPU | Not required — inference server handles all model calls |
| Display | Virtual display at 1920 × 1080, 100% DPI |
| Network | Stable LAN, < 10ms latency to App VM and Inference Server |

---

## Inference Server Specification (Production Only)

Shared by all Agent VMs. Model loaded once, all agents query it over LAN.

| Component | Specification |
|-----------|--------------|
| OS | Ubuntu 22.04 LTS or Windows Server 2022 |
| CPU | 8 cores |
| RAM | 16 GB system RAM |
| GPU | NVIDIA GPU with 8 GB+ VRAM (RTX 3080 / RTX 4080 / A4000 or better) |
| Storage | 50 GB SSD (model files ~5 GB, logs) |
| Network | LAN to all Agent VMs, < 5ms latency |
| Software | NVIDIA drivers + CUDA 12.x + vLLM |

**GPU VRAM guide:**

| GPU VRAM | Suitable Model | Notes |
|----------|---------------|-------|
| 8 GB | Qwen2-VL-7B Q4_K_M | Fits comfortably, recommended minimum |
| 16 GB | Qwen2-VL-7B full precision | Better quality, more headroom |
| 24 GB | Qwen2-VL-72B Q4_K_M | Maximum quality, higher latency |

---

## App VM Specification (hosts LD + IIM)

| Component | Specification |
|-----------|--------------|
| OS | Windows Server 2019 or 2022 |
| CPU | 8 cores (handles multiple simultaneous RDP sessions) |
| RAM | 16 GB |
| Storage | 200 GB SSD |
| Network | LAN with Agent VMs |
| IIS | Version 10 (hosts LD Module and IIM as web apps) |
| RD Services | Remote Desktop Services + RemoteApp configured |

---

## Critical Windows Settings (Agent VM)

These must be configured before running automation. Incorrect settings are a leading cause of silent failures.

### 1. Display and Scaling

```
Settings → System → Display
  Resolution : 1920 × 1080   ← never change during agent operation
  Scale      : 100%           ← fractional scaling (125%, 150%) breaks pywinauto

Settings → System → Display → Advanced display settings
  Refresh rate: 60 Hz
```

### 2. Power and Sleep

```
Settings → System → Power & Sleep
  Screen : Never
  Sleep  : Never

Control Panel → Power Options → Change plan settings
  Turn off display     : Never
  Put computer to sleep: Never
```

### 3. Screen Saver

```
Control Panel → Personalization → Screen Saver
  Screen saver: None
```

### 4. UAC (User Account Control)

```
Control Panel → User Accounts → Change User Account Control settings
  Set to: "Never notify"
  NOTE: Only for dedicated automation service accounts, not personal accounts
```

### 5. Windows Updates

```
Settings → Windows Update → Advanced Options
  Active hours: 6 AM – 11 PM
  Pause updates during active testing phases
  Never allow automatic restart during business hours
```

### 6. RDP Client Settings

```
mstsc.exe → Show Options → Display tab
  Remote desktop size: Full Screen
  Colors: True Color (32-bit)

mstsc.exe → Experience tab
  Connection speed: LAN (10 Mbps or higher)
  Enable : Font smoothing, Desktop composition
  Disable: Show window contents while dragging
```

### 7. Chrome Settings

```
Chrome flags (chrome://flags):
  #enable-automation: Enabled

Chrome settings:
  Downloads: fixed default location (e.g. C:\agents\downloads)
  Disable: "Ask where to save each file before downloading"
  Disable: hardware acceleration (if causing mss screenshot issues)
```

---

## Ollama Setup (Development — Agent VM)

Ollama runs the local LLM on the development machine. No GPU required.

```powershell
# 1. Download and install Ollama for Windows
#    https://ollama.com/download/windows

# 2. Verify Ollama is running
ollama --version

# 3. Pull MiniCPM-V 2.6 (primary dev model — ~5 GB download)
ollama pull minicpm-v

# 4. Verify model works
ollama run minicpm-v "describe this: [test prompt]"

# 5. Ollama runs as a local API server on port 11434
#    Test: http://localhost:11434
```

Expected inference speed on CPU (no GPU):
- Response time: 15–40 seconds per query (acceptable for development and testing)
- Not suitable for production throughput — use GPU inference server in production

---

## vLLM Setup (Production — Inference Server, Linux recommended)

```bash
# 1. Install NVIDIA drivers and CUDA 12.x (follow NVIDIA docs for your OS)

# 2. Install vLLM
pip install vllm

# 3. Download Qwen2-VL-7B GGUF model
#    Get Q4_K_M GGUF from HuggingFace (Qwen/Qwen2-VL-7B-Instruct-GGUF)

# 4. Start vLLM inference server (OpenAI-compatible API)
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2-VL-7B-Instruct \
  --host 0.0.0.0 \
  --port 8080 \
  --gpu-memory-utilization 0.90

# 5. Test from Agent VM
curl http://inference-server:8080/v1/models
```

---

## Python Environment Setup (Agent VM)

```powershell
# 1. Install Python 3.11 from python.org
#    Check "Add to PATH" during installation

# 2. Install Poetry
(Invoke-WebRequest -Uri https://install.python-poetry.org -UseBasicParsing).Content | python -

# 3. Clone project
cd C:\agents
git clone <repo-url> vision-rpa-agent
cd vision-rpa-agent

# 4. Install Python dependencies
poetry install

# 5. Install Playwright browser
poetry run playwright install chromium

# 6. Install Tesseract OCR
#    Download from: https://github.com/UB-Mannheim/tesseract/wiki
#    Install to: C:\Program Files\Tesseract-OCR
#    Add to PATH

# 7. Configure environment
copy .env.example .env
notepad .env

# 8. Verify all imports
poetry run python -c "
import playwright, pywinauto, mss, pdfplumber, chromadb, structlog
print('All imports OK')
"
```

---

## Environment Variables (.env)

```env
# ── On-Prem LLM Inference ─────────────────────────────
# Development (Ollama, CPU)
INFERENCE_URL=http://localhost:11434/v1
MODEL_NAME=minicpm-v:latest

# Production (vLLM, GPU) — swap these two lines only
# INFERENCE_URL=http://inference-server:8080/v1
# MODEL_NAME=qwen2-vl-7b-instruct

# ── Application URLs ──────────────────────────────────
LD_BASE_URL=http://app-vm-ip:8000
IIM_BASE_URL=http://app-vm-ip:8001
RDWEB_URL=https://app-vm-ip/RDWeb

# ── RDP ───────────────────────────────────────────────
RDP_HOST=app-vm-ip
RDP_USERNAME=automation_user
RDP_PASSWORD=from_vault_in_production

# ── Agent ─────────────────────────────────────────────
AGENT_ID=agent_01
MAX_LOOP_STEPS=50
CONFIDENCE_THRESHOLD=0.75
HITL_URL=http://localhost:8080

# ── Paths ─────────────────────────────────────────────
SCREENSHOT_DIR=C:\agents\screenshots
AUDIT_LOG_DIR=C:\agents\audit
DOWNLOAD_DIR=C:\agents\downloads
MODEL_CACHE_DIR=C:\agents\models
```

---

## Virtual Machine Software Options

| Software | Cost | Best For |
|----------|------|----------|
| **VMware Workstation Pro** | Paid | Best performance, snapshot management |
| **VirtualBox** | Free | Development and testing |
| **Hyper-V** (built into Windows 11 Pro) | Free | Enterprise, native Windows integration |
| **VMware ESXi** | Free tier / Paid | Production server virtualization |

**Recommended for development:** Hyper-V (free, built-in) or VMware Workstation Pro
**Recommended for production:** VMware ESXi or existing enterprise VDI infrastructure

---

## Network Configuration

```
Development:
  Agent VM ←── NAT/Host-only ──► App VM
  Both on same host machine
  Ollama: localhost:11434 (no network needed)

Production:
  Agent VM 1 ─┐
  Agent VM 2 ─┼──── LAN ────► Inference Server (GPU)
  Agent VM 3 ─┘         └───► App VM (LD + IIM)

  Firewall rules required:
    3389  TCP  Agent VM → App VM       (RDP)
    80    TCP  Agent VM → App VM       (HTTP — LD/IIM apps)
    443   TCP  Agent VM → App VM       (HTTPS — RD Web)
    8080  TCP  Agent VM → Inf. Server  (vLLM API)
    11434 TCP  localhost only          (Ollama — dev)
```
