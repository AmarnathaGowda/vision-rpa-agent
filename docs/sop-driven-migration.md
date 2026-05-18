# SOP-Driven Migration — `insurance-agent-project` → `vision-rpa-agent`

Status: design, 2026-05-18.
Audience: anyone migrating one of the four legacy cases onto the SOP-driven runtime.

This document is grounded in the actual code at
`/Users/abc/Desktop/Drive_Data/insurance-agent-project/automation/cases/` and
references real files/lines so the migration plan is verifiable, not aspirational.

---

## 1. Current architecture analysis (what's actually there)

### 1.1 Layout

```
insurance-agent-project/automation/cases/
├── base.py                      # CaseHandler Protocol — evaluation only
├── case1/                       # 4 files,  pure logic (no Playwright)
│   ├── decision.py
│   ├── handler.py
│   ├── loan_db.py
│   └── schema.py
├── case2/                       # stage7-10 + validators + dialogs
├── case3/                       # 13 stage_*.py + services + validators + ui
└── case4/                       # 10 stage_*.py + 8 service.py + 2 validators
                                   total ~5 300 LoC across cases
automation/demo/run_caseN_e2e_demo.py   # orchestrator per case
```

### 1.2 Concrete patterns observed

| Pattern | Evidence | Cost |
|---|---|---|
| **One Python module per UI step** | `case3/stage1_remote_login.py`, `stage2_remote_workspace.py`, … through `stage13` — 13 separate files | Adding a new step = new file + import + orchestrator edit. |
| **Hardcoded selectors imported from `locators/rdweb.py`** | `case3/stage1`:38, `from locators.rdweb import Login, RemoteApps` then `page.fill(Login.USERNAME, …)` | A test-id rename breaks every stage that uses it; no fallback. |
| **Hardcoded waits** | `case3/stage1`:42 `page.wait_for_timeout(400)`, 250, 200, 200, 300, 500, 600 — eight magic numbers in one stage | Brittle to network jitter; either too slow or too fast. |
| **Imperative orchestration** | `run_case3_e2e_demo.py` imports stage1…stage13 and calls them in sequence | A missing intermediate stage is silent; reordering is editing source. |
| **Per-case Pydantic result schemas** | `Case1Result`, `Case3InitResult`, `IIMLoanDetail`, `HoldCheckOCRResult`, `BorrowerClaimMatchResult` …  ≥ 12 schemas across the 4 cases | Cross-case reporting needs glue; each new case adds N more schemas. |
| **Constants embedded in modules** | `case3/schema.py`: `CASE3_BATCH = "42650"`, `CASE3_CLAIM_ID = "653943257-0001"`, … 11 constants | Changing the simulated dataset requires editing source. |
| **Decision logic in `decision.py` per case** | `case1/decision.py` has `choose_winner`, `detect_closure_phrase`, `is_already_closed` | These are domain rules the agent should be able to *learn from an SOP*, not have hard-coded. |
| **Services + validators split per case** | `case3/services/borrower_matcher.py`, `iim_result_matcher.py`, … plus `validators/iim_claim_validator.py` | Helpful internally but enforces case-specific code paths. |
| **CaseHandler Protocol is evaluation-only** | `base.py` shows `evaluate(extraction, *, llm)` — runtime workflow is *not* in the protocol | The protocol gates only the *interpretation* of data; the *acting* on data is hardcoded in stages. |

### 1.3 What prevents scalability today

