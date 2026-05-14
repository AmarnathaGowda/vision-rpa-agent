"""Entry point — start one agent instance for a given task."""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import yaml


def preflight_checks() -> None:
    """Fail fast if required services are not available."""
    from openai import OpenAI

    from config.settings import settings

    client = OpenAI(base_url=settings.inference_url, api_key="ignored", timeout=10.0)
    try:
        client.models.list()
    except Exception as e:
        print(f"\n[ERROR] Inference server not reachable at {settings.inference_url}")
        print("  Development: run  ollama serve")
        print("  Production:  check vLLM on inference server")
        print(f"  Details: {e}\n")
        sys.exit(1)


_TEMPLATE_RE = re.compile(r"\{\{\s*([A-Z_][A-Z0-9_]*)\s*\}\}")


def _expand(value, env: dict[str, str]):
    """Recursively substitute {{VAR}} occurrences in strings."""
    if isinstance(value, str):
        return _TEMPLATE_RE.sub(lambda m: env.get(m.group(1), m.group(0)), value)
    if isinstance(value, list):
        return [_expand(v, env) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v, env) for k, v in value.items()}
    return value


def load_task(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Task YAML must be a mapping, got {type(data).__name__}: {path}")

    # Substitute {{LD_BASE_URL}} / {{IIM_BASE_URL}} etc. from settings + env.
    from config.settings import settings
    env = {
        "LD_BASE_URL": settings.ld_base_url,
        "IIM_BASE_URL": settings.iim_base_url,
        "RDWEB_URL":   settings.rdweb_url,
        **{k: v for k, v in os.environ.items() if k.isupper()},
    }
    return _expand(data, env)


def _build_router(task: dict, agent_id: str):
    """Return (router_or_executor, cleanup_callable). Browser apps get a real session."""
    from agent.router import ActionRouter
    from executors.browser import BrowserExecutor, BrowserSession
    from executors.selectors import SelectorResolver
    from config.locators import rdweb

    app = task.get("app", "browser")
    if app == "browser":
        session = BrowserSession().__enter__()
        resolver = SelectorResolver(locator_map=rdweb.ALL)
        executor = BrowserExecutor(session.page, resolver=resolver)
        router = ActionRouter(browser=executor)
        return router, (lambda: session.__exit__(None, None, None))
    # Phase 3+ executors will land here. For now: stub fallback.
    from agent.loop import StubExecutor
    return StubExecutor(), (lambda: None)


def main() -> int:
    parser = argparse.ArgumentParser(description="Vision RPA Agent")
    parser.add_argument("--task", required=True, help="Path to task YAML file")
    parser.add_argument("--agent-id", default=None, help="Override AGENT_ID from .env")
    parser.add_argument("--resume", action="store_true", help="Resume interrupted task")
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip inference-server reachability check (use for offline smoke tests)",
    )
    args = parser.parse_args()

    from config.logging_config import configure_logging, get_logger
    from config.settings import settings

    agent_id = args.agent_id or settings.agent_id
    configure_logging(agent_id)
    log = get_logger(__name__)

    task_path = Path(args.task)
    if not task_path.exists():
        log.error("task_file_not_found", path=str(task_path))
        return 1

    try:
        task = load_task(task_path)
    except (yaml.YAMLError, ValueError) as e:
        log.error("task_file_invalid", path=str(task_path), error=str(e))
        return 1

    log.info(
        "agent_start",
        task_id=task.get("task_id"),
        task_type=task.get("task_type"),
        task_file=task_path.name,
        inference_url=settings.inference_url,
        model=settings.model_name,
        mode="simulation" if settings.use_simulation else "production",
        resume=args.resume,
    )

    # Phase 0 smoke task: skeleton check only — no VLM, no loop.
    if task.get("task_type") == "smoke":
        log.info("smoke_task_ok",
                 message="skeleton imports cleanly; loop not invoked for smoke task")
        return 0

    # Deterministic tasks skip the VLM entirely; preflight only matters for VLM mode.
    if not args.skip_preflight and not task.get("steps"):
        preflight_checks()

    from agent.loop import AgentLoop
    from memory.session import SessionMemory

    session = SessionMemory(agent_id=agent_id)
    executor, cleanup = _build_router(task, agent_id)
    try:
        loop = AgentLoop(session=session, executor=executor, agent_id=agent_id)
        result = loop.run(task)
    finally:
        cleanup()

    log.info("agent_complete", **result)
    return 0 if result["status"] != "failed" else 2


if __name__ == "__main__":
    sys.exit(main())
