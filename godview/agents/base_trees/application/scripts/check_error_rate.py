"""Deterministic validator for service HTTP error rate thresholds."""

from __future__ import annotations

import json
import sys


def main() -> None:
    raw = sys.stdin.read() or "{}"
    payload = json.loads(raw)
    context = payload.get("context", {})
    total_requests = int(context.get("total_requests", 100))
    error_count = int(context.get("error_count", 0))
    rate = error_count / max(1, total_requests)
    threshold = float(context.get("error_threshold", 0.05))

    breached = rate > threshold
    output = {
        "ok": True,
        "error_rate": rate,
        "threshold": threshold,
        "breached": breached,
    }
    print(json.dumps(output))

if __name__ == "__main__":
    main()
