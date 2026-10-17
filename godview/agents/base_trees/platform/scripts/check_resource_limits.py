"""Deterministic validator for container resource saturation and memory limit headroom."""

from __future__ import annotations

import json
import sys


def main() -> None:
    raw = sys.stdin.read() or "{}"
    payload = json.loads(raw)
    context = payload.get("context", {})
    used_mb = float(context.get("used_memory_mb", 512.0))
    limit_mb = float(context.get("limit_memory_mb", 1024.0))

    ratio = used_mb / max(1.0, limit_mb)
    saturated = ratio >= 0.95
    output = {
        "ok": True,
        "used_memory_mb": used_mb,
        "limit_memory_mb": limit_mb,
        "memory_ratio": ratio,
        "saturated": saturated,
    }
    print(json.dumps(output))

if __name__ == "__main__":
    main()