1. **No execution abstraction.** A stage is a function; orchestration is import order. You can't reorder, retry, or recover at the framework level — only inside each stage.
2. **No common state.** Stage 1 returns a bool; stage 4 returns `HoldCheckOCRResult`; stage 9 takes both. Stage outputs flow as positional args. A new case has to invent its own plumbing.
3. **Selectors and timings are facts of the source code.** A UI redesign or a slow VM = a code change.
4. **Decision logic lives in Python.** "Header beats body when both resolve to the same loan" ([case1/decision.py](insurance-agent-project/automation/cases/case1/decision.py)) is an SOP rule expressed as code. New rules from operations cannot land without a developer.
5. **No memory.** Re-running case 3 on a new client document re-discovers everything; no successful selector patterns or recovery hints persist.
6. **Recovery is per-stage `try/except`.** `case3/stage9_iim_loan_open.py`:35 wraps the `wait_for_selector` and returns `(False, detail)` — no retry, no fallback selector, no HITL handoff. Each stage reinvents this badly.

---

## 2. Target architecture (already built — `vision-rpa-agent`)

The vision-rpa-agent project (Phases 0-6, 192 tests passing) is the target runtime. Every primitive the SOP-driven design asks for already exists:

| Need | Existing component |
|---|---|
| Generic action primitives | [agent/schemas.py](agent/schemas.py): `ActionType ∈ {click, type, navigate, read, extract, wait, select_option, file_navigate, file_open, extract_pdf, read_excel, rdp_launch, rdp_reconnect, flag_human, …}` |
| Per-app executor routing | [agent/router.py](agent/router.py): `ActionRouter` + `ROUTING_TABLE` chooses BrowserExecutor / DesktopExecutor / RDPHandler / FileExecutor by `action_type` or explicit `plan.app` |
| Observe → reason → act → store loop | [agent/loop.py](agent/loop.py): `AgentLoop._run_loop()` |
| Vision-based screen understanding | [agent/perception.py](agent/perception.py): mss + VLM → `ScreenState(app_type, summary, elements, error_present, blocking_modal, confidence)` |
| Provider-agnostic LLM | [agent/providers/](agent/providers/): Ollama (local), OpenAI, Claude. Mode-gated. |
| SOP retrieval at plan time | [memory/knowledge.py](memory/knowledge.py): `ChromaKnowledgeStore.query_sop()` injected as `system` message in [agent/planner.py](agent/planner.py) |
| Recovery handler | [agent/recovery.py](agent/recovery.py): typed `RecoveryDirective(retry/skip/rdp_reconnect/hitl/abort)` |
| HITL pause/resume | [hitl/queue.py](hitl/queue.py) + [hitl/runner.py](hitl/runner.py) + [hitl/server.py](hitl/server.py) |
| Checkpoint + audit | [memory/session.py](memory/session.py) (SQLite per agent) + [agent/audit.py](agent/audit.py) (NDJSON) |
| Multi-agent isolation | one `SessionMemory(agent_id)` + one audit file per agent; dashboard aggregates |

The migration is **not** "build an SOP runtime"; the SOP runtime exists. The migration is **"express each of the 4 cases as SOPs + a thin task definition and stop calling stageN code"**.

### 2.1 Mapping the rule-based pieces to the SOP runtime

| Rule-based concept | SOP-driven equivalent |
|---|---|
| `stageN_*.py` function | Steps the planner derives from the SOP + current screen state. The function does NOT migrate one-to-one; many stages collapse into single planner decisions. |
| `locators/rdweb.py` constants | Stay — referenced by `BrowserExecutor` through `SelectorResolver` as the cache-first lookup. **Do not rewrite** (CLAUDE.md). |
| `decision.py` per case | Becomes SOP prose: "Header claim ID wins when both header and body resolve to the same loan; if they resolve to different loans, status is ambiguous." Retrieved by the planner. |
| `validators/` and `services/` | Stay as Python — these are pure functions. Expose them through `ActionPlan.action_type="extract"` or `"read"` with a `target` that names the validator. Treat them as tools the planner can invoke, not as workflow steps. |
| Per-case Pydantic result schemas | A single generic result schema in `WorkingMemory.extracted_values: dict` plus per-task YAML that lists which fields constitute "task complete". |
| `run_caseN_e2e_demo.py` orchestrator | A single goal + task YAML + the agent loop. No per-case orchestrator. |
| Hardcoded waits | Recovery handler + `wait` action with semantic targets ("wait for the loan details page"); the perception layer detects load state. |
| Hardcoded test data (`CASE3_BATCH = "42650"`, …) | A `fixtures.yaml` per case the YAML references at runtime; or absent entirely if the agent reads them from the document. |

