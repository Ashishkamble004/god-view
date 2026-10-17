# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Deterministic validator for fixed-width bureau segment byte offsets (cibil_score / cibil_stub_900)."""

from __future__ import annotations

import json
import sys


def verify_offsets(payload: dict) -> dict:
    context = payload.get("context", {})
    expected_offset = int(context.get("expected_score_offset", 108))
    actual_offset = int(context.get("actual_score_offset", 228))
    score_val = int(context.get("cibil_score", 900))
    shift_bytes = actual_offset - expected_offset
    detected = (score_val == 900) or (shift_bytes != 0)
    return {
        "ok": True,
        "invariant": "cibil_stub_900",
        "shift_bytes": shift_bytes,
        "stub_900_detected": detected,
        "recommended_offset": actual_offset,
    }


if __name__ == "__main__":
    raw = sys.stdin.read().strip()
    data = json.loads(raw) if raw else {}
    print(json.dumps(verify_offsets(data)))
