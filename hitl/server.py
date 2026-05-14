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

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

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
    if action not in ("approve", "correct", "skip", "abort"):
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


@app.post("/api/agent/{agent_id}/resolve/{hitl_id}")
async def api_resolve(agent_id: str, hitl_id: int, resolution: dict) -> JSONResponse:
    store = _session_for(agent_id)
    row = store.get_hitl(hitl_id)
    if not row:
        raise HTTPException(status_code=404, detail="hitl row not found")
    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"already {row['status']}")
    if (resolution.get("action") or "") not in ("approve", "correct", "skip", "abort"):
        raise HTTPException(status_code=400, detail="invalid action")
    store.resolve_hitl(hitl_id, resolution)
    return JSONResponse({"ok": True, "hitl_id": hitl_id})


def run_server(port: int | None = None, host: str = "127.0.0.1") -> None:
    import uvicorn
    uvicorn.run(app, host=host, port=port or settings.hitl_server_port,
                log_level="warning")


if __name__ == "__main__":
    run_server()
