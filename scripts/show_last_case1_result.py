"""Print the most recent Case 1 result from the audit log.

Usage:
    poetry run python scripts/show_last_case1_result.py
    poetry run python scripts/show_last_case1_result.py case1_allstate_fixture
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT_TASK_FILTER = "case1"
AUDIT_LOG = Path("logs/audit/agent_01.ndjson")


def main() -> int:
    if not AUDIT_LOG.exists():
        print(f"audit log not found: {AUDIT_LOG}", file=sys.stderr)
        return 1
    task_filter = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TASK_FILTER

    last_event = None
    with AUDIT_LOG.open() as f:
        for line in f:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("event") != "action_result":
                continue
            if task_filter not in (ev.get("task_id") or ""):
                continue
            last_event = ev

    if last_event is None:
        print(f"no action_result events found for task_filter={task_filter!r}",
              file=sys.stderr)
        return 1

    raw = last_event.get("extracted_value") or ""
    if not raw:
        print(f"event has no extracted_value: {last_event}", file=sys.stderr)
        return 1
    result = json.loads(raw)

    print(f"task_id      : {last_event['task_id']}")
    print(f"timestamp    : {last_event['ts']}")
    print(f"status       : {last_event['status']}")
    print(f"duration_ms  : {last_event['duration_ms']}")
    print()
    print("Case1Result:")
    for k, v in result.items():
        if k == "candidates":
            print(f"  {k:22s} = {len(v)} items")
        else:
            print(f"  {k:22s} = {v!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