---

## 3. SOP authoring strategy

The SOP corpus is the source of truth. Authoring discipline matters more than infrastructure; the ingest pipeline already exists ([memory/ingest_sop.py](memory/ingest_sop.py)).

### 3.1 One SOP file per business workflow, not per case

The four legacy "cases" are workflows. Use that as the file boundary:

```
docs/sop/
├── 01-rdweb-login-and-workspace.md       # used by every case
├── 02-loss-draft-claim-search.md         # cases 2, 3, 4
├── 03-iim-loan-search.md                 # case 3
├── 04-file-explorer-hold-check.md        # case 3
├── 05-claim-letter-request.md            # case 2
├── 06-claim-document-management.md       # cases 2, 4
├── 10-already-closed-rule.md             # case 1 evaluation rules
├── 20-recovery-rdweb-session-expired.md
└── 99-shared-glossary.md                 # claim ID format, status codes, etc.
```

Cross-cutting recovery, glossary, and rule files are read on every retrieval — the planner queries `goal + screen_summary` and gets ranked SOP chunks regardless of which file they came from.

### 3.2 Required content per SOP (the "shape")

Each file needs four sections so retrieval consistently surfaces the right context:

```markdown
# <Workflow name>

## Purpose
<One paragraph: when does this workflow run; what's the success criterion.>

## Preconditions
- The agent is on <screen X>
- The following data is in working memory: <field A>, <field B>

## Steps (intent, not selectors)
1. <Click the X — describe what to look for, e.g. "the Sign-In button at the bottom of the login form">
2. <Type the username from working_memory.rdweb_username into the username field>
3. <Wait for the production RemoteApps grid to appear; the page shows tiles for Loss Drafts, Explore, etc.>
…

## Decision points
- If the page shows "Session has expired", run workflow [[20-recovery-rdweb-session-expired]].
- If two claim IDs are present, the header value wins when both resolve to the same loan; route to HITL when they resolve to different loans.

## Done when
- Working memory contains `loan_id`, `loan_status`.
- The current URL ends with `/loan/details`.
```

The planner reads the intent ("the Sign-In button at the bottom of the login form") and pairs it with `ScreenState.visible_elements` to pick a selector. The locator map (`config/locators/rdweb.py`) is consulted as a cache.

### 3.3 Decision-point translation table

| Legacy file | SOP section it becomes |
|---|---|
| `case1/decision.py::choose_winner` | `10-already-closed-rule.md` § Decision points: "Header beats body when same loan; ambiguous when different loans." |
| `case3/services/iim_result_matcher.py` | `03-iim-loan-search.md` § Decision points: how to match against borrower name + carrier when multiple hits are returned. |
| `case4/claim_task_validator.py` | `05-claim-letter-request.md` § Done when. |
| `case2/validators/*` | `06-claim-document-management.md` § Decision points + § Done when. |

The Python files don't get deleted; they survive as **callable tools** under an `extract` or `read` action_type, invoked by the planner when the SOP says "validate the IIM result against the OCR". Migration is *replacing the orchestration*, not deleting the validators.

---

## 4. Dynamic execution engine — how it actually runs

The runtime already exists. Walking through a task end-to-end against the new design:

