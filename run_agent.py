"""Entry point — start one agent instance for a given task."""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import yaml


def preflight_checks() -> None:
    """Fail fast if the configured LLM provider is unreachable.

    Provider-aware: only pings the endpoint the active provider will use.
    External providers (openai / claude) only require their API key be set;
    a network reachability check would burn an API call before any real work.
    """
    from config.settings import settings

    provider = (getattr(settings, "llm_provider", "ollama") or "ollama").lower()

    if provider == "ollama":
        from openai import OpenAI
        client = OpenAI(base_url=settings.inference_url, api_key="ignored",
                        timeout=10.0)
        try:
            client.models.list()
        except Exception as e:
            print(f"\n[ERROR] Ollama not reachable at {settings.inference_url}")
            print("  Run:  ollama serve")
            print("  Or set LLM_PROVIDER=openai (with OPENAI_API_KEY) for demo.")
            print(f"  Details: {e}\n")
            sys.exit(1)
        return

    if provider == "openai":
        import os
        key = getattr(settings, "openai_api_key", "") or os.environ.get(
            "OPENAI_API_KEY", "")
        if not key:
            print("\n[ERROR] LLM_PROVIDER=openai but OPENAI_API_KEY is unset.\n")
            sys.exit(1)
        return

    if provider == "claude":
        import os
        key = getattr(settings, "anthropic_api_key", "") or os.environ.get(
            "ANTHROPIC_API_KEY", "")
        if not key:
            print("\n[ERROR] LLM_PROVIDER=claude but ANTHROPIC_API_KEY is unset.\n")
            sys.exit(1)
        return

    print(f"\n[ERROR] Unknown LLM_PROVIDER={provider!r}\n")
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


def _scopes_for_task(task: dict) -> set[str]:
    """Determine which executors a task needs based on its `app` and step list."""
    from agent.router import ROUTING_TABLE

    scopes: set[str] = set()
    declared = task.get("app")
    if declared and declared != "auto":
        scopes.add(declared)
    for step in task.get("steps") or []:
        s = step.get("app")
        if s and s != "auto":
            scopes.add(s)
        else:
            scopes.add(ROUTING_TABLE.get(step.get("action_type", ""), "unknown"))
    # `unknown` means LLM-driven planning may pick anything → assume browser.
    if not scopes or scopes == {"unknown"}:
        scopes = {"browser"}
    scopes.discard("noop")
    scopes.discard("unknown")
    return scopes


def _build_router(task: dict, agent_id: str):
    """Return (router, cleanup_callable). Constructs only the executors the task needs."""
    from agent.router import ActionRouter
    from config.locators import rdweb

    scopes = _scopes_for_task(task)
    # Case 1 (and similar) flows use the `tool` executor via deterministic
    # guardrails that don't appear in the task YAML's pre-declared steps.
    # Auto-register the tool executor for these tasks so guardrails can
    # emit `extract → case1_*` (with app=tool) and dispatch correctly.
    if (task.get("task_type") or "").startswith("case"):
        scopes.add("tool")
    cleanups: list = []

    browser_executor = None
    if "browser" in scopes:
        from executors.browser import BrowserExecutor, BrowserSession
        from executors.selectors import SelectorResolver
        bs = BrowserSession().__enter__()
        cleanups.append(lambda: bs.__exit__(None, None, None))
        browser_executor = BrowserExecutor(
            bs.page, resolver=SelectorResolver(locator_map=rdweb.ALL)
        )

    desktop_executor = None
    if scopes & {"desktop", "rdp", "file"}:
        from executors.desktop import DesktopExecutor
        desktop_executor = DesktopExecutor()

    rdp_handler = None
    if "rdp" in scopes:
        from executors.rdp import make_rdp_handler_for_runtime
        rdp_handler = make_rdp_handler_for_runtime(desktop_executor)
        cleanups.append(rdp_handler.disconnect)

    file_executor = None
    if "file" in scopes:
        from executors.file_ops import FileExecutor
        file_executor = FileExecutor(desktop=desktop_executor)

    tool_executor = None
    if "tool" in scopes:
        # Tool executor handles evaluation-only legacy handlers (Case 1).
        from executors.case1_tool import Case1ToolExecutor
        tool_executor = Case1ToolExecutor()

    router = ActionRouter(
        browser=browser_executor,
        desktop=desktop_executor,
        rdp=rdp_handler,
        file=file_executor,
        tool=tool_executor,
    )

    def cleanup():
        for fn in reversed(cleanups):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass

    return router, cleanup


