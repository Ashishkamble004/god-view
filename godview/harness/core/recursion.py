# Copyright 2026 Harness² Authors & Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Recursion engine (Algorithm 1, Section 3.4).

At each round r = 1..max_rounds:
  - Stage the full pool of prior candidates under `context/prior_candidates/`
    and let the proposer autonomously select its evolution operator:
      * Single-parent refinement (`mode: refine`), OR
      * Multi-parent crossover (`mode: crossover`) merging complementary
        components from multiple prior trees.
  - Validate every proposed tree against the 5 structural checks before
    spending rollout tokens R; if validation fails, run one lightweight
    repair pass on the same tree.
  - After termination (budget exhausted, early pass on solved task, or
    `max_rounds` reached), select the best candidate and, if it strictly
    beats h0, adopt it into the domain Harness Library (`Library.adopt`).
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path

from godview.harness.core import prompts, selection
from godview.harness.core.context import stage_context_dir
from godview.harness.core.ledger import Ledger
from godview.harness.core.library import Library
from godview.harness.core.proposer import Proposer
from godview.harness.core.spec import BenchmarkAdapter, BudgetSpec, Candidate
from godview.harness.core.tree import HarnessTree, validate

_BRANCH_HINTS: tuple[str, ...] = (
    "Focus on deterministic verification scripts (`scripts/*.py`) + `Stop` hooks that catch "
    "silent correctness bugs before the agent finishes.",
    "Focus on workflow decomposition: add a domain-specific `skills/<name>/SKILL.md` with clear "
    "`autotrigger:` criteria and a concise checklist in `systemprompt.md`.",
    "Focus on guardrails and failure-recovery rules in `guardrails.md` + `tool_descriptions/` "
    "to prevent common tool-misuse and premature termination traps.",
)


@dataclass
class RecursionConfig:
    """Configuration for a Harness² recursion session."""

    budget: BudgetSpec = field(default_factory=BudgetSpec)
    max_rounds: int = 3
    parallel_branches: int = 2  # K parallel branches on Round 1
    early_stop_score: float = 1.0
    adopt_into_library: bool = True


@dataclass
class RecursionResult:
    """Result of running `RecursionEngine.run_task(task_id)`."""

    task_id: str
    domain: str
    winner: Candidate
    h0: Candidate
    candidates: list[Candidate]
    adopted_path: Path | None
    ledger_summary: dict[str, int | float]


