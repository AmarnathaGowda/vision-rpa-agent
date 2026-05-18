"""FastAPI HITL review dashboard — multi-agent view.

The dashboard discovers every ``data/db/<agent_id>.db`` under
``settings.db_dir`` and aggregates their ``hitl_queue`` rows. A human reviewer
can:

  * see all pending reviews across agents on one page
  * inspect a single review (screenshot + plan + screen context)
  * submit a resolution (approve / correct / skip / abort)

Resolution is written back to the originating agent's SQLite DB via
``SessionMemory.resolve_hitl``; the AgentLoop's supervisor polls that row and
resumes the paused task.

No external network calls — runs locally, bound to 127.0.0.1 by default.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from config.logging_config import get_logger
from config.settings import settings
from memory.session import SessionMemory

log = get_logger(__name__)

app = FastAPI(title="Vision RPA Agent — HITL Dashboard")


# ── DB discovery ─────────────────────────────────────────────────────────
def _discover_agent_dbs(db_dir: str | None = None) -> dict[str, Path]:
    """Return ``{agent_id: Path}`` for every SQLite file under ``db_dir``."""
    root = Path(db_dir or settings.db_dir)
    if not root.exists():
        return {}
    return {p.stem: p for p in sorted(root.glob("*.db"))}


_session_cache: dict[str, SessionMemory] = {}


def _session_for(agent_id: str) -> SessionMemory:
    """Re-use a SessionMemory per agent — sqlite3 connections are cheap but
    re-opening per request would still churn WAL state."""
    if agent_id in _session_cache:
        return _session_cache[agent_id]
    dbs = _discover_agent_dbs()
    if agent_id not in dbs:
        raise HTTPException(status_code=404, detail=f"unknown agent_id: {agent_id}")
    store = SessionMemory(agent_id=agent_id)
    _session_cache[agent_id] = store
    return store


def _clear_session_cache() -> None:
    """Test hook — drops cached connections so a fresh DB scan happens."""
    for s in _session_cache.values():
        try:
            s.conn.close()
        except Exception:
            pass
    _session_cache.clear()


# ── HTML rendering (inline, no template files) ───────────────────────────
def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
 body {{ font-family: -apple-system, sans-serif; margin: 2rem; color:#222; }}
 table {{ border-collapse: collapse; width: 100%; }}
 th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; vertical-align: top; }}
 th {{ background: #f4f4f4; }}
 .pending {{ background: #fff8e1; }}
 .resolved {{ background: #e8f5e9; }}
 pre {{ background: #f7f7f7; padding: 10px; border-radius: 4px; overflow:auto; }}
 a.btn {{ display:inline-block; padding:4px 8px; border:1px solid #888; border-radius:3px; text-decoration:none; color:#222; margin-right:6px; }}
 form.inline {{ display:inline; }}
</style></head><body>
<h1>{title}</h1>
{body}
<p><a href="/">← all agents</a></p>
</body></html>"""


# ── routes ───────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    """Multi-agent index: list every agent and their pending review count."""
    dbs = _discover_agent_dbs()
    if not dbs:
        return _page("HITL Dashboard", "<p>No agent databases found.</p>")
    rows = []
    total_pending = 0
    for agent_id in dbs:
        store = _session_for(agent_id)
        pending = store.list_hitl(status="pending")
        total_pending += len(pending)
        rows.append(
            f"<tr class='{'pending' if pending else ''}'>"
            f"<td><a href='/agent/{agent_id}'>{agent_id}</a></td>"
            f"<td>{len(pending)}</td>"
            f"<td>{len(store.list_hitl())}</td>"
            f"</tr>"
        )
    body = (
        f"<p><b>{total_pending}</b> review(s) pending across {len(dbs)} agent(s).</p>"
        "<table><tr><th>agent_id</th><th>pending</th><th>total</th></tr>"
        + "".join(rows) + "</table>"
    )
    return _page("HITL Dashboard", body)


@app.get("/agent/{agent_id}", response_class=HTMLResponse)
async def agent_view(agent_id: str) -> str:
    store = _session_for(agent_id)
    items = store.list_hitl()
    if not items:
        return _page(f"Agent {agent_id}", "<p>No HITL rows.</p>")
    rows = []
    for it in items:
        css = "pending" if it["status"] == "pending" else "resolved"
        rows.append(
            f"<tr class='{css}'>"
            f"<td>{it['id']}</td>"
            f"<td>{it['task_id']}</td>"
            f"<td>{it['status']}</td>"
            f"<td>{it['reason']}</td>"
            f"<td>{it['created_at']}</td>"
            f"<td><a class='btn' href='/agent/{agent_id}/review/{it['id']}'>open</a></td>"
            f"</tr>"
        )
    body = (
        "<table><tr><th>id</th><th>task</th><th>status</th><th>reason</th>"
        "<th>created</th><th></th></tr>" + "".join(rows) + "</table>"
    )
    return _page(f"Agent {agent_id}", body)


