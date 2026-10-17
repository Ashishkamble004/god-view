# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Deterministic validator correlating recent commit metadata with incident onset."""

from __future__ import annotations

import json
import sys


def verify_commit_regression(payload: dict) -> dict:
    context = payload.get("context", {})
    delta_minutes = float(context.get("commit_age_minutes", 12.0))
    suspect = delta_minutes <= 60.0
    score = round(max(0.5, 1.0 - (delta_minutes / 120.0)), 3)
    return {
        "ok": True,
        "invariant": "commit_regression",
        "suspect_commit_detected": suspect,
        "correlation_score": score,
    }


if __name__ == "__main__":
    raw = sys.stdin.read().strip()
    data = json.loads(raw) if raw else {}
    print(json.dumps(verify_commit_regression(data)))