```
            (1) load task YAML
            ─────────────────────────────────────────
            task_id: case3_hold_check
            goal: |
              Process the Case 3 Hold Check workflow. Log in to RD Web,
              open the MotownPLP RemoteApps Production workspace, navigate
              to the Hold Check PDF for batch 42650, extract claim_id and
              amount, then verify them against the IIM loan and LD claim.
            fixtures:
              borrower_name: "DIANE S BISSETT"
              expected_loan_no: "9703503582"
            done_when:
              all_set: ["claim_id", "amount", "iim_loan_no", "ld_claim_no"]
            ─────────────────────────────────────────

            (2) AgentLoop initialises SessionMemory(agent_id) + WorkingMemory
                seeded with fixtures.

            (3) Loop iteration:
                  observe — capture screen → VLM → ScreenState
                  reason  — query SOP collection with (goal + state_summary)
                          - inject top-2 SOP chunks as system message
                          - planner returns ActionPlan
                  act     — ActionRouter dispatches to BrowserExecutor /
                            DesktopExecutor / FileExecutor / RDPHandler
                  store   — checkpoint working memory + audit append
                  recover — RecoveryHandler runs pre-action (modals) and
                            post-action (failed results)

            (4) Loop exits when working_memory.task_complete OR
                done_when condition met OR step cap.

            (5) HITLRunner re-enters loop after every dashboard resolution.
```

The agent does **not** know about cases. It knows about goals, screens, and SOP chunks.

### 4.1 How UI adaptation actually happens

Concrete example — the legacy code does:

```python
# case3/stage1_remote_login.py:46
page.fill(Login.USERNAME, rdweb_creds.username)
```

If the form changes and `Login.USERNAME = '[data-testid="login-username"]'` no longer matches, this hard-fails.

The SOP-driven equivalent:

1. Perception returns `ScreenState.visible_elements = [{label: "User", testid: "user-input"}, …]`.
2. Planner sees the SOP step "type the username into the username field" + current visible elements + the cached locator from `config/locators/rdweb.py::Login.USERNAME`.
3. `SelectorResolver` (in [executors/selectors.py](executors/selectors.py)) tries strategies in order: `data-testid` → aria-label → name attribute → text → LLM-generated CSS → `flag_human`.
4. If every strategy fails, an `ActionResult(status="failed", error_msg="selector_unresolved")` triggers `RecoveryHandler` which classifies it as transient and retries once, then routes to HITL with the screenshot.

The same SOP works for the unchanged page (cache hit) and for the redesigned page (fallback strategies). No code change.

---

## 5. Generic action primitives — already defined

The legacy stages collapse onto a small set:

| Legacy intent | Generic action_type |
|---|---|
| `page.fill(USERNAME, …)` | `type` with `target="username"`, `value=working.rdweb_username` |
| `page.click(SUBMIT)` | `click` with `target="sign in button"` |
| `page.wait_for_selector(PRODUCTION_PAGE)` | `wait` with `target="production remoteapps grid"` |
| `page.goto(url)` | `navigate` with `value=url` |
| OCR pipeline call | `extract_pdf` (already routed to `FileExecutor.extract_pdf`) |
| Validator call | `read` with `target="validator_name"` — the executor calls the existing pure function and returns its dict |
| Borrower-name matching against IIM results | Two actions: `read` to scrape the rows + `extract` to ask the planner to pick the best match using SOP guidance |
| RemoteApp launch | `rdp_launch` |
| Excel batch lookup | `read_excel` |
| "Pause for human review" | `flag_human` |

There is **no** legacy stage in cases 1-4 that needs a new action_type. The migration verifies this case-by-case (see §8.4).

---

## 6. SOP memory architecture — already wired

Diagram from [docs/architecture-hybrid-runtime.md](docs/architecture-hybrid-runtime.md) §5, applied to this migration:

```
sop_chunks                ← markdown / pdf / docx of business workflows
ui_patterns               ← successful selector hits (written after task)
error_recoveries          ← which recovery directive worked for which error
task_templates            ← reusable goal/done-when shapes per case
```

