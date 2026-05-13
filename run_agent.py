"""Entry point — start one agent instance for a given task."""
from __future__ import annotations

import argparse
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


def load_task(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Task YAML must be a mapping, got {type(data).__name__}: {path}")
    return data


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

    if not args.skip_preflight:
        preflight_checks()

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
    # AgentLoop.run() arrives in Phase 1.
    log.info("agent_loop_not_implemented", phase="Phase 1 target")
    return 0


if __name__ == "__main__":
    sys.exit(main())
