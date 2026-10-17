# Copyright 2026 Harness² Authors & Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Context directory protocol (Section 3.3 / Appendix C).

Before invoking the proposer, Harness² materializes a structured read/write
workspace under `<work_dir>/context/`:

  context/
    task.md                 # natural-language problem statement
    library_index.md        # domain INDEX.md from the Harness Library (if any)
    reference_experience.md # retrieved prior EXPERIENCE.md files (Round 1 only)
    prior_candidates/
      <tag>/
        meta.json           # score, passed, tokens, parent_tags, notes
        summary.txt         # condensed rollout trajectory / error excerpt
        harness/            # snapshot of that candidate's harness tree
    harness/                # EDITABLE working copy the proposer mutates in-place
      systemprompt.md ...

Giving the proposer a filesystem rather than a single prompt string lets the
meta-agent inspect arbitrary trace depths with Read/Grep and perform multi-step
surgical edits across c1..c8 within one session.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from godview.harness.core.spec import Candidate
from godview.harness.core.tree import HarnessTree


def stage_context_dir(
    work_dir: Path,
    *,
    task_md: str,
    seed_tree: HarnessTree,
    prior_candidates: list[Candidate],
    library_index: str = "",
    reference_experience: str = "",
) -> Path:
    """Materialize `<work_dir>/context/` and return the editable `context/harness` path."""
    ctx = work_dir / "context"
    if ctx.exists():
        shutil.rmtree(ctx)
    ctx.mkdir(parents=True, exist_ok=True)

    (ctx / "task.md").write_text(task_md.strip() + "\n", encoding="utf-8")
    if library_index.strip():
        (ctx / "library_index.md").write_text(library_index.strip() + "\n", encoding="utf-8")
    if reference_experience.strip():
        (ctx / "reference_experience.md").write_text(
            reference_experience.strip() + "\n", encoding="utf-8"
        )

    priors_root = ctx / "prior_candidates"
    priors_root.mkdir(parents=True, exist_ok=True)
    for cand in prior_candidates:
        cdir = priors_root / cand.tag
        cdir.mkdir(parents=True, exist_ok=True)
        meta = {
            "tag": cand.tag,
            "round": cand.round_idx,
            "parent_tags": cand.parent_tags,
            "score": cand.score,
            "passed": cand.passed,
            "rollout_tokens": cand.rollout_tokens,
            "proposer_tokens": cand.proposer_tokens,
            "notes": cand.notes,
            "validation_errors": cand.validation_errors,
        }
        (cdir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        summary_txt = ""
        if cand.log_dir and (cand.log_dir / "summary.txt").is_file():
            summary_txt = (cand.log_dir / "summary.txt").read_text(encoding="utf-8")
        (cdir / "summary.txt").write_text(summary_txt, encoding="utf-8")
        if cand.tree_dir.is_dir():
            shutil.copytree(cand.tree_dir, cdir / "harness")
        if cand.log_dir and cand.log_dir.is_dir():
            logs_dst = cdir / "logs"
            shutil.copytree(cand.log_dir, logs_dst, dirs_exist_ok=True)

    editable = ctx / "harness"
    seed_tree.clone_to(editable)
    return editable