| Concern | How it's handled today |
|---|---|
| Global SOP knowledge | `sop_chunks` collection (org-wide). |
| Workflow-specific memory | The task YAML (`fixtures`, `done_when`) — process-scope, not chroma. |
| Runtime working memory | `WorkingMemory` (in-process, checkpointed to SQLite per step). |
| Recovery memory | `error_recoveries` collection — populated by `KnowledgeStore.store_error_recovery()` when a recovery action succeeds. |
| Historical successful paths | `ui_patterns` collection — when a click succeeds via `data-testid`, write `(app, element_desc) → selector` for cache-first reuse next run. CLAUDE.md non-negotiable: only after task ends. |
| UI patterns/selectors | Two tiers: `config/locators/rdweb.py` (authored from POC) + `ui_patterns` (learned). |
| Per-agent isolation | `SessionMemory(agent_id)` — separate SQLite + audit. Knowledge collections are org-wide and read-only during task. |
| Shared organisational memory | All four collections above. Future per-client filtering: add `client_id` to metadata, pass `where={"client_id": …}` on query. |

Retrieval timing (already implemented in [agent/planner.py](agent/planner.py)):

- **SOP**: every plan, injected as system message. Best-effort — never blocks planning if Chroma is down.
- **UI patterns**: cache-first inside `SelectorResolver` before any LLM call (the CLAUDE.md "cache before LLM" non-negotiable).
- **Error recoveries**: queried only inside `RecoveryHandler` when the loop has actually failed — not on every step.

---

## 7. Multi-case generalisation — one runtime, four YAMLs

The legacy code keeps the four cases separated through four orchestrators. After migration there is **one** runtime (`run_agent.py`) and **four** task YAMLs:

```
config/tasks/
├── case1_already_closed.yaml
├── case2_letter_request.yaml
├── case3_hold_check.yaml
└── case4_claim_documents.yaml
```

Each YAML names:
- `goal` (the SOP-aware planner uses this to retrieve)
- `fixtures` (test data formerly hardcoded in `case3/schema.py`)
- `done_when` (replaces per-case result schemas)
- optional `steps` (escape hatch — deterministic mode is preserved for CI)

No new executor, no new schema, no new planner per case. Adding a fifth case is a new SOP file + a new YAML — no Python.

---

## 8. Recovery and robustness

| Scenario | Existing handling in `vision-rpa-agent` |
|---|---|
| Selector miss | `SelectorResolver` falls through 5 strategies; final fallback = HITL. |
| Blocking modal | `RecoveryHandler.detect()` → directive `retry` with a `close-modal` follow-up plan. |
| Workflow drift (agent on wrong screen) | Planner sees `ScreenState.state_summary` doesn't match SOP expectation; emits a `navigate` plan with `confidence < threshold` → HITL. |
| RDP disconnect | `RDPHandler.detect_disconnect()` → `rdp_reconnect` directive bounded by `MAX_RECONNECTS=3` → HITL. |
| Infinite loop | Three bounds: `settings.max_loop_steps`, `RETRY_LIMIT=3` per step in `_store`, recovery attempts capped per step in `_apply_directive`. |
| HITL resume | `HITLRunner` blocks on `wait_for_resolution`, applies resolution to `WorkingMemory`, calls `AgentLoop.resume()`. `MAX_RESUMES=10`. |

None of this is migration work — it's already there. The migration validates that each legacy case's recovery cases (e.g. `case3/stage9` returning False on a wait_for_selector timeout) is now covered by one of the directives above.

---

## 9. Migration strategy — phased, parity-preserving

Goal: do not break any of the four legacy demos while migration is in flight. Keep the old `run_caseN_e2e_demo.py` runnable until the SOP-driven version achieves parity.

### Phase A — Foundation (1 week)
- Copy `locators/rdweb.py` from `insurance-agent-project` into `vision-rpa-agent/config/locators/rdweb.py` **verbatim** (CLAUDE.md: "DO NOT rewrite"). Currently a starter set; full 120+ map lands here.
- Set up `docs/sop/` with file boundaries from §3.1.
- Ingest skeleton SOPs: `python -m memory.ingest_sop docs/sop/`.

