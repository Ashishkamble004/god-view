"""Deterministic validator for data distribution and statistical metric shifts."""

from __future__ import annotations

import json
import sys


def main() -> None:
    raw = sys.stdin.read() or "{}"
    payload = json.loads(raw)
    context = payload.get("context", {})
    statistic = float(context.get("drift_statistic", context.get("cusum_score", 1.0)))
    threshold = float(context.get("drift_threshold", context.get("cusum_threshold", 4.0)))

    drift_detected = statistic > threshold
    output = {
        "ok": True,
        "statistic": statistic,
        "threshold": threshold,
        "drift_detected": drift_detected,
    }
    print(json.dumps(output))

if __name__ == "__main__":
    main()