@app.get("/agent/{agent_id}/review/{hitl_id}", response_class=HTMLResponse)
async def review(agent_id: str, hitl_id: int) -> str:
    store = _session_for(agent_id)
    row = store.get_hitl(hitl_id)
    if not row:
        raise HTTPException(status_code=404, detail="hitl row not found")
    context = json.loads(row["context_json"] or "{}")
    shot = row.get("screenshot") or ""
    shot_html = (
        f"<p><img src='{shot}' style='max-width:600px;border:1px solid #ccc'></p>"
        if shot else "<p><i>no screenshot</i></p>"
    )
    disabled = "" if row["status"] == "pending" else "disabled"
    body = f"""
<p><b>task:</b> {row['task_id']} &nbsp; <b>agent:</b> {agent_id} &nbsp;
   <b>status:</b> {row['status']}</p>
<p><b>reason:</b> {row['reason']}</p>
{shot_html}
<h3>Plan context</h3>
<pre>{json.dumps(context, indent=2)}</pre>
<h3>Resolve</h3>
<form method='post' action='/agent/{agent_id}/resolve/{hitl_id}'>
  <label>Action:
    <select name='action' {disabled}>
      <option value='approve'>approve (retry step)</option>
      <option value='correct'>correct (override values, advance)</option>
      <option value='skip'>skip (advance step)</option>
      <option value='abort'>abort task</option>
    </select>
  </label>
  <br><br>
  <label>Corrected extracted_values (JSON, optional):<br>
    <textarea name='extracted_values' rows='4' cols='60' {disabled}></textarea>
  </label>
  <br><br>
  <label>Resolver: <input name='resolver' value='human' {disabled}></label>
  <label>Note: <input name='note' size='40' {disabled}></label>
  <br><br>
  <button type='submit' {disabled}>Submit resolution</button>
</form>
"""
    return _page(f"Review #{hitl_id}", body)


@app.post("/agent/{agent_id}/resolve/{hitl_id}")
async def resolve(agent_id: str, hitl_id: int, request: Request):
    store = _session_for(agent_id)
    row = store.get_hitl(hitl_id)
    if not row:
        raise HTTPException(status_code=404, detail="hitl row not found")
    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"already {row['status']}")

    form: Any = await request.form()
    action = (form.get("action") or "").strip()
    if action not in (
        "approve", "correct", "skip", "abort", "retry_with_values",
        "retry_with_hint", "correct_target", "teach_selector", "save_as_sop",
    ):
        raise HTTPException(status_code=400, detail=f"invalid action: {action!r}")

    raw_overrides = (form.get("extracted_values") or "").strip()
    overrides: dict = {}
    if raw_overrides:
        try:
            overrides = json.loads(raw_overrides)
            if not isinstance(overrides, dict):
                raise ValueError("must be a JSON object")
        except (ValueError, json.JSONDecodeError) as e:
            raise HTTPException(status_code=400,
                                detail=f"extracted_values must be a JSON object: {e}")

    resolution = {
        "action": action,
        "extracted_values": overrides,
        "note": form.get("note", ""),
        "resolver": form.get("resolver", "human"),
    }
    store.resolve_hitl(hitl_id, resolution)
    log.info("hitl_dashboard_resolved",
             agent_id=agent_id, hitl_id=hitl_id, action=action)
    return RedirectResponse(url=f"/agent/{agent_id}", status_code=303)


# ── JSON API (for tests + headless integrations) ─────────────────────────
@app.get("/api/agents")
async def api_agents() -> JSONResponse:
    dbs = _discover_agent_dbs()
    out = []
    for agent_id in dbs:
        store = _session_for(agent_id)
        out.append({
            "agent_id": agent_id,
            "pending": len(store.list_hitl(status="pending")),
            "total": len(store.list_hitl()),
        })
    return JSONResponse(out)


@app.get("/api/agent/{agent_id}/hitl")
async def api_agent_hitl(agent_id: str) -> JSONResponse:
    store = _session_for(agent_id)
    return JSONResponse(store.list_hitl())


