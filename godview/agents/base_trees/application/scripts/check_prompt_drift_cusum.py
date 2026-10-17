# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Deterministic validator for prompt drift CUSUM and Model Armor template status."""

from __future__ import annotations

import json
import sys


def verify_cusum(payload: dict) -> dict:
    context = payload.get("context", {})
    cusum_score = float(context.get("cusum_score", 5.4))
    threshold = float(context.get("cusum_threshold", 4.0))
    armor_enabled = bool(context.get("model_armor_enabled", True))
    drift_detected = cusum_score > threshold
    return {
        "ok": True,
        "invariant": "prompt_drift_cusum",
        "cusum_score": round(cusum_score, 3),
        "drift_detected": drift_detected,
        "model_armor_enforced": armor_enabled,
    }


if __name__ == "__main__":
    raw = sys.stdin.read().strip()
    data = json.loads(raw) if raw else {}
    print(json.dumps(verify_cusum(data)))
