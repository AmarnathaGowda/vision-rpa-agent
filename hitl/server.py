"""FastAPI HITL review dashboard — human sees screenshot + context, approves."""
from __future__ import annotations
from fastapi import FastAPI
import uvicorn

app = FastAPI(title="Vision RPA Agent — HITL Dashboard")


@app.get("/")
async def dashboard():
    return {"status": "HITL dashboard — not yet implemented"}


@app.get("/review/{task_id}")
async def review(task_id: str):
    raise NotImplementedError


@app.post("/resolve/{task_id}")
async def resolve(task_id: str):
    raise NotImplementedError


def run_server(port: int = 8080) -> None:
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    run_server()