@app.get("/api/agent/{agent_id}/hitl/{hitl_id}")
async def api_agent_hitl_detail(agent_id: str, hitl_id: int) -> JSONResponse:
    """Full review payload — used by the floating UI to render the panel."""
    store = _session_for(agent_id)
    row = store.get_hitl(hitl_id)
    if not row:
        raise HTTPException(status_code=404, detail="hitl row not found")
    out = dict(row)
    out["context"] = json.loads(row["context_json"] or "{}")
    out.pop("context_json", None)
    return JSONResponse(out)


@app.get("/api/agent/{agent_id}/screenshot/{hitl_id}")
async def api_agent_hitl_screenshot(agent_id: str, hitl_id: int):
    """Serve the PNG captured at HITL escalation. 404 if not present."""
    from fastapi.responses import FileResponse
    store = _session_for(agent_id)
    row = store.get_hitl(hitl_id)
    if not row or not row.get("screenshot"):
        raise HTTPException(status_code=404, detail="no screenshot")
    path = Path(row["screenshot"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="screenshot file missing")
    return FileResponse(str(path), media_type="image/png")


@app.post("/api/agent/{agent_id}/resolve/{hitl_id}")
async def api_resolve(agent_id: str, hitl_id: int, resolution: dict) -> JSONResponse:
    store = _session_for(agent_id)
    row = store.get_hitl(hitl_id)
    if not row:
        raise HTTPException(status_code=404, detail="hitl row not found")
    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"already {row['status']}")
    if (resolution.get("action") or "") not in (
        "approve", "correct", "skip", "abort", "retry_with_values",
        "retry_with_hint", "correct_target", "teach_selector", "save_as_sop",
    ):
        raise HTTPException(status_code=400, detail="invalid action")
    store.resolve_hitl(hitl_id, resolution)
    return JSONResponse({"ok": True, "hitl_id": hitl_id})


# ── SSE event stream ────────────────────────────────────────────────────
# Tails every agent's NDJSON audit log and streams new lines as SSE.
# Plus periodic pending-HITL pings so the floating UI knows when a new
# review lands. One stream multiplexes everything — clients filter
# client-side by agent_id.

async def _tail_audit_files(
    last_offsets: dict[str, int],
    poll_interval: float = 0.5,
) -> AsyncIterator[dict]:
    """Yield {event: 'audit', data: <line>} for each new NDJSON line.

    Keeps a per-file offset so reconnecting clients only see new lines.
    """
    log_dir = Path(settings.audit_log_dir)
    while True:
        if log_dir.exists():
            for path in sorted(log_dir.glob("*.ndjson")):
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                offset = last_offsets.get(str(path), size)  # first-seen → tail-only
                if size > offset:
                    try:
                        with path.open("r", encoding="utf-8") as f:
                            f.seek(offset)
                            for raw_line in f:
                                line = raw_line.strip()
                                if not line:
                                    continue
                                try:
                                    record = json.loads(line)
                                except json.JSONDecodeError:
                                    continue
                                yield {"event": "audit", "data": record}
                            last_offsets[str(path)] = f.tell()
                    except OSError:
                        continue
                else:
                    last_offsets[str(path)] = size
        await asyncio.sleep(poll_interval)


async def _hitl_pings(poll_interval: float = 2.0) -> AsyncIterator[dict]:
    """Yield {event: 'hitl_state', data: <pending list>} on a slower cadence."""
    while True:
        try:
            dbs = _discover_agent_dbs()
            pending = []
            for agent_id in dbs:
                store = _session_for(agent_id)
                for row in store.list_hitl(status="pending"):
                    pending.append({
                        "agent_id": agent_id,
                        "hitl_id": row["id"],
                        "task_id": row["task_id"],
                        "reason": row["reason"],
                        "created_at": str(row["created_at"]),
                    })
            yield {"event": "hitl_state", "data": pending}
        except Exception as e:  # noqa: BLE001 — never crash the stream
            log.warning("hitl_pings_error", error=str(e))
        await asyncio.sleep(poll_interval)


async def _merge_streams(*streams: AsyncIterator[dict]) -> AsyncIterator[dict]:
    """Merge multiple async iterators into one SSE-formatted stream."""
    queue: asyncio.Queue = asyncio.Queue()

    async def _pump(it: AsyncIterator[dict]) -> None:
        try:
            async for item in it:
                await queue.put(item)
        except Exception as e:  # noqa: BLE001
            await queue.put({"event": "error", "data": str(e)})

    tasks = [asyncio.create_task(_pump(s)) for s in streams]
    try:
        while True:
            item = await queue.get()
            yield item
    finally:
        for t in tasks:
            t.cancel()