def _start_runtime_ui(log, *, port: int) -> list:
    """Spawn the FastAPI dashboard + pywebview floating window.

    Returns the list of subprocess handles for teardown. Idempotent: if a
    server is already listening on `port`, the FastAPI launch is skipped.
    """
    import socket
    import subprocess
    import sys as _sys

    procs: list = []

    # 1. FastAPI dashboard — skip if port already bound AND that server
    #    has the /runtime route (i.e. is the current version). A stale
    #    older server occupying the port would cause "Not Found" in the
    #    floating window; refuse to use it.
    import urllib.request
    def _has_runtime(p: int) -> bool:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{p}/runtime", timeout=0.5
            ) as r:
                return r.status == 200
        except Exception:
            return False

    def _port_busy(p: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            return s.connect_ex(("127.0.0.1", p)) == 0

    if _port_busy(port):
        if _has_runtime(port):
            log.info("runtime_ui_dashboard_already_running", port=port)
        else:
            log.error(
                "runtime_ui_stale_dashboard_detected",
                port=port,
                hint=f"port {port} is bound but /runtime returns non-200. "
                     f"Kill the old hitl.server process and retry.",
            )
            # Don't try to use the stale one and don't spawn a second on
            # the same port — print loud guidance and continue without UI.
            print(
                f"\n[runtime-ui] WARNING: port {port} is bound by a stale "
                f"dashboard (no /runtime route).\n"
                f"            Kill it and re-run, e.g.:\n"
                f"              lsof -ti:{port} | xargs kill\n"
                f"            Continuing without the floating UI for this run.\n",
                file=_sys.stderr,
            )
            return procs   # Skip the window too
    else:
        log.info("runtime_ui_starting_dashboard", port=port)
        procs.append(subprocess.Popen(
            [_sys.executable, "-m", "hitl.server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ))

    # 2. Floating window — runs in its own process; on Windows it must be
    #    the main thread of its process, hence subprocess (not threading).
    log.info("runtime_ui_starting_window", port=port)
    procs.append(subprocess.Popen(
        [_sys.executable, "-m", "hitl.floating_window",
         "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ))
    return procs


def _stop_proc(p) -> None:
    """Try graceful terminate, then kill after a short grace period."""
    if p is None or p.poll() is not None:
        return
    try:
        p.terminate()
        try:
            p.wait(timeout=3)
        except Exception:  # noqa: BLE001
            p.kill()
    except Exception:  # noqa: BLE001
        pass


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
    parser.add_argument(
        "--no-hitl-wait",
        action="store_true",
        help="Exit immediately on HITL pause instead of waiting for dashboard "
             "resolution. Default: wait (browser stays open).",
    )
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Don't launch the floating runtime UI. Default: spawn the UI "
             "+ a local FastAPI dashboard on port HITL_SERVER_PORT (8080).",
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

    # Spawn the FastAPI dashboard + floating runtime UI as decoupled
    # subprocesses (event-driven: they read SQLite + audit NDJSON, never
    # touch the agent loop). Skipped with --no-ui or if already running.
    ui_procs: list = []
    if not args.no_ui:
        ui_procs = _start_runtime_ui(log, port=settings.hitl_server_port)

    session = SessionMemory(agent_id=agent_id)
    executor, cleanup = _build_router(task, agent_id)
    try:
        loop = AgentLoop(session=session, executor=executor, agent_id=agent_id)
        # Wire the Playwright page into perception so capture() reads from
        # the browser directly — bypasses mss focus issues on macOS.
        browser_exec = getattr(executor, "browser", None)
        if browser_exec is not None and getattr(browser_exec, "page", None):
            loop.perception.page = browser_exec.page
        if args.no_hitl_wait:
            # Legacy path — exit immediately on HITL pause (closes the browser).
            result = loop.run(task)
        else:
            # Default — HITLRunner blocks on dashboard resolution and resumes
            # the same loop, so the browser session stays alive across HITL.
            from hitl.runner import HITLRunner
            result = HITLRunner(loop=loop).run_task(task)
    finally:
        cleanup()
        for p in ui_procs:
            _stop_proc(p)

    log.info("agent_complete", **result)
    return 0 if result["status"] != "failed" else 2


if __name__ == "__main__":
    sys.exit(main())
