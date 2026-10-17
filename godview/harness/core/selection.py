# Copyright 2026 Harness² Authors & Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Selection & termination criteria (Section 3.1 / Section 3.4)."""

from __future__ import annotations

from godview.harness.core.spec import Candidate


def rank_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Return candidates ordered best-first by (passed, score, -rollout_tokens, -round_idx)."""
    scored = [c for c in candidates if c.score is not None]
    return sorted(
        scored,
        key=lambda c: (
            bool(c.passed),
            float(c.score or 0.0),
            -int(c.rollout_tokens),
            int(c.round_idx),
        ),
        reverse=True,
    )


def select_best(candidates: list[Candidate]) -> Candidate:
    ranked = rank_candidates(candidates)
    if not ranked:
        raise ValueError("No evaluated candidates available for selection")
    return ranked[0]


def top_m(candidates: list[Candidate], m: int = 2) -> list[Candidate]:
    return rank_candidates(candidates)[: max(1, m)]


def is_trivial_at_h0(h0: Candidate, threshold: float = 1.0) -> bool:
    """Trivial-task early exit (Section 3.4): h0 already solves the task on Round 0."""
    return bool(h0.passed) and (h0.score or 0.0) >= threshold
