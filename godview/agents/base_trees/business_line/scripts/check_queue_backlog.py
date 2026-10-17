"""Deterministic validator for queue backlog depth and DLQ message accumulation."""

from __future__ import annotations

import json
import sys


def main() -> None:
    raw = sys.stdin.read() or "{}"
    payload = json.loads(raw)
    context = payload.get("context", {})
    backlog_depth = int(context.get("backlog_depth", context.get("dlq_backlog_depth", 0)))
    threshold = int(context.get("backlog_threshold", 10))

    stalled = backlog_depth > threshold
    output = {
        "ok": True,
        "backlog_depth": backlog_depth,
        "threshold": threshold,
        "queue_stalled": stalled,
        "saga_stalled": stalled,
    }
    print(json.dumps(output))

if __name__ == "__main__":
    main()
