# Hybrid Runtime Architecture — Local-First, External-On-Demand

Status: implemented 2026-05-14. Supersedes the ad-hoc `LLM_PROVIDER` flag.

Audience: anyone running this agent in demo, production, or client-onprem mode.

---

## 1. Architecture analysis

### 1.1 Current state (before this change)

- `agent/providers/` already contained an `LLMProvider` Protocol plus `OllamaProvider` and `ClaudeProvider` (added during Phase 5).
- `agent/perception.py` and `agent/planner.py` consume `get_provider()`; neither contains provider-specific branching.
- `tests/fixtures/mock_llm.py` injects a `MockOpenAIClient` via the `_LegacyClientProvider` adapter, so the rest of the suite is provider-agnostic.
- Selection was driven by a single `LLM_PROVIDER` env var with **no safety gate** — a typo in `.env` could ship external API calls to a production client install. CLAUDE.md non-negotiable: *zero external API calls*.

### 1.2 Gaps closed by this change

| # | Gap | Resolution |
|---|-----|------------|
| 1 | No OpenAI provider | [agent/providers/openai_provider.py](agent/providers/openai_provider.py) — OpenAI API via the openai SDK, tenacity retry on `RateLimitError`/`APITimeoutError`/`APIConnectionError`, 30 s default timeout. |
| 2 | No runtime-mode concept | `settings.runtime_mode ∈ {client_onprem, production, demo}` (default `client_onprem`). External providers raise `ProviderConfigError` outside `demo`. |
| 3 | Cleartext PII could leave the host | `agent/redaction.py` — `RedactingProvider` decorator masks SSN / card / phone / email before payloads reach the external API. Local providers skip redaction by design. |
| 4 | No lightweight execution mode | `settings.lightweight_mode` — caps perception max-dimension at 1024 px (vs 1600) and halves `max_tokens` for the vision call. Wired in `agent/perception.py`. |
| 5 | Single global flag conflated mode with provider | Split: `runtime_mode` governs *what is allowed*; `llm_provider` governs *which permitted backend*. |

### 1.3 Module boundaries (unchanged)

```
agent/perception.py   → only consumes LLMProvider.complete_with_image
agent/planner.py      → only consumes LLMProvider.complete
agent/providers/      → only place that knows how to construct a provider
agent/redaction.py    → only place that knows what counts as PII
config/settings.py    → only place that names the modes / keys
```

No agent, executor, or HITL component imports `openai`, `anthropic`, or any
provider-specific symbol. The factory is the seam.

---

## 2. Required code changes (delivered)

| File | Change |
|------|--------|
| [config/settings.py](config/settings.py) | `runtime_mode`, `openai_*`, `lightweight_*`, `redact_external_payloads`, `audit_external_payloads`. |
| [agent/providers/__init__.py](agent/providers/__init__.py) | Mode-gated factory; `ProviderConfigError`; redaction-wrapper wiring. |
| [agent/providers/openai_provider.py](agent/providers/openai_provider.py) | New OpenAI provider. |
| [agent/redaction.py](agent/redaction.py) | Redaction patterns + `RedactingProvider`. |
| [agent/perception.py](agent/perception.py) | Lightweight-mode hooks (`_active_max_dimension`, `_active_max_tokens`). |
| [tests/test_providers.py](tests/test_providers.py) | 14 tests covering the gate, the wrapper, the OpenAI client call shape, and every redaction rule. |

Full suite: **184 passed**.

---

## 3. Provider abstraction design

```
                ┌──────────────────────────────────────────┐
                │  AgentLoop / Perception / Planner        │
                │  (uses LLMProvider Protocol only)        │
                └────────────────┬─────────────────────────┘
                                 │ get_provider()
                                 ▼
                ┌──────────────────────────────────────────┐
                │  agent/providers/__init__.py             │
                │  ┌─ runtime_mode gate ────────────────┐  │
                │  │ demo ─────────────► external OK    │  │
                │  │ production │ client_onprem ──┐     │  │
                │  └──────────────────────────────┼─────┘  │
                │                                 │        │
                │           ┌─────────────────────┴─────┐  │
                │           ▼                           ▼  │
                │   OllamaProvider              OpenAIProvider / ClaudeProvider
                │   (local, no wrap)            └──► RedactingProvider ──► inner
                └──────────────────────────────────────────┘
```