class RecursionEngine:
    """Orchestrates h0 -> Round 1..T evolution -> selection -> Library adoption."""

    def __init__(
        self,
        adapter: BenchmarkAdapter,
        proposer: Proposer,
        library: Library,
        work_root: Path,
        config: RecursionConfig | None = None,
    ) -> None:
        self.adapter = adapter
        self.proposer = proposer
        self.library = library
        self.work_root = work_root
        self.cfg = config or RecursionConfig()

    def run_task(self, task_id: str) -> RecursionResult:
        task_dir = self.work_root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        ledger = Ledger(self.cfg.budget).bind(task_dir / "ledger.json")

        domain = self.adapter.domain_of(task_id)
        task_md = self.adapter.task_description(task_id)
        surface = self.adapter.substrate.spec

        # --- Round 0: evaluate seed harness h0 ---
        h0_dir = task_dir / "candidates" / "h0" / "harness"
        HarnessTree(self.adapter.substrate.base_tree(), surface).clone_to(h0_dir)
        h0 = Candidate(
            tag="h0",
            round_idx=0,
            tree_dir=h0_dir,
            notes="Base seed harness h0",
        )
        self._evaluate(task_id, h0, task_dir, ledger)
        candidates: list[Candidate] = [h0]

        # Trivial-task early exit (Section 3.4): if h0 already achieves full score,
        # skip proposer calls entirely.
        if selection.is_trivial_at_h0(h0, self.cfg.early_stop_score):
            self._write_manifest(task_dir, task_id, domain, h0, candidates, None, ledger)
            return RecursionResult(task_id, domain, h0, h0, candidates, None, ledger.summary())

        lib_index = self.library.read_index(domain)
        ref_exp = self.library.retrieve_experiences(domain, task_md)

        # --- Rounds 1..max_rounds ---
        for r in range(1, self.cfg.max_rounds + 1):
            if not (ledger.can_rollout() and ledger.can_propose()):
                break

            k = max(1, self.cfg.parallel_branches) if r == 1 else 1
            round_cands = self._propose_round(
                task_id=task_id,
                task_md=task_md,
                round_idx=r,
                k=k,
                prior_candidates=list(candidates),
                lib_index=lib_index if r == 1 else "",
                ref_exp=ref_exp if r == 1 else "",
                task_dir=task_dir,
                ledger=ledger,
            )
            candidates.extend(round_cands)

            best_now = selection.select_best(candidates)
            if best_now.passed and (best_now.score or 0.0) >= self.cfg.early_stop_score:
                break

        winner = selection.select_best(candidates)
        adopted: Path | None = None
        if self.cfg.adopt_into_library and (winner.score or 0.0) > (h0.score or 0.0):
            adopted = self.library.adopt(
                domain=domain,
                task_id=task_id,
                task_md=task_md,
                winner=winner,
                all_candidates=candidates,
                proposer=self.proposer,
                ledger=ledger,
                work_dir=task_dir,
            )

        self._write_manifest(task_dir, task_id, domain, winner, candidates, adopted, ledger)
        return RecursionResult(task_id, domain, winner, h0, candidates, adopted, ledger.summary())

    def _propose_round(
        self,
        *,
        task_id: str,
        task_md: str,
        round_idx: int,
        k: int,
        prior_candidates: list[Candidate],
        lib_index: str,
        ref_exp: str,
        task_dir: Path,
        ledger: Ledger,
    ) -> list[Candidate]:
        """Run `k` proposer branches in parallel (when k > 1) or a single sequential branch."""
        surface = self.adapter.substrate.spec
        seed_parent = selection.select_best(prior_candidates)
        prior_tags = [c.tag for c in prior_candidates]

        def _build_one(branch_idx: int) -> tuple[Candidate, int, str]:
            tag = f"r{round_idx}_b{branch_idx}" if k > 1 else f"r{round_idx}_s0"
            c_root = task_dir / "candidates" / tag
            hint = _BRANCH_HINTS[branch_idx % len(_BRANCH_HINTS)] if k > 1 else ""
            editable = stage_context_dir(
                c_root,
                task_md=task_md,
                seed_tree=HarnessTree(seed_parent.tree_dir, surface),
                prior_candidates=prior_candidates,
                library_index=lib_index,
                reference_experience=ref_exp,
            )
            prompt = prompts.compose_round(
                surface,
                round_idx=round_idx,
                candidate_tag=tag,
                prior_tags=prior_tags,
                branch_hint=hint,
            )
            res = self.proposer.run(c_root, prompt)
            p_tokens = res.tokens_used

            tree = HarnessTree(editable, surface)
            report = self._validate_candidate_tree(tree)
            if not report.ok:
                repair_prompt = (
                    prompt
                    + "\n\n### VALIDATION ERRORS TO FIX IMMEDIATELY\n"
                    + "\n".join(f"- {e}" for e in report.errors)
                )
                rep = self.proposer.run(c_root, repair_prompt)
                p_tokens += rep.tokens_used
                report = self._validate_candidate_tree(tree)

            final_tree_dir = c_root / "harness"
            tree.clone_to(final_tree_dir)
            chosen_parents = [p for p in res.selected_parents if p in prior_tags]
            if not chosen_parents:
                chosen_parents = [seed_parent.tag]
            cand = Candidate(
                tag=tag,
                round_idx=round_idx,
                tree_dir=final_tree_dir,
                parent_tags=chosen_parents,
                notes=res.notes,
                proposer_tokens=p_tokens,
                validation_errors=report.errors,
            )
            return cand, p_tokens, f"propose {tag} (parents={chosen_parents})"

        built: list[tuple[Candidate, int, str]] = []
        if k == 1:
            built.append(_build_one(0))
        else:
            with ThreadPoolExecutor(max_workers=k) as pool:
                futs = [pool.submit(_build_one, i) for i in range(k)]
                for f in as_completed(futs):
                    built.append(f.result())
            built.sort(key=lambda item: item[0].tag)

        out: list[Candidate] = []
        for cand, p_tokens, note in built:
            ledger.charge_p(p_tokens, tag=cand.tag, note=note)
            if ledger.can_rollout():
                self._evaluate(task_id, cand, task_dir, ledger)
            out.append(cand)
        return out

    def _validate_candidate_tree(self, tree: HarnessTree):
        validate_fn = getattr(self.adapter, "validate_tree", None)
        if callable(validate_fn):
            return validate_fn(tree)
        return validate(tree)

    def _evaluate(self, task_id: str, cand: Candidate, task_dir: Path, ledger: Ledger) -> None:
        res = self.adapter.execute(task_id=task_id, harness_dir=cand.tree_dir, run_id=cand.tag)
        cand.passed = res.passed
        cand.score = res.score
        cand.rollout_tokens = res.rollout_tokens
        cand.log_dir = res.log_dir
        if res.log_dir:
            res.log_dir.mkdir(parents=True, exist_ok=True)
            (res.log_dir / "summary.txt").write_text(res.summary or "", encoding="utf-8")
        ledger.charge_r(
            res.rollout_tokens,
            tag=cand.tag,
            note=f"rollout score={res.score}",
        )

    @staticmethod
    def _write_manifest(
        task_dir: Path,
        task_id: str,
        domain: str,
        winner: Candidate,
        candidates: list[Candidate],
        adopted: Path | None,
        ledger: Ledger,
    ) -> None:
        def _ser(c: Candidate) -> dict:
            d = asdict(c)
            d["tree_dir"] = str(c.tree_dir)
            d["log_dir"] = str(c.log_dir) if c.log_dir else None
            return d

        manifest = {
            "task_id": task_id,
            "domain": domain,
            "winner": _ser(winner),
            "adopted_path": str(adopted) if adopted else None,
            "ledger": ledger.summary(),
            "candidates": [_ser(c) for c in candidates],
        }
        (task_dir / "recursion_manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )


def run_parallel(
    adapter: BenchmarkAdapter,
    proposer: Proposer,
    library: Library,
    work_root: Path,
    task_id: str,
    *,
    k: int = 1,
    budget: BudgetSpec | None = None,
) -> RecursionResult:
    """Run 1 round of parallel exploration with `k` branches."""
    cfg = RecursionConfig(
        budget=budget or BudgetSpec(),
        max_rounds=1,
        parallel_branches=max(1, k),
        adopt_into_library=True,
    )
    engine = RecursionEngine(adapter, proposer, library, work_root, cfg)
    return engine.run_task(task_id)


def run_sequential(
    adapter: BenchmarkAdapter,
    proposer: Proposer,
    library: Library,
    work_root: Path,
    task_id: str,
    *,
    k: int = 1,
    budget: BudgetSpec | None = None,
) -> RecursionResult:
    """Run `k` sequential refinement rounds (1 branch per round)."""
    cfg = RecursionConfig(
        budget=budget or BudgetSpec(),
        max_rounds=max(1, k),
        parallel_branches=1,
        adopt_into_library=True,
    )
    engine = RecursionEngine(adapter, proposer, library, work_root, cfg)
    return engine.run_task(task_id)


def trivial_selection(candidates: list[Candidate]) -> Candidate:
    """Select the highest-scoring candidate from a pool (early exit if h0 is trivial)."""
    if candidates and selection.is_trivial_at_h0(candidates[0]):
        return candidates[0]
    return selection.select_best(candidates)


def reflect_adoption(
    library: Library,
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
    """Run Library reflection and adopt `winner` into the domain library."""
    return library.adopt(
        domain=domain,
        task_id=task_id,
        task_md=task_md,
        winner=winner,
        all_candidates=all_candidates,
        proposer=proposer,
        ledger=ledger,
        work_dir=work_dir,
    )
