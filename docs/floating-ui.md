# Floating Runtime UI

Status: implemented 2026-05-18.

A small always-on-top window that an operator watches while the agent
runs. Shows live activity, current task state, and inline HITL prompts
without juggling terminal + browser + dashboard.

---

## 1. Architecture

```
                                ┌──────────────────────────┐
                                │ Agent process            │
                                │  AgentLoop ─ writes ─┐   │
                                │                      │   │
                                │  Audit NDJSON  ◄────┘   │
                                │  data/db/<agent>.db  ◄──┘
                                └──────────────────────────┘
                                          │  (file-based event bus)
                                          ▼
                ┌─────────────────────────────────────────────────┐
                │ hitl.server (FastAPI, port 8080)                │
                │                                                 │
                │  GET /runtime          → self-contained HTML    │
                │  GET /stream/events    → SSE (audit + hitl_state) │
                │  POST /api/.../resolve → write HITL resolution  │
                │  GET /api/agents       → multi-agent state      │
                └─────────────────────────────────────────────────┘
                                          │  (HTTP + SSE)
                                          ▼
                ┌─────────────────────────────────────────────────┐
                │ hitl.floating_window                            │
                │  pywebview native window  ─or─  default browser │
                │  Loads /runtime; consumes /stream/events        │
                └─────────────────────────────────────────────────┘
```

### Why this shape

- **File-based event bus.** The agent already writes structured NDJSON to
  `logs/audit/<agent_id>.ndjson`. Adding a broker would be reinventing
  the wheel. The SSE endpoint tails these files and pushes lines as
  `event: audit` to the UI.
- **HITL state on a slower cadence.** SQLite is polled every 2 s for
  pending reviews and pushed as `event: hitl_state`. That's the
  *transition* signal — the audit log carries the *progress* signal.
- **Decoupled from the loop.** The agent never calls into the UI. The
  UI never calls into the agent. They communicate via the same files
  the framework already maintains. If the UI crashes, the agent runs
  on; if the agent dies, the UI keeps showing the last state.
- **Multi-agent for free.** The dashboard already discovers
  `data/db/*.db`. The SSE endpoint already tails every `*.ndjson` in
  `logs/audit/`. Adding a second agent process is one terminal —
  zero UI changes.

### Concurrency model

- **Agent**: single-threaded loop (unchanged).
- **FastAPI**: uvicorn worker; async generator for SSE; no GIL contention
  with the agent because they run in separate processes.
- **Floating window**: pywebview owns its own event loop in its own
  process. The browser fallback is just a `webbrowser.open()` call —
  the OS handles it from there.

### Thread-safety

The only shared resource is SQLite. Each `SessionMemory` opens its own
connection with `check_same_thread=False` and `journal_mode=WAL`, which
allows the FastAPI server (different process) to read while the agent
writes. Confirmed by `tests/test_multi_agent.py`.

---

## 2. Running it

```bash
# Default: launches FastAPI + the floating window automatically.
poetry run python run_agent.py --task config/tasks/<task>.yaml

# Without UI (CI, server, headless box):
poetry run python run_agent.py --task ... --no-ui

# Manual launch if you want the UI standalone:
poetry run python -m hitl.server &              # terminal 1 — dashboard
poetry run python -m hitl.floating_window       # terminal 2 — window
```

The launcher first tries pywebview. If pywebview isn't installed (the
default), it falls back to opening the dashboard URL in the system's
default browser.

To get the native window:

```bash
poetry run pip install pywebview
# (macOS) brew install --cask webview2  # not needed; uses WebKit
# (Linux) sudo apt install python3-gi gir1.2-webkit2-4.0
```

On macOS, pywebview uses WebKit by default — no extra install.

---

## 3. UI surface

