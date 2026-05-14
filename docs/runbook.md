# Vision RPA Agent — Operations Runbook

Production operations for the on-prem insurance-claim automation agent.
Targets a Windows host with Ollama (dev) or vLLM (prod) running locally.

---

## 1. Daily start-up

```powershell
# 1. Confirm local LLM is up. No external API calls — ever.
ollama serve            # dev box
# OR
systemctl status vllm   # prod (Linux GPU host)

# 2. Pull / verify the model
ollama list | findstr minicpm-v
# prod: curl -s http://inference-server:8080/v1/models

# 3. Verify Python env + deps
poetry env info
poetry install --with phase4   # adds chromadb cache for known UI patterns

# 4. Quick smoke
poetry run pytest tests/test_smoke.py -q
poetry run python run_agent.py --task config/tasks/smoke_test.yaml --skip-preflight
```

Expected: smoke task returns `status=success exit_reason=task_complete`.

---

## 2. Launching agents

### Single agent
```powershell
poetry run python run_agent.py --task config/tasks/<task>.yaml
```

### HITL dashboard (separate terminal)
```powershell
poetry run python -m hitl.server      # binds 127.0.0.1:8080
```

Open <http://127.0.0.1:8080/> to see every agent's pending reviews.

### 3-agent parallel (production pattern)
Each agent must have a **unique `agent_id`** — that drives the SQLite file
name (`data/db/<agent_id>.db`) and the audit log (`logs/audit/<agent_id>.ndjson`).

```powershell
$env:AGENT_ID="agent_01"; poetry run python run_agent.py --task config/tasks/case2_claim_search.yaml &
$env:AGENT_ID="agent_02"; poetry run python run_agent.py --task config/tasks/case3_form_fill.yaml &
$env:AGENT_ID="agent_03"; poetry run python run_agent.py --task config/tasks/case4_extract.yaml &
```

The dashboard auto-discovers all three.

---

## 3. Configuration switching

`.env` keys that change between environments (everything else is identical):

| Key | Dev (Ollama, CPU) | Prod (vLLM, GPU) |
|-----|-------------------|-------------------|
| `INFERENCE_URL` | `http://localhost:11434/v1` | `http://inference-server:8080/v1` |
| `MODEL_NAME`    | `minicpm-v:latest`          | `qwen2-vl-7b-instruct`            |

Other tunables (full list in [config/settings.py](../config/settings.py)):

- `CONFIDENCE_THRESHOLD` (default 0.75) — below this, a plan routes to HITL.
- `FINANCIAL_CONFIDENCE_THRESHOLD` (0.90) — never lower. CLAUDE.md non-negotiable.
- `HITL_TIMEOUT_MINUTES` (30) — runner abandons after this with `exit_reason=hitl_timeout`.
- `RDP_KEEPALIVE_SECONDS` (240) — must be **less than** the host's idle-lockout policy (A-08).
- `VLM_MAX_PAGES` (5) — extraction VLM tier budget per PDF.

---

## 4. Monitoring & on-call

### Where to look first
| Symptom | First check |
|---|---|
| Agent silent | `logs/audit/<agent_id>.ndjson` — last `event` field |
| Lots of HITL pauses | Dashboard → click agent → review `reason` column |
| RDP keeps disconnecting | A-08 / A-09 — check idle policy, monitor index |
| OCR tier never matches | Tesseract binary missing (A-13): `tesseract --version` |
| `ChromaKnowledgeStore` not used | A-12 — re-run `poetry install --with phase4` |

### Healthcheck commands
```bash
# Latest event per agent
ls logs/audit/*.ndjson | xargs -I{} sh -c 'echo {}; tail -1 {} | jq .event,.task_id'

# Pending HITL count by agent
curl -s http://127.0.0.1:8080/api/agents | jq

# Active tasks per agent DB
sqlite3 data/db/agent_01.db "SELECT task_id, status FROM tasks WHERE status='running' OR status='hitl_wait';"
```

### Audit log fields worth alerting on
- `event=hitl_routed` — every HITL pause
- `event=retry_limit_exceeded` — repeated failure (3×) on the same step
- `event=recovery_attempts_exceeded` — recovery bounded out
- `event=task_finalise` with `exit_reason != task_complete`

---

## 5. Common incidents

### "Agent stuck waiting for HITL but nobody is at the dashboard"
The runner times out after `HITL_TIMEOUT_MINUTES` and returns
`exit_reason=hitl_timeout`. The HITL row stays `pending` — to recover, open
the dashboard and resolve manually, then re-launch the task with the same
`task_id` (start_task is idempotent).

### "Two agents racing on the same SQLite file"
Don't share `agent_id`. The DB is per-agent (`data/db/<agent_id>.db`).
WAL mode handles two readers + one writer fine; cross-process writers to
the *same* file will eventually contend (A-18). Limit ≤ 10 parallel agents.

### "Browser session leaked after a crash"
`BrowserSession.__exit__` is fail-safe (nested try/finally per step) — but
if the host crashed, run:
```powershell
Stop-Process -Name chromium,chrome -Force
```

### "RDP keeps reconnecting in a loop"
Check `MAX_RECONNECTS=3` on `RDPHandler`. Beyond that the recovery layer
escalates to HITL. If creds rotated mid-task (A-10), refresh `.env` and
re-launch.

### "Knowledge store entries missing after a crash"
Expected — `KnowledgeStore` flushes only on `complete_task` (CLAUDE.md
non-negotiable; A-15). The action log in SQLite is authoritative.

---

## 6. Backup & retention

| Data | Path | Retention guidance |
|------|------|---------------------|
| Session DB | `data/db/*.db` | Backup nightly; rotate quarterly |
| Audit logs | `logs/audit/*.ndjson` | Append-only; ship to SIEM if available |
| Screenshots | `screenshots/` | Purge weekly unless attached to an open HITL |
| ChromaDB cache | `data/chroma/` | Rebuilds itself; safe to delete |
| Downloads | `downloads/` | Purge after the owning task completes |

Never back up `.env` to a non-secret store.

---

## 7. Deploying changes

1. `poetry run pytest tests/ -q` — must pass 100%.
2. `poetry run ruff check .` and `poetry run mypy .`.
3. Verify `config/locators/rdweb.py` was NOT touched without sim regression
   (CLAUDE.md: "DO NOT rewrite without testing against simulation first").
4. Confirm no `.env` or credential files in the diff.
5. Tag and ship. The agent has no migration system — running a newer
   binary against an older SQLite file is safe (schema is `CREATE IF NOT EXISTS`).

---

## 8. Escalation

- LLM giving wrong selectors → tune `config/locators/rdweb.py`, not the model.
- Financial confidence threshold breach → never lower the threshold. Find the
  upstream data quality issue.
- Anything CLAUDE.md says is non-negotiable → it's non-negotiable. Page the
  architect before deviating.
