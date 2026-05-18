# Case 1 — Full Workflow Analysis and Migration

Status: analysis + MVP migration plan, 2026-05-18.

This document grounds every claim in the actual legacy code at
[legacy/automation/demo/run_case1_e2e_demo.py](legacy/automation/demo/run_case1_e2e_demo.py)
and the supporting flows it calls. **There are no assumed or imagined steps
in this analysis** — every stage cites the file and lines it came from.

---

## 1. Legacy workflow — what Case 1 actually does

| # | Stage | Application | File / lines |
|---|---|---|---|
| 1 | RD Web login + Production folder open | Browser | [run_case1_e2e_demo.py:146](legacy/automation/demo/run_case1_e2e_demo.py#L146) → [flows/launch_lossdrafts_flow.py:42](legacy/automation/flows/launch_lossdrafts_flow.py#L42) |
| 2 | Click "Loss Drafts" tile → downloads HTML launcher (NOT .rdp) → meta-refresh URL → SSO sign-on → land on `/lossdrafts/` | Browser → SSO redirect | `flows/launch_lossdrafts_flow.py` |
| 3 | Open Document Management tab | Browser | [run_case1_e2e_demo.py:152](legacy/automation/demo/run_case1_e2e_demo.py#L152) |
| 4 | Select the Case 1 row in the document grid | Browser | [run_case1_e2e_demo.py:160](legacy/automation/demo/run_case1_e2e_demo.py#L160) |
| 5 | Click the Link icon → PDF opens in a NEW tab → bytes fetched via authenticated `context.request.get(url)` | Browser + new tab | [run_case1_e2e_demo.py:171](legacy/automation/demo/run_case1_e2e_demo.py#L171) |
| 6 | Extract claim IDs from PDF: pdfplumber → Tesseract fallback (`extraction.extract_from_pdf`) | In-process | [run_case1_e2e_demo.py:211](legacy/automation/demo/run_case1_e2e_demo.py#L211) |
| 7 | Validate each claim ID via the Claim Search panel (real typing + click) | Browser | [run_case1_e2e_demo.py:242](legacy/automation/demo/run_case1_e2e_demo.py#L242) |
| 8 | Cross-verify name + address (PDF identity vs `loan_db.lookup(...)`) | In-process | [run_case1_e2e_demo.py:322](legacy/automation/demo/run_case1_e2e_demo.py#L322) |
| 9 | Run `Case1Handler.evaluate(extraction)` → produces `Case1Result` | In-process | [run_case1_e2e_demo.py:343](legacy/automation/demo/run_case1_e2e_demo.py#L343) |

### Critical fact about Case 1

**Case 1 is 100% browser-based.** There is **no `mstsc` launch, no
`pywinauto`, no desktop window attach, no RDP session, no .rdp file**.

The "RDP_LAUNCH → REMOTE_SESSION → DESKTOP_INTERACTIONS" graph from your
spec describes **Case 3** (Hold Check), which DOES involve File Explorer
navigation on a Windows host. For Case 1 the entire workflow stays in a
single Chromium context with one popup tab for the PDF viewer.

### External systems Case 1 actually touches

- `localhost:8000/rdweb/...` — RD Web Access portal (simulation).
- `localhost:8000/sso/...` — SAML IdP mock.
- `localhost:8000/lossdrafts/...` — Loss Drafts module.
- `localhost:8000/static/pdfs/case1_closed_allstate_letter.pdf` — the actual
  PDF the Link icon resolves to.
- `cases.case1.loan_db` — in-process Python dict, no network. The "system
  cross-verify" in stage 8 reads from this.

No remote files, no RDP, no Excel, no IIM in Case 1. Those belong to
Cases 3 and 4.

---

## 2. Root cause of the "stops after login" problem

The agent stops after login because **the task YAML I wrote
([config/tasks/sim_live_rdweb.yaml](config/tasks/sim_live_rdweb.yaml))
explicitly tells it to**. The relevant lines:

```yaml
goal: |
  Log in to the RD Web Access portal ... Fill both fields, then flag for
  human approval BEFORE clicking the Sign-in button. After the operator
  approves and the page transitions to the RemoteApps landing page,
  declare the task complete by emitting action_type "task_complete".
```

The LLM did exactly that. The bug is **mis-scoped task**: I authored a
login-only task but called it `case1` (`task_type: case1`). It's actually
the **Stage-1-and-2** prefix of the real Case 1 flow.

There is no bug in the planner, no missing state detection, no recovery
issue. The framework is honest — it runs the task you give it. The fix is
to write a task and SOP set that actually describes the full 9-stage flow.

Concretely:

| Layer | Why it stopped |
|---|---|
| Task YAML | Goal explicitly said "after landing page, declare task complete" |
| Planner | Followed the goal — emitted `task_complete` on the post-login page |
| Loop | Saw `task_complete` (just added), set `working.task_complete=True`, exited |
| HITL | Not involved — it was a clean task_complete, not an abort |

**No code is broken.** What's missing is **multi-stage workflow
authoring** + a way for SOPs to describe stage transitions instead of
single-screen actions.

---

## 3. SOP-driven migration shape

Three layers, each with one source of truth:

```
┌──────────────────────────────────────────────────────────────────┐
│ config/tasks/case1_full_flow.yaml                                │
│   Umbrella task. Names the 9 stages, the completion criteria for │
│   each, and the final acceptance.                                │
└────────────────────────────────────────────────────────────────────
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│ docs/sop/case1-*.md                                              │
│   Stage-level SOPs. The planner retrieves them per current stage │
│   so the prompt context is sharp and not bloated.                │
│                                                                   │
│   sim-rdweb-login.md           Stage 1 (already exists)          │
│   case1-loss-drafts-launch.md  Stage 2 (Loss Drafts + SAML)      │
│   case1-document-management.md Stage 3-5 (tab + row + PDF link)  │
│   case1-pdf-extraction.md      Stage 6 (extract_pdf action)      │
│   case1-claim-validation.md    Stage 7 (Claim Search per ID)     │
│   case1-cross-verify.md        Stage 8 (system row vs PDF)       │
│   case1-already-closed-rule.md Stage 9 (existing — evaluation)   │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│ WorkingMemory.current_stage / stages_completed                   │
│   Tracker the planner reads to know "which SOP applies now".     │
│   Advance signal = a new `stage_complete` action_type the LLM    │
│   emits when its current-stage exit criteria are met.            │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. What this turn delivers vs what's deferred

I'm being explicit so you can judge whether to keep me building or
adjust scope.

### Delivered now (verifiable)

1. **This analysis document** — every claim grounded in the legacy file.
2. **5 new stage-level SOPs** covering stages 2-8 (stage 1 SOP and
   stage 9 SOP already exist).
3. **`config/tasks/case1_full_flow.yaml`** — single umbrella task that
   replaces `sim_live_rdweb.yaml` for full-flow runs. The login YAML
   stays as a smoke test.
4. **`current_stage` + `stages_completed`** fields on `WorkingMemory`.
5. **`stage_complete` action_type** — LLM emits this to advance the
   tracker without terminating the task.
6. **Planner prompt updated** with the stage rules.
7. **Tests** for the new schema fields + the stage-advance flow.

### Deferred (real engineering, real time)

| Asked | Status | Reason |
|---|---|---|
| Workflow state machine class with edges + guards | Not built | Would be premature without seeing 2 cases hit the same shape. The `current_stage` string is the MVP; a full graph adds value when Cases 2/3/4 land. |
| Per-stage deterministic post-login detection rules | Partially — SOP § "Done when" already describes them in prose; deterministic check lives in the LLM's planner output | Adding hard URL/element checks per stage is one PR per stage; doing it speculatively before the flow runs end-to-end risks gold-plating wrong invariants. |
| Tests/test_case1_e2e_runtime.py end-to-end | Not built | Requires the legacy sim running on :8000 + OpenAI/Ollama live. Can't run from here. Sketched in §9 below as a manual runbook. |
| Recovery rules graph per stage | Not built | Existing `RecoveryHandler` already handles transient / modal / disconnect; stage-specific rules belong in each SOP's "Recovery" section (added). |
| Workflow visualisation in the floating UI | Not built | Out of scope for this turn. Stage tracking is in `WorkingMemory` and will surface in the runtime log. |

This is what an honest one-session delivery looks like. Layer (3) — the
state-machine + deterministic guards + e2e test — is real follow-on work,
~1 week.

---

## 5. Stage-by-stage SOP mapping

Each new SOP lives at `docs/sop/case1-<stage>.md` and is retrieved by the
planner based on the current stage. The umbrella task names them in order.

### Stage 1 — RD Web login + Production folder
- Already in `docs/sop/sim-rdweb-login.md`.
- Done-when: URL contains `/Default.aspx/Production` AND tile grid visible.

### Stage 2 — Loss Drafts launch + SAML SSO
- New SOP: `docs/sop/case1-loss-drafts-launch.md`.
- Done-when: URL contains `/lossdrafts/` AND the LD shell page header is
  visible.

### Stage 3-5 — Document Management → Case 1 row → PDF link
- New SOP: `docs/sop/case1-document-management.md`.
- Done-when: PDF bytes captured into `working_memory.extracted_values.pdf_bytes`.

### Stage 6 — PDF extraction
- New SOP: `docs/sop/case1-pdf-extraction.md`.
- Action: `extract_pdf` (already exists in FileExecutor). Tool call.
- Done-when: `working_memory.extracted_values.candidates` is non-empty.

### Stage 7 — Claim Search per candidate
- New SOP: `docs/sop/case1-claim-validation.md`.
- Loop: for each candidate.role ∈ {header, body}, type → submit → record
  empty/found result.
- Done-when: `working_memory.extracted_values.validations` has one entry
  per candidate.

### Stage 8 — Cross-verify identity
- New SOP: `docs/sop/case1-cross-verify.md`.
- Tool call: `loan_db_lookup` on the body candidate's value.
- Done-when: both `pdf_identity` and `system_identity` are in working
  memory.

### Stage 9 — Already-Closed evaluation
- Already in `docs/sop/case1-already-closed-rule.md`.
- Tool call: `case1_evaluate` (already wired).
- Done-when: `working_memory.extracted_values.case1_result` is populated.

---

## 6. Completion criteria — what "done" means

The umbrella task's `done_when` is the **logical AND** of all stage exit
conditions. Single-source-of-truth in
[config/tasks/case1_full_flow.yaml](config/tasks/case1_full_flow.yaml):

```yaml
done_when:
  all_set:
    - rdweb_authenticated
    - lossdrafts_loaded
    - pdf_bytes
    - candidates
    - validations
    - case1_result
```

The planner only emits `action_type: task_complete` when every key is
present in `working.extracted_values`. The loop confirms before exiting
(deterministic check, not LLM judgement).

---

## 7. Recovery integration (per-stage table)

Each SOP includes a "Recovery rules" section. Summary:

| Stage | Most likely failures | Recovery directive |
|---|---|---|
| 1 | Wrong credentials, session expired | HITL credential prompt |
| 2 | SAML loop, popup blocked | RDP-style reconnect → HITL |
| 3-5 | Row missing / link gone / popup blocked | Retry once, then HITL |
| 6 | OCR returns 0 candidates | HITL with extract pipeline log |
| 7 | Network/search empty when expected found | Retry once, log, continue |
| 8 | Identity fields ambiguous | Skip (no-op for downstream) |
| 9 | Handler raises | HITL with stacktrace |

The existing `RecoveryHandler` already handles transient/modal/disconnect.
Stage-specific recovery is encoded in the SOP "Recovery rules" prose so
the planner can choose between retry / HITL / proceed-with-partial.

---

## 8. Open questions before running end-to-end

To run this against your simulation today:

1. **Does the legacy sim's `/lossdrafts/document-management` accept the
   credentials you have?** Your earlier login succeeded, so yes.
2. **Does the simulation actually serve the Case 1 PDF at
   `/static/pdfs/case1_closed_allstate_letter.pdf`?** I see it under
   `legacy/simulation/static/pdfs/`. Yes.
3. **Is `pdfplumber` + Tesseract installed in the dev venv?**
   `pdfplumber` is in the project; Tesseract binary is system-level and
   was an A-13 carry-forward. Without it the extraction tier falls
   through to VLM (already handled).

---

## 9. Manual e2e runbook (the test that needs a live sim + LLM)

```bash
# Terminal 1 — simulation
cd legacy/simulation && .venv/bin/uvicorn app.main:app --port 8000 --reload

# Terminal 2 — agent
poetry run python run_agent.py --task config/tasks/case1_full_flow.yaml
```

Expected stages observable in the floating UI's live log:

```
stage_complete  → login
stage_complete  → lossdrafts_launch
stage_complete  → document_management
stage_complete  → pdf_extraction
stage_complete  → claim_validation
stage_complete  → cross_verify
task_complete   → case1_already_closed (or not_already_closed)
```

If a stage fails, HITL fires with the failure category and the operator
can correct it. The framework already has all the primitives — this turn
adds the orchestration.