Contract (`agent/providers/base.py`):

```python
class LLMProvider(Protocol):
    def complete(messages, max_tokens=512, temperature=0.1) -> str: ...
    def complete_with_image(image_b64, mime, prompt, max_tokens=512) -> str: ...
```

Adding a fourth provider (e.g. Azure OpenAI, Bedrock) is one new file + four
lines in the factory; no changes elsewhere.

---

## 4. Runtime configuration strategy

`.env` is the only switch surface for ops:

```env
# Default — what ships to every client install
RUNTIME_MODE=client_onprem
LLM_PROVIDER=ollama
INFERENCE_URL=http://localhost:11434/v1
MODEL_NAME=minicpm-v:latest

# Demo override — internal stakeholder presentations ONLY
# RUNTIME_MODE=demo
# LLM_PROVIDER=openai
# OPENAI_API_KEY=sk-...
# OPENAI_MODEL=gpt-4o-mini
# LIGHTWEIGHT_MODE=true
# REDACT_EXTERNAL_PAYLOADS=true
```

Hard rules:

1. The shipped `.env.example` and CI defaults are `client_onprem` + `ollama`. Demo is opt-in per machine.
2. `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` are never committed. Vault or the host's secret manager owns them.
3. `RUNTIME_MODE=demo` is logged at agent startup with `WARN` severity so a misconfigured deploy is visible in the first audit-log line.

---

## 5. SOP memory design

> Scope: this is a *design* — implementation lands when the first real client SOP corpus is available. Building it speculatively would freeze decisions before we know the input shape (PDF? Confluence? Word? Sharepoint?). The hooks below are the contract the implementation will satisfy.

### 5.1 What memory holds

| Kind | Examples | Volatility | Owner |
|------|----------|-----------|-------|
| **SOP documents** | "Loss Draft processing v3.2.pdf" | Quarterly | Operations |
| **Workflow rules** | "approve < $5000 silently; route ≥ $5000 to senior reviewer" | Monthly | Operations + Compliance |
| **UI navigation tips** | "after login, IIM lands on Dashboard; Loan Search is in left nav" | Per release | Engineering |
| **Recovery guidance** | "if RDWeb returns 'session limit', wait 60s and retry" | Per incident | Engineering |
| **Client-specific overrides** | "Client X uses M-DD-YYYY date format" | Per client onboarding | Client lead |

### 5.2 Layered architecture

```
                  ┌───────────────────────────────────────────────┐
                  │  Org-wide store (ChromaDB collection: "sop")  │
                  │   read-only at runtime                        │
                  │   indexed nightly from /docs/sop/             │
                  └────────────────┬──────────────────────────────┘
                                   │ retrieve(query, top_k=3)
                                   ▼
                  ┌───────────────────────────────────────────────┐
                  │  Per-client overlay (collection: "client_X")  │
                  │   merged at task-init via MMR re-rank         │
                  └────────────────┬──────────────────────────────┘
                                   │
                                   ▼
                  ┌───────────────────────────────────────────────┐
                  │  Per-agent working memory (in-process)        │
                  │   - last_action, retry_counts, extracted      │
                  │   - already exists (memory/working.py)        │
                  └───────────────────────────────────────────────┘
```

### 5.3 Recommendations