@app.get("/stream/events")
async def stream_events(request: Request) -> StreamingResponse:
    """Server-Sent Events: live audit lines + HITL state pings."""
    last_offsets: dict[str, int] = {}

    async def event_source() -> AsyncIterator[bytes]:
        # Initial hello so the client knows the channel is alive.
        yield b": connected\n\n"
        merged = _merge_streams(
            _tail_audit_files(last_offsets),
            _hitl_pings(),
        )
        try:
            async for item in merged:
                if await request.is_disconnected():
                    break
                payload = json.dumps(item["data"], default=str)
                event = item["event"]
                yield f"event: {event}\ndata: {payload}\n\n".encode("utf-8")
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Floating runtime view ───────────────────────────────────────────────
@app.get("/runtime", response_class=HTMLResponse)
async def runtime_view() -> str:
    """Single-page floating runtime UI: live log + multi-agent state +
    inline HITL form. Designed to be loaded in a small native window
    (pywebview) or a browser tab.

    JS-only — no template engine; the HTML is self-contained.
    """
    return _RUNTIME_HTML


_RUNTIME_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Agent Runtime</title>
<style>
 * { box-sizing: border-box; }
 body { font-family: -apple-system, sans-serif; margin: 0; background: #1e1e1e;
        color: #ddd; font-size: 13px; }
 header { background: #2d2d2d; padding: 8px 12px; display: flex;
          justify-content: space-between; align-items: center;
          border-bottom: 1px solid #444; }
 header h1 { font-size: 14px; margin: 0; font-weight: 600; }
 header .stat { font-size: 11px; color: #888; }
 .panel { padding: 8px 12px; border-bottom: 1px solid #333; }
 .panel h2 { font-size: 11px; text-transform: uppercase; color: #888;
             margin: 0 0 6px; letter-spacing: 0.5px; }
 #agents { display: flex; gap: 8px; flex-wrap: wrap; }
 .agent { background: #2a2a2a; padding: 6px 10px; border-radius: 4px;
          font-size: 12px; }
 .agent.pending { background: #4a3700; color: #ffd54f; }
 #log { font-family: ui-monospace, monospace; font-size: 11px; line-height: 1.5;
        padding: 8px 12px; overflow-y: auto; height: calc(100vh - 290px);
        background: #1a1a1a; }
 .line { white-space: pre-wrap; word-break: break-word; padding: 1px 0; }
 .line .ts { color: #555; }
 .line .agent_id { color: #6a9; margin: 0 6px; }
 .line .event { color: #fc6; }
 .line.warning { color: #ffb74d; }
 .line.error, .line.hitl { color: #ef5350; }
 .line.action_result { color: #80cbc4; }
 .line.plan { color: #90caf9; }
 .line.perception { color: #b39ddb; }
 #hitl-panel { padding: 14px; background: #4a1f1f; display: none;
               border-bottom: 2px solid #c44; }
 #hitl-panel.visible { display: block; }
 #hitl-panel h2 { color: #ffb3b3; margin: 0 0 10px; font-size: 15px;
                  text-transform: none; letter-spacing: 0; font-weight: 700; }
 #hitl-panel .friendly { color: #ffe; margin-bottom: 10px; line-height: 1.5;
                         font-size: 13px; }
 #hitl-panel .hitl-shot { display: block; max-width: 100%; max-height: 240px;
                          margin: 0 0 12px; border: 1px solid #2a0; border-radius: 3px; }
 #hitl-panel .hitl-shot[src=""] { display: none; }
 #hitl-panel .hitl-actions { display: flex; gap: 8px; flex-wrap: wrap;
                             margin-bottom: 10px; }
 #hitl-panel button { color: #fff; border: none; padding: 8px 14px; cursor: pointer;
                      font-family: inherit; font-size: 13px; border-radius: 4px;
                      font-weight: 600; }
 #hitl-panel button:hover { opacity: 0.92; }
 #hitl-panel button:disabled { opacity: 0.5; cursor: not-allowed; }
 #hitl-panel .btn-primary { background: #2e7d32; }
 #hitl-panel .btn-skip    { background: #6a6f00; }
 #hitl-panel .btn-abort   { background: #b71c1c; }
 #hitl-panel .hitl-creds { display: none; margin: 6px 0 12px; padding: 8px;
                            background: #2a1717; border: 1px solid #6a3a3a;
                            border-radius: 4px; }
 #hitl-panel .hitl-creds.visible { display: block; }
 #hitl-panel .hitl-creds .row { display: flex; align-items: center;
                                  gap: 10px; margin-bottom: 4px; }
 #hitl-panel .hitl-creds label { min-width: 160px; color: #ffd6a5;
                                   font-family: ui-monospace, monospace;
                                   font-size: 12px; }
 #hitl-panel .hitl-creds input { flex: 1; background: #1a1a1a; color: #fff;
                                   border: 1px solid #555; padding: 6px 8px;
                                   border-radius: 3px; font-family: inherit; }
 #hitl-panel button.hidden { display: none; }

 /* Failure category chip */
 #hitl-panel .failure-category { display: none; margin: 0 0 10px;
                                  padding: 3px 8px; border-radius: 3px;
                                  font-size: 11px; font-family: ui-monospace,
                                  monospace; background: #2a1717;
                                  border: 1px solid #6a3a3a;
                                  color: #ffb3b3; align-self: flex-start;
                                  width: fit-content; }
 #hitl-panel .failure-category.visible { display: inline-block; }

 /* Guidance box */
 #hitl-panel .guidance-box { background: #1f2a35; border: 1px solid #355064;
                              border-radius: 4px; padding: 10px;
                              margin: 0 0 12px; }
 #hitl-panel .guidance-box .row { margin-bottom: 8px; }
 #hitl-panel .guidance-box .two-col { display: flex; gap: 10px; }
 #hitl-panel .guidance-box .two-col > div { flex: 1; }
 #hitl-panel .guidance-box label { display: block; color: #aac8e0;
                                    font-size: 11px; margin-bottom: 3px;
                                    text-transform: uppercase;
                                    letter-spacing: 0.4px; }
 #hitl-panel .guidance-box textarea,
 #hitl-panel .guidance-box input[type=text] {
   width: 100%; background: #14202a; color: #fff; border: 1px solid #4a6680;
   padding: 6px 8px; border-radius: 3px; font-family: inherit; font-size: 12px;
   resize: vertical; }
 #hitl-panel .guidance-box textarea:focus,
 #hitl-panel .guidance-box input[type=text]:focus {
   outline: none; border-color: #6fa0c8; }
 #hitl-panel .guidance-box .checkboxes { display: flex; gap: 16px;
                                          flex-wrap: wrap;
                                          margin-bottom: 0; }
 #hitl-panel .guidance-box .checkboxes label { display: inline-flex;
                                                 align-items: center; gap: 6px;
                                                 text-transform: none;
                                                 letter-spacing: 0;
                                                 font-size: 12px; color: #cfe;
                                                 cursor: pointer; }
 #hitl-panel .btn-secondary { background: #455a64; }
 #hitl-panel .tech-details { margin-top: 6px; font-size: 11px; color: #c99; }
 #hitl-panel .tech-details summary { cursor: pointer; outline: none; }
 #hitl-panel .reason { padding: 6px 0; font-family: ui-monospace, monospace;
                       color: #fdd; white-space: pre-wrap; }