### Phase B — Case 1 first (1 week, lowest risk)
Reason: Case 1 is **evaluation-only** ([case1/handler.py](insurance-agent-project/automation/cases/case1/handler.py) — no Playwright). It tests SOP-driven decision making without UI complexity.
- Write `docs/sop/10-already-closed-rule.md` with the rules from `case1/decision.py`.
- Write `config/tasks/case1_already_closed.yaml` using `task_type=case1` and `goal="Evaluate the claim header/body and the closure phrase…"`.
- Port `loan_db.py` lookup as a tool the planner can call via `read` action with `target="loan_db_lookup"`.
- Acceptance: run the same 10 fixture extractions through both pipelines; compare output. Pydantic equivalence (modulo timestamps) = parity.

### Phase C — Case 2 (1.5 weeks)
Loss-Drafts-only flow (no RDP, no Explorer). Browser-only.
- SOPs: `01-rdweb-login`, `02-loss-draft-claim-search`, `05-claim-letter-request`.
- Map `stage7_letter_request.py`, `stage8_communication_history.py`, `stage9_claim_linking.py`, `stage10_document_assignment.py` to SOP § Steps.
- Acceptance: side-by-side demo runs — same fixture, same final state in SQLite `extractions` table.

### Phase D — Case 4 (2 weeks)
Bigger document-processing flow, Excel reading, multiple service modules.
- Add `06-claim-document-management.md`, `07-question-document-processing.md`.
- Validators stay as Python; expose them as `read` targets.
- Acceptance as Phase C.

### Phase E — Case 3 (3 weeks, last)
Reason: Case 3 is the hardest — RD Web → File Explorer → PDF OCR → LD → IIM, 13 stages.
- Add `03-iim-loan-search.md`, `04-file-explorer-hold-check.md`.
- Stage 1-2 (RD Web + RemoteApps) reuse case-2 SOPs.
- Acceptance: full e2e demo + screenshot diff against the legacy run.

### Phase F — Decommission (1 week)
- Once all four cases pass parity, mark `run_caseN_e2e_demo.py` as legacy.
- Delete after one quarter of stable SOP-driven runs.
- `decision.py` / `validators/` modules remain — they are tools, not orchestrators.

Total: ~9 weeks for full migration with parity validation. Earlier phases are smaller and de-risk later ones.

---

## 10. Performance & production readiness

| Metric | Legacy | SOP-driven (measured / expected) |
|---|---|---|
| Per-step latency | ~0.5-2 s (mostly hardcoded waits) | ~1-3 s on OpenAI / 30-120 s on CPU Ollama. Demo mode brings parity. |
| Retrieval cost | n/a | ~30-50 ms per `query_sop` (local Chroma + MiniLM). Negligible vs LLM. |
| Reasoning overhead | n/a (Python decides) | One LLM call per loop step. Cached SOP chunks (Claude `cache_control: ephemeral`) reduce input tokens ~70%. |
| Caching opportunities | None | `ui_patterns` cache eliminates LLM calls for known selectors; `error_recoveries` short-circuits known failures. |
| Parallel agents | One process per case, hardcoded | Multi-agent already validated (`tests/test_multi_agent.py`) — three concurrent agents with isolated SessionMemory. |
| Failure recovery | per-stage try/except returns False | Typed `RecoveryDirective` + bounded retries + HITL escalation. |
| Production scalability ceiling | Bounded by code maintenance (4 cases × 13 stages × hardcoded waits) | Bounded by SQLite write contention ~10 concurrent agents (A-18). Beyond that, swap SessionMemory backend for Postgres LISTEN/NOTIFY. |

---

## 11. Risks and limitations (honest)