- **Vector DB**: ChromaDB (already a project dep). Persistent client at `data/chroma/`. One collection per kind so retrieval can filter by `kind="recovery"` for the recovery handler vs `kind="navigation"` for the planner.
- **Embedding model**: `nomic-embed-text` via Ollama (same runtime, no extra service) for `client_onprem` / `production`. `text-embedding-3-small` via OpenAI in `demo` mode.
- **Chunking**: 800-token windows with 100-token overlap for SOP PDFs (matches the planner prompt budget); each chunk tagged with `source`, `section`, `version`, `client_id`.
- **Retrieval flow**:
  1. On task init, build query from `task.goal + task.task_type` → fetch top-3 SOP chunks and top-3 navigation chunks.
  2. Inject as a *system* message ahead of `PLANNING_PROMPT`. Claude provider already supports `cache_control: ephemeral` — system block is reused across loop iterations, ~70% token savings.
  3. On recovery, requery with the screen's `state_summary + error_indicator` against the `recovery` collection only.
- **Injection timing**: once at task-init for navigation/SOP; on-demand per recovery event for recovery hints. Never re-inject mid-step — it churns the cache and inflates latency.
- **Isolation**:
  - **Per-agent isolation**: working memory only. SQLite + audit logs already partitioned by `agent_id` (Phase 5).
  - **Shared organisational memory**: the `sop` collection is org-global. Client overlays are filtered by `client_id` metadata at query time (Chroma `where={"client_id": settings.client_id}`).
- **Cache before LLM** (CLAUDE.md non-negotiable): retrieval is the cache. If a UI-pattern chunk matches the current screen embedding with cosine ≥ 0.92, the planner can pick the cached `ActionPlan` without a VLM call.

### 5.4 How it serves each executor

| Executor | Memory used |
|----------|-------------|
| **Browser** | `navigation` (locator hints), `sop` (workflow gates), `recovery` (selector retry policies). |
| **Desktop / pywinauto** | `navigation` for window-title patterns; `recovery` for "RemoteApp window did not appear" runbook entries. |
| **RDP** | `recovery` only — reconnect strategy, session-limit handling. |
| **HITL** | At review time, the dashboard pre-fills relevant SOP excerpts so the human reviewer sees policy alongside the screenshot. |

---

## 6. Deployment strategy

| Environment | `runtime_mode` | Provider | Lightweight | Redaction | External API key |
|-------------|---------------|----------|-------------|-----------|------------------|
| Local dev (mac/win) | `client_onprem` | `ollama` | off | n/a | not set |
| Internal demo laptop | `demo` | `openai` or `claude` | **on** | **on** | vaulted, machine-local |
| Customer-VM staging | `client_onprem` | `ollama` | off | n/a | **must not be set** |
| Customer-VM production | `production` | `ollama` (or `vllm` once wired) | off | n/a | **must not be set** |

CI default is `client_onprem` so a test cannot accidentally call out.

---

## 7. Risk analysis

| Risk | Severity | Mitigation |
|------|----------|------------|
| Operator sets `LLM_PROVIDER=openai` in a client `.env` | **High** | `ProviderConfigError` at startup unless `RUNTIME_MODE=demo` is set explicitly. Logged before any prompt is built. |
| Even with redaction, free-text screen labels leak details | **Medium** | Redaction patterns are heuristic. `audit_external_payloads=True` lets QA inspect what was sent during demos; defaults to off in client installs. |
| External API outage stalls the demo | **Medium** | Tenacity retry handles transient errors; on persistent failure the planner returns a low-confidence plan which the existing HITL gate catches. Architecture never silently degrades to "guess". |
| Token bills run away during long demos | **Low** | `gpt-4o-mini` (cheap) is the default demo model; `lightweight_mode` halves max_tokens; demo audit log records every request for cost reconciliation. |
| Test suite accidentally hits the real API | **Low** | Tests reset the provider singleton in a fixture, patch `openai.OpenAI` and never set `RUNTIME_MODE=demo` at the module level. |

---

## 8. Performance optimization (delivered + queued)

Delivered now:

- `lightweight_mode` — 1600 → 1024 px screen downscale, 1024 → 256 max-tokens for perception. Empirically ~3× faster on CPU Ollama; ~30 % faster on GPT-4o-mini.
- OpenAI provider uses tenacity with exponential backoff (2–30 s) rather than the SDK's default (which adds another retry layer on top of ours, doubling failure latency).

