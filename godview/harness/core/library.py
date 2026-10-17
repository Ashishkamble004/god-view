# Copyright 2026 Harness² Authors & Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Harness Library (Section 3.5): cross-task accumulation of adapted harnesses."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from godview.harness.core import prompts
from godview.harness.core.context import stage_context_dir
from godview.harness.core.ledger import Ledger
from godview.harness.core.proposer import Proposer
from godview.harness.core.spec import Candidate, SurfaceSpec
from godview.harness.core.tree import HarnessTree


class Library:
    """Persistent domain-partitioned store of adapted harnesses + EXPERIENCE.md notes."""

    def __init__(self, root: Path, surface: SurfaceSpec) -> None:
        self.root = root / surface.name
        self.surface = surface
        self.root.mkdir(parents=True, exist_ok=True)

    def domain_dir(self, domain: str) -> Path:
        d = self.root / _slug(domain)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def read_index(self, domain: str) -> str:
        idx = self.domain_dir(domain) / "INDEX.md"
        return idx.read_text(encoding="utf-8") if idx.is_file() else ""

    def retrieve_experiences(self, domain: str, task_md: str, max_items: int = 3) -> str:
        """Keyword-overlap retrieval over prior EXPERIENCE.md files in `domain`."""
        harnesses_root = self.domain_dir(domain) / "harnesses"
        if not harnesses_root.is_dir():
            return ""
        query_terms = set(_tokenize(task_md))
        scored: list[tuple[int, str, str]] = []
        for tdir in sorted(harnesses_root.iterdir()):
            exp = tdir / "EXPERIENCE.md"
            if not exp.is_file():
                continue
            body = exp.read_text(encoding="utf-8")
            overlap = len(query_terms & set(_tokenize(body)))
            scored.append((overlap, tdir.name, body))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        chosen = [f"### Prior Task `{tid}`\n{body.strip()}" for _, tid, body in scored[:max_items]]
        return "\n\n---\n\n".join(chosen)

    def adopt(
        self,
        *,
        domain: str,
        task_id: str,
        task_md: str,
        winner: Candidate,
        all_candidates: list[Candidate],
        proposer: Proposer | None,
        ledger: Ledger,
        work_dir: Path,
    ) -> Path:
        """Persist `winner` into the domain library and synthesize EXPERIENCE.md + INDEX.md."""
        ddir = self.domain_dir(domain)
        dest = ddir / "harnesses" / _slug(task_id)
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(winner.tree_dir, dest / "harness")

        exp_body = _default_experience(task_id, domain, winner, all_candidates)
        idx_entry = (
            f"- **`{task_id}`** (score={winner.score}, tag={winner.tag}): "
            f"{_first_line(winner.notes) or 'Adapted harness with task-specific skills and scripts.'}"
        )

        if proposer is not None and ledger.can_propose():
            reflect_dir = work_dir / "reflect"
            stage_context_dir(
                reflect_dir,
                task_md=task_md,
                seed_tree=HarnessTree(winner.tree_dir, self.surface),
                prior_candidates=all_candidates,
                library_index=self.read_index(domain),
            )
            prompt = prompts.experience(task_id=task_id, domain=domain, best_tag=winner.tag)
            res = proposer.run(reflect_dir, prompt)
            ledger.charge_p(res.tokens_used, tag=f"reflect:{task_id}", note="Library reflection")
            gen_exp = reflect_dir / "context" / "EXPERIENCE.md"
            gen_idx = reflect_dir / "context" / "INDEX_ENTRY.md"
            if gen_exp.is_file() and gen_exp.read_text(encoding="utf-8").strip():
                exp_body = gen_exp.read_text(encoding="utf-8").strip()
            if gen_idx.is_file() and gen_idx.read_text(encoding="utf-8").strip():
                idx_entry = gen_idx.read_text(encoding="utf-8").strip()

        (dest / "EXPERIENCE.md").write_text(exp_body + "\n", encoding="utf-8")
        # Also mirror latest domain experience at `<domain>/EXPERIENCE.md` for direct inspection.
        (ddir / "EXPERIENCE.md").write_text(exp_body + "\n", encoding="utf-8")
        self._append_index(ddir / "INDEX.md", domain, task_id, idx_entry)
        return dest

    @staticmethod
    def _append_index(index_path: Path, domain: str, task_id: str, entry: str) -> None:
        header = f"# Harness Library Index — Domain `{domain}`\n\n"
        existing = index_path.read_text(encoding="utf-8") if index_path.is_file() else header
        filtered = [ln for ln in existing.splitlines() if f"`{task_id}`" not in ln]
        body = "\n".join(filtered).rstrip() + "\n" + entry.strip() + "\n"
        index_path.write_text(body, encoding="utf-8")


def _default_experience(
    task_id: str,
    domain: str,
    winner: Candidate,
    all_candidates: list[Candidate],
) -> str:
    traj = "\n".join(
        f"- `{c.tag}` (round {c.round_idx}, parents={c.parent_tags}): "
        f"score={c.score}, passed={c.passed} — {_first_line(c.notes)}"
        for c in all_candidates
    )
    return (
        f"# Experience — `{task_id}` ({domain})\n\n"
        f"## Task Fingerprint\nAdapted harness `{winner.tag}` achieved score={winner.score}.\n\n"
        f"## Evolution Trajectory\n{traj}\n\n"
        f"## Reusable Components\n{winner.notes or 'See harness/ directory.'}\n"
    )


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._\-]+", "_", s.strip()) or "default"


def _tokenize(s: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[a-zA-Z]{4,}", s)]


def _first_line(s: str) -> str:
    for ln in s.splitlines():
        if ln.strip():
            return ln.strip()[:160]
    return ""