1. **SOP authoring quality is the new bottleneck.** Migration shifts complexity from Python code to prose. A vague SOP produces vague plans. Mitigation: §3.2's required-sections template; SOP review is part of merge.
2. **LLM-driven planning is non-deterministic.** Two runs may pick different but valid selectors. For audit-sensitive workflows (anything financial) the existing `is_financial → HITL` gate still fires; deterministic-mode YAMLs (Phase 2 capability) remain available for compliance-bound flows.
3. **Local CPU Ollama latency makes live demos painful.** Mitigation already shipped: `RUNTIME_MODE=demo` + OpenAI provider (3-5 s per step). Demo only; production runs Ollama / vLLM.
4. **Legacy validators are not re-tested.** They keep working because they're pure functions — the migration just changes who calls them. Add an integration test per validator that calls it via the new `read` action path.
5. **Locator map is a single point of brittleness.** Even with fallback strategies, a wholesale UI redesign costs time. Mitigation: `ui_patterns` learned cache; HITL reviews surface the broken selectors fast.
6. **SOP versioning.** Re-ingesting an updated SOP upserts existing chunks (content-hashed IDs) and orphans deleted chunks. Mitigation: `python -m memory.ingest_sop docs/sop/ --reset` after major rewrites; document SOP version in the file frontmatter.
7. **External-LLM demo mode bypasses CLAUDE.md non-negotiable.** Hard-gated by `runtime_mode=demo`; production deploys must not set this. The gate is enforced in code, not policy.

---

## 12. Recommended implementation phases (summary)

| Week | Phase | Outcome |
|---|---|---|
| 1 | A — Foundation | Locator map ported; SOP skeleton ingested; baseline tests pass. |
| 2 | B — Case 1 | Evaluation-only case runs SOP-driven; parity with legacy. |
| 3-4 | C — Case 2 | Browser-only LD flow runs SOP-driven; parity. |
| 5-6 | D — Case 4 | Document-management flow runs SOP-driven; parity. |
| 7-9 | E — Case 3 | Full RDP + Explorer + OCR + LD + IIM flow runs SOP-driven; parity. |
| 10 | F — Decommission | Legacy orchestrators marked deprecated; validators retained as tools. |

---

## 13. Testing strategy

1. **Parity harness** per case: run legacy `run_caseN_e2e_demo.py` and the new `run_agent.py --task config/tasks/caseN.yaml` against the same simulator + fixtures; diff the final SQLite `extractions` rows and screenshot pairs. Fail the PR on divergence.
2. **SOP unit tests**: ingest each SOP file, query it with the goals from the task YAMLs, assert relevant chunks rank top-2. Fast (<1 s per query); catches authoring regressions.
3. **Recovery injection** ([tests/test_error_injection.py](tests/test_error_injection.py) already covers this): each new case-specific failure mode (e.g. "IIM returns zero matches") adds a test that asserts the SOP routes to HITL rather than failing silently.
4. **Performance budget**: end-to-end case 3 must complete in ≤ 10 minutes on demo mode (OpenAI) and ≤ 30 minutes on Ollama. Tracked in `tests/test_performance.py`.
5. **Multi-agent regression**: existing `tests/test_multi_agent.py` already exercises three concurrent agents; extend it with a "one case 1 + one case 3" mix to confirm DB isolation under mixed-workflow load.

---

## 14. Deliverables (this document closes them)

| Deliverable | Location |
|---|---|
| Target architecture | §2, §4 — diagrams and component mapping |
| Migration approach | §9 — phased plan, parity checkpoints |
| Runtime flow | §4 — observe→reason→act→store with SOP injection |
| SOP ingestion pipeline | [memory/ingest_sop.py](memory/ingest_sop.py) + §3 |
| Memory architecture | §6 — four chroma collections, retrieval timing |
| Planner/reasoner design | [agent/planner.py](agent/planner.py) + §3.2 (SOP shape) |
| Execution lifecycle | §4 + [agent/loop.py](agent/loop.py) |
| Risks and limitations | §11 |
| Implementation phases | §12 |
| Testing strategy | §13 |