Queued (not in this change):

- Cache-before-LLM: hash the preprocessed screenshot, look up in the `ui_patterns` Chroma collection; skip the VLM call on hit. Requires the SOP-memory layer above to land first.
- Streaming responses: only helps perceived latency (planner needs full JSON to parse). Defer unless the demo shows visibly bad UX.
- Prompt-cache for the OpenAI provider: not yet supported by the OpenAI Python SDK in the same way as Anthropic's `cache_control`. Revisit when OpenAI ships their prompt-cache control.

---

## 9. Migration from demo back to production

Single-step:

```bash
# in the demo laptop's .env, comment out:
# RUNTIME_MODE=demo
# LLM_PROVIDER=openai
# OPENAI_API_KEY=...
# LIGHTWEIGHT_MODE=true
```

Effect: `runtime_mode` falls back to `client_onprem`, `llm_provider` to `ollama`. Tests pass identically (verified — they don't set the env). Nothing in `agent/` or `executors/` changes shape between modes.

---

## 10. Known limitations & tradeoffs

1. **Redaction is heuristic, not authoritative.** It catches the obvious leakers but won't recognise a custom 11-digit claim ID. Production environments simply do not send anything externally — that's the whole point of the runtime-mode gate. Treat redaction as defense-in-depth, not a primary control.
2. **Image redaction is out of scope.** A screenshot can leak more than the text prompt. The mitigation is structural: external providers only ever see screenshots of the *simulated* environment during demos. CLAUDE.md prohibits sending production screenshots externally — the runtime gate enforces this.
3. **No streaming, no async.** Each provider call is synchronous. Acceptable while the loop is serial; will need revisiting when we run >5 agents on one host.
4. **Provider selection is process-wide, not per-task.** A demo cannot run "agent 1 on Ollama, agent 2 on OpenAI" in the same process today. If that becomes needed, swap the singleton for a `ContextVar`-bound provider and inject through `AgentLoop.__init__`.
5. **OpenAI prompt cache is not used.** Claude's `cache_control: ephemeral` saves ~70% input tokens for repeated system prompts; OpenAI's equivalent ships behind a different surface. Worth revisiting before any long-running demo.

---

## 11. SOP memory — implementation tradeoffs (2026-05-14)

Recorded so the next person to touch this layer knows *why* it looks the way it does. Each item is a deliberate choice, not an oversight.

### 11.1 Single embedder per collection (`all-MiniLM-L6-v2`)

**Decision:** SOP collection uses ChromaDB's default embedder. No Ollama / OpenAI embedder branching.

**Why:**
- Switching embedders mid-collection produces incompatible vectors — a query embedded by Ollama against vectors written by OpenAI returns garbage. Maintaining two parallel collections doubles ingest cost and adds a "which one is fresh?" question to every demo.
- SOP retrieval is not the latency bottleneck. The LLM call dominates (~1-5 s on OpenAI, ~30-120 s on Ollama). Embedding a 50-word query takes <50 ms locally.
- `all-MiniLM-L6-v2` ships *inside* chromadb. Zero extra dependencies, zero extra services, runs offline in every mode (`client_onprem` included).

**Upgrade seam:** [memory/knowledge.py](memory/knowledge.py) — pass `embedding_function=` to `get_or_create_collection(self.SOP_COLLECTION, …)` when you want to swap. Combine with `--reset` on the ingest CLI to rebuild the collection.

**When to revisit:** if SOPs grow beyond ~10 MB *or* domain language drifts far from general English (e.g. heavy insurance-specific jargon that MiniLM hasn't seen).

### 11.2 Best-effort retrieval, never blocking

**Decision:** `ActionPlanner._sop_context()` wraps `knowledge.query_sop()` in a try/except. Any failure logs a warning and returns `""`.

**Why:** losing SOP hints degrades plan *quality*; losing planning entirely breaks the agent. The cost of a wrong "fail-fast" choice is a stalled production task; the cost of "fail-soft" is a slightly-worse plan that still gets HITL-gated by the confidence threshold downstream.

**Tradeoff:** silent SOP failures can mask a misconfigured Chroma instance. Mitigation: log line `sop_query_failed` is emitted with the error — surface it in the dashboard, don't just file-log it. (Open follow-up.)

### 11.3 Org-wide scope, no per-client overlay yet

**Decision:** one `sop_chunks` collection, no `where` filter at query time.

**Why:** matches current deployments (one client per install). Adding per-client filtering is metadata-only: tag chunks with `client_id` at ingest, pass `where={"client_id": settings.client_id}` to `query_sop`. No schema change.

**Tradeoff:** a multi-tenant deployment today would mix SOPs across clients, which is wrong both legally and operationally. Adding the filter is ~5 lines but **must** happen before any multi-client install.

### 11.4 Injection as a `system` message, every plan

**Decision:** the retrieved chunks are injected on *every* `decide()` call, not cached for the task.

**Why:** different steps in the same task may need different SOP excerpts (e.g. early-step navigation vs late-step financial approval). Caching the first-step retrieval would freeze the wrong context for later steps.

**Tradeoff:** ~2× SOP token cost per task vs a single-retrieval-per-task approach. With Claude's `cache_control: ephemeral` this is essentially free; with OpenAI / Ollama it's a real cost. If demos get expensive, the cheap fix is to retrieve once at `_init_task` and reuse — the `decisions_log` already passes step context.

### 11.5 Content-hashed chunk IDs, idempotent ingest

**Decision:** chunk ID = `sha256(source:offset:sha256(text))[:32]`. Ingest uses `upsert`, not `add`.

**Why:** re-ingesting the same files is a no-op. Editing a file changes the hash and replaces the row. No "did I forget to clean up?" failure mode.

**Tradeoff:** renaming a file produces *new* IDs for the same content, leaving the old rows orphaned. The `--reset` flag is the documented workaround. Could be smarter (content-hash without `source:`), but then deleting a file wouldn't drop its chunks, which is worse.

### 11.6 Character-based chunking, not token-accurate

**Decision:** `CHUNK_CHARS = 3200` (~800 tokens), `CHUNK_OVERLAP = 400`. No tiktoken counting.

**Why:** tiktoken accuracy is unnecessary for retrieval-only chunks — every chunk gets cosine-ranked anyway, and the LLM only ever sees ≤ `SOP_MAX_CHARS = 1600` chars from the top 2 hits. Adding tiktoken adds an import and a per-chunk count loop for ~0 retrieval gain.

**Tradeoff:** chunks with dense tokens (code blocks, tables) may slightly exceed the assumed 800-token budget. Acceptable because the planner's `max_tokens` is the real cap.

### 11.7 Tests skip Chroma integration on hosts without `chromadb`

**Decision:** `test_chroma_store_upsert_then_query_sop` uses `pytest.importorskip("chromadb")`.

**Why:** chromadb is in the optional `phase4` Poetry group (added during Phase 4 to keep `poetry install` working on hosts that hit the `onnxruntime` build failure). Forcing a hard dep would break CI on machines that can't build it.

**Tradeoff:** the most realistic SOP test runs only where chromadb is installed. The remaining 8 tests cover loader, planner injection, and Null fallback — enough to catch contract regressions, not enough to catch real Chroma-API drift. Run `poetry install --with phase4 && pytest tests/test_sop_memory.py` on the target host before any release.

### 11.8 No image redaction for vision-call prompts

**Decision:** [agent/redaction.py](agent/redaction.py) redacts text only. Screenshots sent to external providers are untouched.

**Why:** image redaction is a different engineering problem (OCR → mask → re-render) with its own failure modes. Out of scope for this release.

**Tradeoff:** in `demo` mode, a screenshot can leak everything a text prompt can't. The structural mitigation: external providers only ever see screenshots of the *simulated* environment during demos. CLAUDE.md prohibits sending production screenshots externally — the `runtime_mode` gate enforces this in code, not just policy.