</style></head>
<body>
<header>
  <h1>🤖 Agent Runtime</h1>
  <span class="stat" id="stream-status">connecting…</span>
</header>

<div class="panel">
  <h2>Agents</h2>
  <div id="agents"><span class="stat">no agents detected</span></div>
</div>

<div id="hitl-panel" class="panel">
  <h2>⚠️ The agent needs your help</h2>
  <div class="friendly" id="hitl-friendly"></div>
  <div id="hitl-category" class="failure-category"></div>
  <img id="hitl-shot" class="hitl-shot" alt="agent screenshot" />
  <div id="hitl-creds" class="hitl-creds"></div>

  <!-- Guidance section (instruction + corrected target + selector) -->
  <div class="guidance-box">
    <div class="row">
      <label>Tell the agent what to do (optional)</label>
      <textarea id="g-instruction" rows="3"
        placeholder="e.g. The username field is labelled 'Domain\\user name'. Type the username into the textbox next to that label, not into the label itself."></textarea>
    </div>
    <div class="row two-col">
      <div>
        <label title="Names the element the agent should target. Required if you want a deterministic override.">
          Corrected target name (optional)
        </label>
        <input id="g-target" type="text" placeholder="e.g. login_username">
      </div>
      <div>
        <label title="The literal value to type into the target. ONLY effective when combined with a corrected target.">
          Value to type (optional)
        </label>
        <input id="g-value" type="text"
               placeholder="e.g. vsonawane001  — typed verbatim">
      </div>
    </div>
    <div class="row">
      <label title="Playwright selector. Saved to memory if you tick the save checkbox.">
        Verified selector (optional)
      </label>
      <input id="g-selector" type="text"
             placeholder='e.g. [data-testid="login-username"]'>
    </div>
    <div class="row checkboxes">
      <label><input type="checkbox" id="g-save-memory">
        Save this correction so the agent reuses it next time
      </label>
      <label><input type="checkbox" id="g-save-sop">
        Add to SOP knowledge (helps all future tasks)
      </label>
    </div>
  </div>

  <div class="hitl-actions">
    <button type="button" id="submit-creds" class="btn-primary hidden"
            title="Submit the credentials you typed above and continue">
      🔑 Submit credentials &amp; continue
    </button>
    <button type="button" id="submit-guidance" class="btn-primary"
            title="Send your guidance to the agent and retry the step">
      💬 Submit guidance &amp; retry
    </button>
    <button type="button" class="btn-secondary" data-action="approve"
            title="Retry the same step without changing anything">
      ↻ Just retry
    </button>
    <button type="button" class="btn-skip" data-action="skip"
            title="Skip this step and continue to the next one">
      ⤼ Skip this step
    </button>
    <button type="button" class="btn-abort" data-action="abort"
            title="Stop the task entirely">
      ✕ Stop task
    </button>
  </div>

  <details class="tech-details">
    <summary>Technical details</summary>
    <div class="reason" id="hitl-reason"></div>
  </details>
  <input type="hidden" id="hitl-agent-id">
  <input type="hidden" id="hitl-hitl-id">
