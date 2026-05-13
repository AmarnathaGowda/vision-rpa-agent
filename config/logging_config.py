"""Centralised logging — structlog (JSON to file) + Rich (pretty console)."""
from __future__ import annotations

import logging
from pathlib import Path

import structlog
from rich.logging import RichHandler

from config.settings import settings

_configured = False


def configure_logging(agent_id: str) -> None:
    """Idempotent — safe to call from every entry point."""
    global _configured
    if _configured:
        return

    log_dir = Path(settings.audit_log_dir).parent
    log_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = log_dir / "agent.jsonl"

    file_handler = logging.FileHandler(jsonl_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(message)s"))

    console_handler = RichHandler(rich_tracebacks=True, show_path=False, markup=False)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)
    root.setLevel(logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    structlog.contextvars.bind_contextvars(agent_id=agent_id)
    _configured = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
