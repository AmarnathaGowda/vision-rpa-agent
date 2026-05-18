"""Pretty-print the observe→reason→act→store trace for a task from the audit log.

Shows every loop iteration as four lines:
    PERCEPTION : app_type, confidence, summary
    PLAN       : action_type, target, confidence, [hitl?]
    RESULT     : status, duration_ms, error
    [STORED    : extracted_value snippet]

Usage:
    poetry run python scripts/show_loop_trace.py                # latest task
    poetry run python scripts/show_loop_trace.py sim_live_rdweb # filter
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

AUDIT_LOG = Path("logs/audit/agent_01.ndjson")
MAX_SUMMARY_CHARS = 70


def main() -> int:
    if not AUDIT_LOG.exists():
        print(f"audit log not found: {AUDIT_LOG}", file=sys.stderr)
        return 1
    task_filter = sys.argv[1] if len(sys.argv) > 1 else ""

    # Group events by (task_id, step) for filtered tasks.
    events: list[dict] = []
    for line in AUDIT_LOG.read_text().splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if task_filter and task_filter not in (ev.get("task_id") or ""):
            continue
        events.append(ev)

    if not events:
        print(f"no events found for task_filter={task_filter!r}", file=sys.stderr)
        return 1

    # Find the most recent task_init and show everything from there.
    last_init = max((i for i, e in enumerate(events) if e["event"] == "task_init"),
                    default=0)
    events = events[last_init:]

    print(f"┌─ task: {events[0].get('task_id')} "
          f"(events: {len(events)}) ─" + "─" * 30)

    for ev in events:
        evt = ev["event"]
        step = ev.get("step")
        if evt == "task_init":
            print(f"│ init      goal={_short(ev.get('goal',''))}")
        elif evt == "perception":
            screen = ev.get("screen", {})
            print(f"│ step {step}  observe  : app={screen.get('app_type'):8s} "
                  f"conf={screen.get('confidence',0):.2f}  "
                  f"summary={_short(screen.get('state_summary',''))}")
        elif evt == "perception_skipped":
            print(f"│ step {step}  observe  : (skipped — {ev.get('reason')})")
        elif evt == "plan":
            plan = ev.get("plan") or {}
            extra = " [HITL]" if plan.get("requires_hitl") else ""
            src = ev.get("source", "llm")
            print(f"│ step {step}  reason   : {plan.get('action_type'):10s} "
                  f"→ {_short(plan.get('target','')):30s}  "
                  f"conf={plan.get('confidence',0):.2f} ({src}){extra}")
        elif evt == "action_result":
            extracted = ev.get("extracted_value") or ""
            preview = f"  extracted={_short(extracted)}" if extracted else ""
            err = f"  err={_short(ev.get('error'))}" if ev.get("error") else ""
            print(f"│ step {step}  act      : status={ev.get('status'):8s} "
                  f"dur={ev.get('duration_ms')}ms{err}{preview}")
        elif evt == "hitl_routed":
            print(f"│ step {step}  hitl     : id={ev.get('hitl_id')}  "
                  f"reason={_short(ev.get('reason'))}")
        elif evt == "recovery_directive":
            print(f"│ step {step}  recover  : {ev.get('action')} "
                  f"({_short(ev.get('reason'))})")
        elif evt == "task_finalise":
            print(f"│ final     status={ev.get('status')} "
                  f"exit_reason={ev.get('exit_reason')} steps={ev.get('steps')}")

    print("└" + "─" * 65)
    return 0


def _short(text: str | None, n: int = MAX_SUMMARY_CHARS) -> str:
    if not text:
        return ""
    text = str(text).replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


if __name__ == "__main__":
    sys.exit(main())
