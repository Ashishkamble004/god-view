# Copyright 2026 Harness² Authors & Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Verified run marker (`RUN_SUCCESS.json`) emitted only after a real end-to-end rollout."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from godview.harness.core.recursion import RecursionResult
from godview.harness.core.schedule import ScheduleSpec, ns


def write_run_success(
    runs_root: Path,
    spec: ScheduleSpec,
    results: list[RecursionResult],
) -> Path:
    """Write `<runs_root>/<ns>/RUN_SUCCESS.json` if every task recorded a real rollout."""
    if not results:
        raise RuntimeError("Cannot write RUN_SUCCESS.json: zero task results recorded.")

    entries: list[dict] = []
    for r in results:
        if r.h0.rollout_tokens <= 0:
            raise RuntimeError(
                f"Task {r.task_id} has zero rollout tokens on h0 — refusing to mark run verified."
            )
        manifest_path = runs_root / ns(spec) / r.domain / r.task_id / "recursion_manifest.json"
        digest = (
            hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            if manifest_path.is_file()
            else ""
        )
        entries.append(
            {
                "task_id": r.task_id,
                "domain": r.domain,
                "h0_score": r.h0.score,
                "winner_tag": r.winner.tag,
                "winner_score": r.winner.score,
                "winner_passed": r.winner.passed,
                "candidates_evaluated": len(r.candidates),
                "adopted_path": str(r.adopted_path) if r.adopted_path else None,
                "ledger": r.ledger_summary,
                "manifest_sha256": digest,
            }
        )

    out_dir = runs_root / ns(spec)
    out_dir.mkdir(parents=True, exist_ok=True)
    marker = out_dir / "RUN_SUCCESS.json"
    payload = {
        "verified": True,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "schedule": {
            "benchmark": spec.benchmark,
            "substrate": spec.substrate,
            "channel": spec.channel,
            "install_mode": spec.install_mode,
            "namespace": ns(spec),
        },
        "tasks": entries,
    }
    marker.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return marker
