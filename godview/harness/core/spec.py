# Copyright 2026 Harness² Authors & Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Core data types for Harness².

Section 3 of the paper defines:
  - A 8-category harness taxonomy H = (c1, ..., c8) partitioned into
    Always-on Context (c1..c4) and On-demand Capabilities (c5..c8).
  - Two target agent substrates (OpenCode and Codex CLI) plus the
    God-View Court Agent substrate, each described here by a declarative
    SurfaceSpec so the recursion engine stays substrate-agnostic.
  - A two-layer token budget B = (R, P) separating task rollout tokens R
    from proposer reflection tokens P, plus an Always-on Context cap of
    12,000 tokens.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Category = Literal["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8"]
ALWAYS_ON: tuple[Category, ...] = ("c1", "c2", "c3", "c4")
ON_DEMAND: tuple[Category, ...] = ("c5", "c6", "c7", "c8")

# Proposer channel: Vertex AI Gemini native API or CLI harnesses.
Channel = Literal["vertex_gemini", "claude_code", "opencode"]
CHANNELS: tuple[Channel, ...] = ("vertex_gemini", "claude_code", "opencode")

# Install mode: "native" uses the substrate's own hooks/subagent mechanisms;
# "projected" compiles the entire tree into Always-on Context + a file tree
# referenced from the system prompt.
InstallMode = Literal["native", "projected"]
INSTALL_MODES: dict[str, tuple[InstallMode, ...]] = {
    "godview": ("native", "projected"),
    "opencode": ("native", "projected"),
    "codex": ("native", "projected"),
}


@dataclass(frozen=True)
class SurfaceSpec:
    """Declarative mapping from H = (c1..c8) onto an agent substrate's filesystem."""

    name: Literal["godview", "opencode", "codex"]
    # Always-on files (relative to the harness directory). Order follows (c1..c4).
    always_on_files: dict[Category, str]
    # Directory globs (relative to the harness directory) for on-demand components.
    sub_agents_glob: str  # c5
    skills_glob: str  # c6
    scripts_glob: str  # c7
    hooks_file: str  # c8
    # Maximum combined token estimate across c1..c4 before validation rejects.
    always_on_max: int = 12_000
    # Valid hook event names accepted in hooks.json on this surface.
    hook_events: frozenset[str] = frozenset({"PreToolUse", "PostToolUse", "Stop"})


@dataclass(frozen=True)
class BudgetSpec:
    """Two-layer token budget B = (R, P) from Section 3.1 (in thousands of tokens)."""

    R: int = 4_000  # rollout budget across all candidate evaluations (k tokens)
    P: int = 3_000  # proposer budget across all synthesis / reflection calls (k tokens)
    always_on_max: int = 12_000  # token cap on Always-on Context (raw tokens)


@dataclass
class Candidate:
    """One harness tree node in the recursion graph."""

    tag: str  # e.g. "h0", "r1_c0", "r2_seq"
    round_idx: int
    tree_dir: Path  # directory containing the concrete harness files
    parent_tags: list[str] = field(default_factory=list)
    notes: str = ""  # proposer's design rationale / failure attribution
    score: float | None = None
    passed: bool = False
    rollout_tokens: int = 0
    proposer_tokens: int = 0
    log_dir: Path | None = None  # rollout stdout/stderr/trace location
    validation_errors: list[str] = field(default_factory=list)


@dataclass
class ExecutionResult:
    """Outcome of running the target agent once under a candidate harness."""

    passed: bool
    score: float
    rollout_tokens: int
    log_dir: Path
    summary: str = ""  # condensed trajectory / failure excerpt fed to proposer


class Substrate(ABC):
    """Target agent harness substrate (God-View, OpenCode, or Codex CLI)."""

    spec: SurfaceSpec

    @abstractmethod
    def base_tree(self) -> Path:
        """Return path to the seed h0 harness directory for this substrate."""


class BenchmarkAdapter(ABC):
    """Per-benchmark driver implementing execute() + domain_of()."""

    name: str
    substrate: Substrate

    @abstractmethod
    def domain_of(self, task_id: str) -> str:
        """Return the task's domain name (used to namespace the Harness Library)."""

    @abstractmethod
    def task_description(self, task_id: str) -> str:
        """Return the natural-language problem statement shown to the proposer."""

    @abstractmethod
    def execute(self, task_id: str, harness_dir: Path, run_id: str) -> ExecutionResult:
        """Install `harness_dir` onto the substrate, run `task_id`, score the result."""