</div>

<div class="panel"><h2>Live activity</h2></div>
<div id="log"></div>

<script>
const $ = (s) => document.querySelector(s);
const logEl = $('#log');
const MAX_LINES = 500;
const agents = new Map();           // agent_id → {pending, total}
let activeHitl = null;              // {agent_id, hitl_id, reason}

function ts(s) { return s ? s.replace(/T/, ' ').replace(/\\..*$/, '') : ''; }

function renderAgents() {
  const ag = $('#agents');
  ag.innerHTML = '';
  if (agents.size === 0) {
    ag.innerHTML = '<span class="stat">no agents detected</span>';
    return;
  }
  for (const [id, st] of agents) {
    const el = document.createElement('div');
    el.className = 'agent' + (st.pending > 0 ? ' pending' : '');
    el.textContent = `${id} — pending: ${st.pending}`;
    ag.appendChild(el);
  }
}

function appendLine(rec) {
  const cls = rec.level === 'warning' || rec.event === 'hitl_routed' ? 'warning'
            : rec.level === 'error' ? 'error'
            : rec.event === 'hitl_routed' ? 'hitl'
            : rec.event === 'action_result' ? 'action_result'
            : rec.event === 'plan' ? 'plan'
            : rec.event === 'perception' ? 'perception'
            : '';
  const line = document.createElement('div');
  line.className = 'line ' + cls;
  const summary = rec.summary || rec.action_type || rec.status || rec.target || rec.reason || '';
  line.innerHTML =
    '<span class="ts">' + ts(rec.ts || rec.timestamp || '') + '</span> ' +
    '<span class="agent_id">' + (rec.agent_id || '') + '</span>' +
    '<span class="event">' + (rec.event || '') + '</span> ' +
    String(summary).slice(0, 240);
  logEl.appendChild(line);
  while (logEl.children.length > MAX_LINES) logEl.removeChild(logEl.firstChild);
  logEl.scrollTop = logEl.scrollHeight;
}

