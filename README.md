# Vision RPA Agent

On-premises AI agent that automates insurance claim workflows by observing the
screen, reasoning about current state, and taking actions — across browser
apps, RDP sessions, File Explorer, and PDF documents. No external API calls;
all LLM inference runs locally (Ollama in dev, vLLM in prod).

Architecture: `observe (mss+VLM) → reason (local LLM) → act (Playwright/pywinauto) → store (SQLite+ChromaDB) → loop`.

## Quickstart

```bash
# Install
poetry install
poetry run playwright install chromium

# Start the local LLM (dev)
ollama serve
ollama pull minicpm-v

# Configure
cp .env.example .env   # then edit credentials

# Phase 0 smoke check (no LLM required)
poetry run python run_agent.py --task config/tasks/smoke_test.yaml --skip-preflight

# Tests
poetry run pytest
```

## Documentation

- [docs/architecture.md](docs/architecture.md) — system design
- [docs/roadmap.md](docs/roadmap.md) — phased delivery plan
- [docs/todo.md](docs/todo.md) — current work queue
- [docs/tech-stack.md](docs/tech-stack.md) — libraries and rationale
- [CLAUDE.md](CLAUDE.md) — project rules and module boundaries