| Section | Source | Update mechanism |
|---|---|---|
| Agents bar | `/api/agents` on load + every `hitl_state` ping | 2 s |
| Live activity (log) | `/stream/events` `event: audit` | Push (~real time) |
| HITL panel (inline) | `/stream/events` `event: hitl_state` with non-empty list | 2 s; transitions hidden when list empties |
| Resolve form | POST `/api/agent/<id>/resolve/<hitl_id>` | Operator action |

The HITL panel shows only the *first* pending review — if multiple are
queued (rare; happens only with multiple agents flagging at once), the
operator clears them one at a time. Each resolution submits as JSON
without a page reload; the next ping clears the panel.

### Visual cues

- Warnings / errors / HITL events are coloured red/amber.
- Plan and perception events are coloured (blue / purple) so the
  observe→reason rhythm is obvious at a glance.
- The agents bar turns amber for any agent with `pending > 0`.

---

## 4. Integration points with AgentLoop / HITL

Nothing changed in `agent/loop.py` or `hitl/queue.py`. The UI is purely
a *consumer* of artefacts the framework already produces:

| Existing artefact | UI consumer |
|---|---|
| `agent.audit.AuditLog.append(...)` | `/stream/events` tail |
| `SessionMemory.write_hitl(...)` | `/stream/events` `hitl_state` ping |
| `SessionMemory.resolve_hitl(...)` | Triggered by POST from the HITL panel |
| `data/db/<agent>.db` per agent | `_discover_agent_dbs()` (already there) |

This is deliberate — the UI is purely additive. Pulling it out is one
PR (revert the floating-window files; no agent code touched).

---

## 5. Tests

- [tests/test_runtime_ui.py](tests/test_runtime_ui.py) — 4 tests:
  HTML self-contained, SSE route registered, audit tailer picks up new
  lines, `/api/agents` not regressed.
- [tests/test_hitl_server.py](tests/test_hitl_server.py) — 6 tests
  unchanged; verifies the dashboard / resolve form / API still work.

End-to-end SSE assertion (TestClient + `iter_raw`) is flaky because
streaming flush timing isn't deterministic in-process. The async
generator is tested directly instead — that's the real critical
path.

---

## 6. Known limitations

1. **No screenshots in the HITL panel yet.** The HITL row's
   `screenshot` field is captured but the runtime view doesn't render
   it (the `/agent/{id}/review/{id}` page in the dashboard does). Easy
   addition once screenshots are reliably written.
2. **Polling SQLite every 2 s for HITL transitions.** Acceptable up to
   ~10 agents (see A-18). A future revision can replace this with
   `LISTEN/NOTIFY` if SQLite is swapped for Postgres.
3. **Audit log file rotation isn't handled.** NDJSON files grow
   unbounded. For production deployments, configure logrotate on
   `logs/audit/*.ndjson` and restart the dashboard daily.
4. **Single floating window per host.** Two `run_agent.py` invocations
   both try to claim port 8080; the second detects the first and skips
   spawning the dashboard, which is correct. But two `--no-ui` flags
   are the only way to run two parallel agents truly headless.
5. **No auth.** A-19 (already in [docs/architecture-hybrid-runtime.md](architecture-hybrid-runtime.md))
   stands — front with an auth proxy before exposing beyond 127.0.0.1.
6. **pywebview WebKit cookies are ephemeral on macOS.** Not an issue
   for us because the UI is stateless and reads from the FastAPI
   server, but worth knowing if you ever need persistent client state.

---

## 7. Migration path to "real" web app later

If/when this needs to be a hosted multi-tenant tool, swap the file-bus
for a real message broker:

- Phase 1 (now): file-based bus, single host.
- Phase 2: same SSE / same UI, but back with Redis pub/sub or NATS.
  Agent publishes audit events; UI subscribes. SQLite stays for durable
  HITL state.
- Phase 3: switch the floating window's pywebview shell for a real
  WebSocket connection in a hosted SPA. The `/runtime` HTML already
  uses EventSource — porting to WebSocket is a 10-line change.

Each phase is forward-compatible; no UI rewrite required.
