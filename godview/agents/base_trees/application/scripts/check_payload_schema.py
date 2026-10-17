"""Deterministic validator for payload schema and field bounds."""

from __future__ import annotations

import json
import sys


def main() -> None:
    raw = sys.stdin.read() or "{}"
    payload = json.loads(raw)
    context = payload.get("context", {})
    expected_fields = context.get("expected_fields", [])
    actual_fields = context.get("actual_fields", [])

    missing = [f for f in expected_fields if f not in actual_fields]
    output = {
        "ok": len(missing) == 0,
        "missing_fields": missing,
        "field_count": len(actual_fields),
    }
    print(json.dumps(output))

if __name__ == "__main__":
    main()
