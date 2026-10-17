"""Deterministic validator for database query latency thresholds."""

from __future__ import annotations

import json
import sys


def main() -> None:
    raw = sys.stdin.read() or "{}"
    payload = json.loads(raw)
    context = payload.get("context", {})
    latency_ms = float(context.get("query_latency_ms", 10.0))
    threshold_ms = float(context.get("latency_threshold_ms", 500.0))

    breached = latency_ms > threshold_ms
    output = {
        "ok": True,
        "query_latency_ms": latency_ms,
        "threshold_ms": threshold_ms,
        "breached": breached,
    }
    print(json.dumps(output))

if __name__ == "__main__":
    main()