function showHitl(pending) {
  if (!pending || pending.length === 0) {
    $('#hitl-panel').classList.remove('visible');
    activeHitl = null;
    return;
  }
  const first = pending[0];
  if (activeHitl && activeHitl.hitl_id === first.hitl_id) return;
  activeHitl = first;
  // Fetch the full review details (so we get friendly_reason + screenshot path).
  fetch(`/api/agent/${first.agent_id}/hitl/${first.hitl_id}`)
    .then(r => r.json())
    .then(detail => {
      const ctx = detail.context || {};
      const plan = ctx.plan || {};
      const isFlagGate = plan.action_type === 'flag_human';
      let friendly = ctx.friendly_reason || detail.reason || 'The agent needs your help.';
      if (isFlagGate) {
        friendly = `The agent paused before performing: ` +
          `${plan.action_type || 'an action'} on '${plan.target || '?'}'. ` +
          `If you click "Approve & execute" the agent will perform this action. ` +
          `If you click "Skip this step" the action will be skipped.`;
      }
      $('#hitl-friendly').textContent = friendly;
      $('#hitl-reason').textContent =
        `task: ${detail.task_id}\nagent: ${detail.agent_id}\nreason: ${detail.reason}`;
      // Adapt the "Just retry" button label when the agent is paused on
      // a flag_human gate — "retry" would just re-ask the question.
      const retryBtn = document.querySelector('[data-action="approve"]');
      if (retryBtn) {
        retryBtn.textContent = isFlagGate ? '✓ Approve & execute' : '↻ Just retry';
        retryBtn.title = isFlagGate
          ? `Approve the agent to perform: ${plan.action_type} on '${plan.target}'`
          : 'Retry the same step without changing anything';
      }
      // Failure-category chip — lets the operator see at a glance what
      // type of problem the agent hit.
      const cat = classifyFailure(detail.reason);
      const catEl = $('#hitl-category');
      catEl.textContent = '⚑ ' + cat;
      catEl.classList.add('visible');
      $('#hitl-shot').src = detail.screenshot
        ? `/api/agent/${first.agent_id}/screenshot/${first.hitl_id}`
        : '';
      $('#hitl-agent-id').value = first.agent_id;
      $('#hitl-hitl-id').value = first.hitl_id;
      // Render credential input fields if the agent needs them.
      const credKeys = ctx.credential_keys || [];
      const credBox = $('#hitl-creds');
      credBox.innerHTML = '';
      if (credKeys.length) {
        for (const key of credKeys) {
          const row = document.createElement('div');
          row.className = 'row';
          const isSecret = /password|secret|token|key/i.test(key);
          row.innerHTML =
            '<label>' + key + '</label>' +
            `<input class="cred-input" data-key="${key}" ` +
            `type="${isSecret ? 'password' : 'text'}" autocomplete="off">`;
          credBox.appendChild(row);
        }
        credBox.classList.add('visible');
        $('#submit-creds').classList.add('visible');
      } else {
        credBox.classList.remove('visible');
        $('#submit-creds').classList.remove('visible');
      }
      $('#hitl-panel').classList.add('visible');
    })
    .catch(() => {
      // Fall back to the lightweight info if the detail endpoint fails.
      $('#hitl-friendly').textContent = first.reason || 'The agent needs your help.';
      $('#hitl-agent-id').value = first.agent_id;
      $('#hitl-hitl-id').value = first.hitl_id;
      $('#hitl-panel').classList.add('visible');
    });
}

async function submitHitl(action) {
  const agentId = $('#hitl-agent-id').value;
  const hitlId  = $('#hitl-hitl-id').value;
  if (!agentId || !hitlId) return;
  const buttons = document.querySelectorAll('#hitl-panel .hitl-actions button');
  buttons.forEach(b => b.disabled = true);
  try {
    const r = await fetch(
      `/api/agent/${agentId}/resolve/${hitlId}`,
      { method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action, resolver: 'floating-ui'}) }
    );
    if (r.ok) {
      $('#hitl-panel').classList.remove('visible');
      activeHitl = null;
    } else {
      alert('Could not submit: ' + r.status + ' ' + await r.text());
    }
  } finally {
    buttons.forEach(b => b.disabled = false);
  }
}
function classifyFailure(reason) {
  const t = (reason || '').toLowerCase();
  if (t.includes('selector_unresolved') || t.includes('no candidate matched'))
    return 'selector_missing';
  if (t.includes('blank page') || t.includes('not loaded'))
    return 'page_not_loaded';
  if (t.includes('blocking_modal') || t.includes('modal'))
    return 'modal_blocking';
  if (t.includes('rdp') && t.includes('disconnect'))
    return 'session_disconnected';
  if (t.includes('unresolved_credentials'))
    return 'missing_credentials';
  if (t.includes('duplicate_plan') || t.includes('same action'))
    return 'stuck_loop';
  if (t.includes('financial') || t.includes('confidence'))
    return 'low_confidence';
  return 'uncertain_target';
}

