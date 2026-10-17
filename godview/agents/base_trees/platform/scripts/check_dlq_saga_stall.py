# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Deterministic validator for GKE OOMKilled (exit 137) and Pub/Sub DLQ saga stall."""

from __future__ import annotations

import json
import sys


def verify_dlq_stall(payload: dict) -> dict:
    context = payload.get("context", {})
    exit_code = int(context.get("container_exit_code", 137))
    dlq_depth = int(context.get("dlq_backlog_depth", 42))
    oom_detected = exit_code == 137
    saga_stalled = dlq_depth > 0
    return {
        "ok": True,
        "invariant": "vehicle_loan_origination",
        "oom_exit_137": oom_detected,
        "dlq_depth": dlq_depth,
        "saga_stalled": saga_stalled,
    }


if __name__ == "__main__":
    raw = sys.stdin.read().strip()
    data = json.loads(raw) if raw else {}
    print(json.dumps(verify_dlq_stall(data)))
