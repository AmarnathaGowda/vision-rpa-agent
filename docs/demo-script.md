# Vision RPA Agent — Demo Script

Target length: **~12 minutes**, three acts. Assumes a clean macOS/Windows
dev box, vendored sim pages, Ollama running locally.

---

## Pre-flight (do this BEFORE the audience joins)

```bash
# 1. Confirm everything is healthy
ollama serve &
poetry run pytest tests/ -q          # expect 100% green
poetry run python -m hitl.server &   # dashboard on 127.0.0.1:8080
```

Open three terminals + one browser tab on the dashboard. Have the architecture
diagram from [docs/architecture.md](architecture.md) ready in another tab.

---

## Act 1 — The non-negotiables (2 min)

Talking points:

> *"Three principles drive every design decision in this codebase, and
> they're written into [CLAUDE.md](../CLAUDE.md) so the LLM can't drift away
> from them either."*

1. **Zero external API calls.** Show `INFERENCE_URL=http://localhost:11434/v1`.
   No Anthropic, no OpenAI. Insurance data never leaves the host.
2. **Confidence ≥ 0.90 for financial fields.** Show
   [agent/planner.py:95-106](../agent/planner.py#L95) — the rule is enforced
   *after* the model's judgement, not in the prompt.
3. **Human approval on every write for first 10 runs.** Show the HITL
   dashboard. Anything below threshold pauses the agent here.

---

## Act 2a — Live VLM run against the local sim (5 min)

If a local sim is up (LossDraft on `:8000/lossdrafts/document-management`,
IIM on `:8000/proctor/loan-search`, RD Web on
`:8000/rdweb/pages/en-US/login.aspx`), use the VLM-driven task YAMLs:

```bash
ollama serve &
ollama pull minicpm-v          # first run only
poetry run python run_agent.py --task config/tasks/sim_live_lossdraft.yaml
poetry run python run_agent.py --task config/tasks/sim_live_iim.yaml
poetry run python run_agent.py --task config/tasks/sim_live_rdweb.yaml
```

These have no deterministic `steps:` — the agent runs the real
observe → reason → act loop, so the audience sees mss capture, VLM
inference, and Playwright execution actually firing. The RD Web task is
designed to trip HITL on the password field, which is the perfect cue
to switch over to the dashboard demo.

## Act 2b — Watch an agent work (5 min)

### Step 1: Happy-path run against the vendored sim

```bash
LD_BASE_URL=file://$PWD/tests/sim/pages \
  poetry run python run_agent.py --task config/tasks/claim_search.yaml --skip-preflight
```

Talking points while it runs:
- "Six deterministic steps: navigate → type → click → wait → read → extract."
- "Every step writes a checkpoint to SQLite — if I kill the agent now, the
  next run resumes from the last completed step."
- "The audit log is append-only NDJSON. Every perception, plan, action,
  and recovery directive is recorded with a UTC timestamp."

Show the final line: `agent_complete status=success exit_reason=task_complete steps=6`.

Then `tail logs/audit/agent_01.ndjson | jq .` to demonstrate the event stream.

### Step 2: Trigger a HITL pause

```bash
# Same task, but lower the threshold so a plan trips HITL
CONFIDENCE_THRESHOLD=0.99 \
  poetry run python run_agent.py --task config/tasks/claim_search.yaml --skip-preflight
```

Switch to the browser tab on <http://127.0.0.1:8080/>. Talking points:
- "The dashboard discovered the agent's DB automatically — there's no
  central registry. It just scans `data/db/*.db`."
- Open the review. Show the reason string ("plan confidence 0.85 below 0.99")
  and the JSON context (the plan + screen state at the moment of the pause).
- Choose **approve** to retry, or **correct** with `{"claim_no": "CLM-9"}`
  to override.

The agent prints `task_resume` and finishes. Highlight the HITL row is now
`resolved` and the task moved back to `running`.

---

## Act 3 — Run three agents at once (3 min)

```bash
AGENT_ID=agent_demo_1 poetry run python run_agent.py --task config/tasks/login.yaml --skip-preflight &
AGENT_ID=agent_demo_2 poetry run python run_agent.py --task config/tasks/form_fill.yaml --skip-preflight &
AGENT_ID=agent_demo_3 poetry run python run_agent.py --task config/tasks/extract_pdf.yaml --skip-preflight &
```

Refresh the dashboard. Three agents appear, each with their own row.
Talking points:
- "Each agent gets its own SQLite file in WAL mode, its own audit log,
  its own screenshot folder. No shared writers, no contention."
- "The dashboard is the only place state from all three agents converges,
  and even that's read-mostly — resolutions write back to the originating
  agent's DB."

---

## Act 4 — Recovery & robustness (2 min)

Run the error-injection suite live:
```bash
poetry run pytest tests/test_error_injection.py tests/test_e2e_full.py -v
```

Talking points:
- "Every category of failure has a contracted destination:
  executor exception → recovery → HITL (no silent drops).
  Blocking modal → bounded retry → HITL.
  RDP disconnect → reconnect ≤ 3× → HITL.
  Low financial confidence → HITL, period."
- "The HITL apply step now validates *before* mutating working memory, so
  a malformed resolution leaves the review pending instead of silently
  swallowing it. Found and fixed during Phase 6 hardening."

---

## Closing (30 sec)

> *"The whole pipeline is observe → reason → act → store. Every layer is
> swappable: Playwright today, Selenium tomorrow; Ollama today, vLLM in prod;
> SQLite today, Postgres if we ever outgrow it. The non-negotiables stay
> the same."*

Open [docs/architecture.md](architecture.md) one last time, point at the
data-flow diagram, and stop.

---

## If something goes wrong

| Symptom | Recovery |
|---|---|
| Ollama not responding | `pkill ollama && ollama serve &` and re-run |
| Dashboard 500 | `pkill -f 'hitl.server' && poetry run python -m hitl.server &` |
| Sim browser hangs | `Stop-Process -Name chromium -Force` (Windows) or `pkill chromium` (mac) |
| Tests fail live | Skip Act 4; reference the green CI run instead |
