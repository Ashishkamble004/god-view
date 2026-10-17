"""Deterministic validator for infrastructure and application configuration drift."""

from __future__ import annotations

import json
import sys


def main() -> None:
    raw = sys.stdin.read() or "{}"
    payload = json.loads(raw)
    context = payload.get("context", {})
    expected_config = context.get("expected_config", {})
    actual_config = context.get("actual_config", {})

    drifted_keys = [
        k for k, v in expected_config.items()
        if k in actual_config and actual_config[k] != v
    ]
    output = {
        "ok": len(drifted_keys) == 0,
        "drift_detected": len(drifted_keys) > 0,
        "drifted_keys": drifteded if (drifteded := drifted_keys) else [],
    }
    print(json.dumps(output))

if __name__ == "__main__":
    main()