async function submitGuidance() {
  const agentId = $('#hitl-agent-id').value;
  const hitlId  = $('#hitl-hitl-id').value;
  if (!agentId || !hitlId) return;
  const instruction = $('#g-instruction').value.trim();
  const target = $('#g-target').value.trim();
  const value = ($('#g-value') ? $('#g-value').value : '');
  const selector = $('#g-selector').value.trim();
  const saveMem = $('#g-save-memory').checked;
  const saveSop = $('#g-save-sop').checked;
  if (!instruction && !target && !selector && !value) {
    alert('Please fill in at least one of: instruction, target, value, or selector.');
    return;
  }
  let action = 'retry_with_hint';
  if (target && selector && saveMem)       action = 'teach_selector';
  else if (saveSop)                         action = 'save_as_sop';
  else if (target && !instruction)          action = 'correct_target';

  const btn = $('#submit-guidance');
  btn.disabled = true;
  try {
    const r = await fetch(
      `/api/agent/${agentId}/resolve/${hitlId}`,
      { method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          action, instruction,
          corrected_target: target || null,
          corrected_value: value !== '' ? value : null,
          selector_hint: selector || null,
          save_to_memory: saveMem,
          save_to_sop: saveSop,
          resolver: 'floating-ui',
        }) }
    );
    if (r.ok) {
      $('#hitl-panel').classList.remove('visible');
      $('#g-instruction').value = '';
      $('#g-target').value = '';
      if ($('#g-value')) $('#g-value').value = '';
      $('#g-selector').value = '';
      $('#g-save-memory').checked = false;
      $('#g-save-sop').checked = false;
      activeHitl = null;
    } else {
      alert('Could not submit: ' + r.status + ' ' + await r.text());
    }
  } finally {
    btn.disabled = false;
  }
}

document.addEventListener('click', async (e) => {
  // Action buttons (approve / skip / abort)
  const actionBtn = e.target.closest('#hitl-panel .hitl-actions button[data-action]');
  if (actionBtn) { submitHitl(actionBtn.dataset.action); return; }

  // Submit guidance
  if (e.target.id === 'submit-guidance') { submitGuidance(); return; }

  // Submit credentials
  if (e.target.id === 'submit-creds') {
    const agentId = $('#hitl-agent-id').value;
    const hitlId  = $('#hitl-hitl-id').value;
    if (!agentId || !hitlId) return;
    const values = {};
    document.querySelectorAll('#hitl-creds .cred-input').forEach(inp => {
      const v = inp.value;
      if (v) values[inp.dataset.key] = v;
    });
    if (!Object.keys(values).length) { alert('Please fill in at least one field.'); return; }
    e.target.disabled = true;
    try {
      const r = await fetch(
        `/api/agent/${agentId}/resolve/${hitlId}`,
        { method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            action: 'retry_with_values',
            extracted_values: values,
            resolver: 'floating-ui',
          }) }
      );
      if (r.ok) {
        $('#hitl-panel').classList.remove('visible');
        activeHitl = null;
      } else {
        alert('Could not submit: ' + r.status + ' ' + await r.text());
      }
    } finally {
      e.target.disabled = false;
    }
  }
});

const es = new EventSource('/stream/events');
es.onopen = () => $('#stream-status').textContent = '● live';
es.onerror = () => $('#stream-status').textContent = '○ reconnecting';
es.addEventListener('audit', (e) => {
  try { appendLine(JSON.parse(e.data)); } catch (_) {}
});
es.addEventListener('hitl_state', (e) => {
  try {
    const list = JSON.parse(e.data);
    const byAgent = {};
    for (const item of list) {
      byAgent[item.agent_id] = (byAgent[item.agent_id] || 0) + 1;
    }
    // Update pending counts; preserve agents we've seen even with 0 pending.
    for (const id of agents.keys()) agents.set(id, {pending: 0, total: 0});
    for (const [id, n] of Object.entries(byAgent)) agents.set(id, {pending: n});
    renderAgents();
    showHitl(list);
  } catch (_) {}
});

// Bootstrap the agent list (in case there are no events yet).
fetch('/api/agents').then(r => r.json()).then(list => {
  for (const a of list) agents.set(a.agent_id, {pending: a.pending, total: a.total});
  renderAgents();
}).catch(() => {});
</script>
</body></html>
"""


def run_server(port: int | None = None, host: str = "127.0.0.1") -> None:
    import uvicorn
    uvicorn.run(app, host=host, port=port or settings.hitl_server_port,
                log_level="warning")


if __name__ == "__main__":
    run_server()
