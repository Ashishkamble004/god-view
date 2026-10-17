# Copyright 2026 Harness² Authors & Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Domain-consistent scheduling (Section 3.5 / Section 4.1)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from godview.harness.core.library import Library
from godview.harness.core.proposer import Proposer
from godview.harness.core.recursion import (
    RecursionConfig,
    RecursionEngine,
    RecursionResult,
)
from godview.harness.core.spec import BenchmarkAdapter, Channel, InstallMode


@dataclass(frozen=True)
class ScheduleSpec:
    """Full configuration of a benchmark schedule run."""

    benchmark: str  # "godview" | "swebench" | "swebench_pro" | "scicode" | "litqa2"
    substrate: str  # "godview" | "opencode" | "codex"
    channel: Channel = "vertex_gemini"
    install_mode: InstallMode = "native"
    max_domain_workers: int = 4


def ns(spec: ScheduleSpec) -> str:
    """Canonical output namespace `<benchmark>/<substrate>__<channel>__<install_mode>`."""
    return f"{spec.benchmark}/{spec.substrate}__{spec.channel}__{spec.install_mode}"


def group_by_domain(adapter: BenchmarkAdapter, task_ids: list[str]) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = defaultdict(list)
    for tid in task_ids:
        buckets[adapter.domain_of(tid)].append(tid)
    return dict(buckets)


def run_domain_schedule(
    *,
    spec: ScheduleSpec,
    adapter: BenchmarkAdapter,
    proposer: Proposer,
    task_ids: list[str],
    runs_root: Path,
    library_root: Path,
    recursion_cfg: RecursionConfig | None = None,
    on_task_done: Callable[[RecursionResult], None] | None = None,
) -> list[RecursionResult]:
    """Execute `task_ids` sequentially within each domain and concurrently across domains."""
    run_dir = runs_root / ns(spec)
    lib_dir = library_root / f"{spec.substrate}__{spec.channel}__{spec.install_mode}"
    library = Library(lib_dir, adapter.substrate.spec)
    buckets = group_by_domain(adapter, task_ids)

    def _run_domain(domain: str, tids: list[str]) -> list[RecursionResult]:
        engine = RecursionEngine(
            adapter=adapter,
            proposer=proposer,
            library=library,
            work_root=run_dir / domain,
            config=recursion_cfg,
        )
        out: list[RecursionResult] = []
        for tid in tids:
            res = engine.run_task(tid)
            out.append(res)
            if on_task_done:
                on_task_done(res)
        return out

    results: list[RecursionResult] = []
    workers = max(1, min(spec.max_domain_workers, len(buckets) or 1))
    if workers == 1:
        for dom, tids in buckets.items():
            results.extend(_run_domain(dom, tids))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_run_domain, dom, tids): dom for dom, tids in buckets.items()}
            for f in as_completed(futs):
                results.extend(f.result())
    return results
