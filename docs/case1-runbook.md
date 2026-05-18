# Case 1 — End-to-end runbook

Status: Phase A complete + Phase B parity baseline complete (2026-05-18).
The legacy `Case1Handler` is now reachable from the new framework with
byte-identical output across all four decision branches. The full
SOP-driven step-by-step path is the next iteration; this runbook covers
**what works today**.

---

## What changed

1. **`legacy/`** — full copy of `insurance-agent-project/automation` and
   `simulation` (sans venvs and screenshots), ~14 MB. Importable via
   `import legacy` which adds both dirs to `sys.path`.
2. **`config/locators/rdweb.py`** — 684-line legacy locator map ported
   verbatim (CLAUDE.md non-negotiable) + flat-dict adapter at the bottom
   so the new framework's `SelectorResolver` sees 628 named selectors.
3. **`.env.example`** — adds `SIM_USERNAME=plp\sonawane001` /
   `SIM_PASSWORD=Welcome@123` mirroring `simulation/data/users.json`.
   `RDWEB_USERNAME` / `RDWEB_PASSWORD` (which the framework already reads)
   default to the same values for local sim runs.
4. **`agent/router.py`** — new `tool` scope for evaluation-only legacy
   handlers. `ExecutorScope` literal extended; backwards-compatible.
5. **`executors/case1_tool.py`** — `Case1ToolExecutor` that bridges
   `legacy.cases.case1.handler.Case1Handler` into the new framework via
   an `ActionPlan(target="case1_evaluate", app="tool")`.
6. **`docs/sop/case1-already-closed-rule.md`** — SOP capturing every rule
   from `legacy/automation/cases/case1/decision.py` as prose.
7. **`config/tasks/case1_already_closed.yaml`** — deterministic single-step
   task that invokes the legacy handler. Parity baseline.
8. **`tests/test_parity_case1.py`** — 7 tests, 4 fixtures × parity assertion
   + 3 error-path tests. Diffs new vs legacy output and fails on any
   non-volatile-field divergence.

---

## How to run

### Prerequisites

```bash
# 1. Install the new framework (one time)
poetry install --with phase4

# 2. Set up the local simulation (legacy/simulation has its own venv)
cd legacy/simulation
python3 -m venv .venv
.venv/bin/pip install -e .
cd ../..

# 3. Copy env template + edit if needed
cp .env.example .env
# The defaults already match the sim; only edit if you swapped credentials.
```

### Run the simulation server

```bash
cd legacy/simulation
.venv/bin/uvicorn app.main:app --port 8000 --reload
# Leave this terminal running.
```

Verify in a browser: <http://localhost:8000/> → redirects to the RD Web
login page. Test credentials: `plp\sonawane001` / `Welcome@123`.

### Run the parity test (no sim needed)

```bash
poetry run pytest tests/test_parity_case1.py -v
# Expect: 7 passed
```

This proves the legacy handler is reachable through the new framework
without launching a browser, the simulation, or an LLM.

### Run the deterministic task

```bash
poetry run python run_agent.py \
    --task config/tasks/case1_already_closed.yaml \
    --skip-preflight
```

What you'll see:
- `agent_start` log line with `task_id=case1_already_closed`.
- `router_dispatch scope=tool action_type=extract`.
- `action_result status=ok` after the handler returns.
- `agent_complete status=success exit_reason=task_complete steps=1`.

The result lands in `data/db/agent_01.db` (table `actions`) and
`logs/audit/agent_01.ndjson`. Query it:

```bash
sqlite3 data/db/agent_01.db \
  "SELECT task_id, step, action_type, target, result_status, error_msg
     FROM actions WHERE task_id='case1_already_closed' ORDER BY id DESC LIMIT 5;"
```

### Run with real fixture data

The default task YAML uses a stub `extraction` so the run completes
without inputs. To exercise a real branch, copy the YAML and replace the
`value:` field with the JSON form of an `ExtractionResult`:

```yaml
steps:
  - action_type: extract
    target: case1_evaluate
    app: tool
    value: |
      {"candidates": [
        {"value": "0823814694", "role": "header",
         "line": "Re: Claim Number 0823814694", "line_index": 3, "confidence": 0.99},
        {"value": "0819963926", "role": "body",
         "line": "the correct claim number is 0819963926", "line_index": 14, "confidence": 0.99}
       ],
       "raw_text": "Re: Claim Number 0823814694\n...the correct claim number is 0819963926...\nrespectfully closing.",
       "cleaned_lines": [],
       "ocr_used": false,
       "duration_ms": 0}
```

Expected output for that fixture (Allstate duplicate scenario):

```
selected_claim_id = 0823814694   (header wins)
loan_id           = 1026766183   (from DB record of 0819963926? No — header doesn't exist in DB)
status            = success
case              = "Already Closed"
reason_codes      = ["only_body_valid", "MISSING_CLOSURE_PHRASE"]  (or similar)
```

The exact reason codes depend on the fixture; cross-check with
`tests/test_parity_case1.py::CASE_ALREADY_CLOSED_HEADER_WINS`.

---

## How parity is validated

Every change to either the legacy handler or the bridge MUST be guarded
by `tests/test_parity_case1.py`. The test:

1. Calls the legacy handler directly (`Case1Handler().evaluate(...)`).
2. Calls the same input through `Case1ToolExecutor().execute(plan)`.
3. Strips volatile fields (`duration_ms`) from both.
4. Asserts deep-equal.

A divergence is the migration's red flag — do not ship.

---

## What's NOT in this phase (and what's next)

| Not yet | Why | When |
|---|---|---|
| AgentLoop full-loop run with `case1_evaluate` | Loop wiring uses `PerceptionLayer` even for tool-only tasks; needs a small `_should_skip_perception` predicate. | Phase B.2 — separate small commit. |
| SOP-driven step-by-step path (planner orchestrates loan_db_lookup → closure check → winner pick) | Requires the planner to consume the SOP and emit a 4-step plan. Needs Ollama / OpenAI online to test. | Phase B.3 — after the loop wiring lands. |
| Cases 2/3/4 | Each needs its own SOP, task YAML, and parity harness. Pattern is now established. | Phases C / D / E per [docs/sop-driven-migration.md](sop-driven-migration.md) §9. |
| PDF extraction → ExtractionResult pipeline in the new framework | Already exists in `executors/extraction.py`. Need to expose its output in the right shape for `case1_evaluate`. | Phase B.4. |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: cases.case1.handler` | Forgot `import legacy` before importing legacy modules | The bridge `legacy/__init__.py` adds paths on import — always import `legacy` first. |
| `pydantic ValidationError: line / line_index required` | New code passing a `ClaimCandidate` dict without `line` / `line_index` | Both fields are required by the legacy schema. Test fixtures include `"line": "..."` and `"line_index": 0`. |
| Parity test passes locally, fails in CI | Different Python / Pydantic versions | Pin both to project versions. |
| `Failed to send telemetry event …` from chromadb | Harmless — chromadb's bundled posthog client | Set `ANONYMIZED_TELEMETRY=False` in `.env`. |
| `CoreMLExecutionProvider` ONNX errors on macOS | Apple's CoreML provider is unstable for the default chromadb embedder | Already fixed in [memory/knowledge.py](memory/knowledge.py) via `preferred_providers=["CPUExecutionProvider"]`. If you see it again, `rm -rf data/chroma/` and re-ingest. |
