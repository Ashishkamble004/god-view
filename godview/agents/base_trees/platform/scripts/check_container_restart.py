"""Deterministic validator for container restarts and termination exit codes."""

from __future__ import annotations

import json
import sys


def main() -> None:
    raw = sys.stdin.read() or "{}"
    payload = json.loads(raw)
    context = payload.get("context", {})
    exit_code = int(context.get("exit_code", context.get("container_exit_code", 0)))
    restart_count = int(context.get("restart_count", 0))

    is_oom = exit_code == 137
    output = {
        "ok": True,
        "exit_code": exit_code,
        "restart_count": restart_count,
        "oom_exit_137": is_oom,
        "crashed": exit_code != 0,
    }
    print(json.dumps(output))

if __name__ == "__main__":
    main()
