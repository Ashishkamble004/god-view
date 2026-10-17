# Copyright 2026 Harness² Authors & Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Substrate-agnostic core primitives for Harness²."""

from __future__ import annotations

from godview.harness.core.context import stage_context_dir
from godview.harness.core.ledger import (
    BudgetError,
    BudgetExhausted,
    BudgetLedger,
    Ledger,
    LedgerEntry,
)
from godview.harness.core.library import Library
from godview.harness.core.proposer import Proposer, ProposerCallResult
from godview.harness.core.recursion import (
    RecursionConfig,
    RecursionEngine,
    RecursionResult,
    reflect_adoption,
    run_parallel,
    run_sequential,
    trivial_selection,
)
from godview.harness.core.runmark import write_run_success
from godview.harness.core.schedule import (
    ScheduleSpec,
    group_by_domain,
    ns,
    run_domain_schedule,
)
from godview.harness.core.selection import (
    is_trivial_at_h0,
    rank_candidates,
    select_best,
    top_m,
)
from godview.harness.core.spec import (
    ALWAYS_ON,
    CHANNELS,
    INSTALL_MODES,
    ON_DEMAND,
    BenchmarkAdapter,
    BudgetSpec,
    Candidate,
    Category,
    Channel,
    ExecutionResult,
    InstallMode,
    Substrate,
    SurfaceSpec,
)
from godview.harness.core.tree import (
    HarnessTree,
    ValidationReport,
    autotrigger,
    estimate_tokens,
    render_tree,
    validate,
)

__all__ = [
    "ALWAYS_ON",
    "BenchmarkAdapter",
    "BudgetError",
    "BudgetExhausted",
    "BudgetLedger",
    "BudgetSpec",
    "CHANNELS",
    "Candidate",
    "Category",
    "Channel",
    "ExecutionResult",
    "HarnessTree",
    "INSTALL_MODES",
    "InstallMode",
    "Ledger",
    "LedgerEntry",
    "Library",
    "ON_DEMAND",
    "Proposer",
    "ProposerCallResult",
    "RecursionConfig",
    "RecursionEngine",
    "RecursionResult",
    "ScheduleSpec",
    "Substrate",
    "SurfaceSpec",
    "ValidationReport",
    "autotrigger",
    "estimate_tokens",
    "group_by_domain",
    "is_trivial_at_h0",
    "ns",
    "rank_candidates",
    "reflect_adoption",
    "render_tree",
    "run_domain_schedule",
    "run_parallel",
    "run_sequential",
    "select_best",
    "stage_context_dir",
    "top_m",
    "trivial_selection",
    "validate",
    "write_run_success",
]
