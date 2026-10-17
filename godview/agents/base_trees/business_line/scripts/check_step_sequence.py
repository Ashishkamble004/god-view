"""Deterministic validator for journey step sequencing and transition ordering."""

from __future__ import annotations

import json
import sys


def main() -> None:
    raw = sys.stdin.read() or "{}"
    payload = json.loads(raw)
    context = payload.get("context", {})
    expected_order = context.get("expected_steps", [])
    actual_order = context.get("actual_steps", [])

    is_conformant = True
    if expected_order and actual_order:
        last_idx = -1
        for step in actual_order:
            if step in expected_order:
                idx = expected_order.index(step)
                if idx < last_idx:
                    is_conformant = False
                    break
                last_idx = idx

    output = {
        "ok": is_conformant,
        "conformant": is_conformant,
        "actual_steps_count": len(actual_order),
    }
    print(json.dumps(output))

if __name__ == "__main__":
    main()
