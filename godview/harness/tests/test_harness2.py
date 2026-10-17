# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Comprehensive unit and scenario tests for `godview/harness` (Harness² × God-View v4.0)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from godview.harness import (
    GODVIEW_SURFACE,
    CloudSyncedLibrary,
    GodViewCourtAdapter,
    GodViewSubstrate,
    HookRunner,
    ScriptValidationError,
    VertexGeminiProposer,
    validate_script_ast,
)
from godview.harness.core import (
    BudgetExhausted,
    BudgetLedger,
    BudgetSpec,
    HarnessTree,
    autotrigger,
    reflect_adoption,
    render_tree,
    run_parallel,
    run_sequential,
    trivial_selection,
    validate,
)


def test_budget_ledger_enforces_rollout_and_proposer_ceilings() -> None:
    spec = BudgetSpec(R=4, P=3, always_on_max=12_000)  # 4,000 R tokens, 3,000 P tokens
    ledger = BudgetLedger(spec=spec, strict=True)
    assert ledger.r_limit == 4_000
    assert ledger.p_limit == 3_000

    ledger.charge_r(2_500, tag="h0", note="initial rollout")
    ledger.charge_p(1_500, tag="r1_b0", note="round 1 propose")
    assert ledger.r_remaining == 1_500
    assert ledger.p_remaining == 1_500

    with pytest.raises(BudgetExhausted, match="Rollout budget R exhausted"):
        ledger.charge_r(2_000, tag="r1_b0", note="overflow R")

    with pytest.raises(BudgetExhausted, match="Proposer budget P exhausted"):
        ledger.charge_p(2_000, tag="r1_b1", note="overflow P")


def test_all_five_seed_base_trees_validate_cleanly() -> None:
    adapter = GodViewCourtAdapter()
    for domain in ("application", "data", "business_line", "change", "platform"):
        substrate = GodViewSubstrate(domain=domain)
        base_dir = substrate.base_tree()
        assert base_dir.is_dir(), f"Missing base tree directory for {domain}"
        tree = HarnessTree(base_dir, GODVIEW_SURFACE)
        report = adapter.validate_tree(tree)
        assert report.ok, f"Base tree {domain} failed validation: {report.errors}"
        assert 0 < report.always_on_tokens <= 12_000
        rendered = render_tree(tree)
        assert "Harness surface: godview" in rendered
        for sp in tree.skills():
            trig = autotrigger(sp.read_text(encoding="utf-8"))
            assert trig is not None and len(trig) > 5


def test_validate_rejects_oversized_always_on_and_missing_autotrigger(
    tmp_path: Path,
) -> None:
    substrate = GodViewSubstrate(domain="application")
    cloned = HarnessTree(substrate.base_tree(), GODVIEW_SURFACE).clone_to(tmp_path / "bad_tree")
    # 1. Exceed 12,000 token ceiling on c1..c4
    (cloned.root / "systemprompt.md").write_text("X" * 52_000, encoding="utf-8")
    # 2. Remove autotrigger from skill
    bad_skill = cloned.root / "skills" / "broken-skill" / "SKILL.md"
    bad_skill.parent.mkdir(parents=True, exist_ok=True)
    bad_skill.write_text("# Broken skill without autotrigger\n", encoding="utf-8")
    # 3. Reference a non-existent script in systemprompt
    (cloned.root / "guardrails.md").write_text(
        "Always run `python3 scripts/missing_script.py`.\n", encoding="utf-8"
    )

    report = validate(cloned)
    assert not report.ok
    joined = "\n".join(report.errors)
    assert "Always-on Context exceeds ceiling" in joined
    assert "missing a non-empty `autotrigger:`" in joined
    assert "missing_script.py" in joined


def test_validate_script_ast_sandbox_rejects_escapes_and_allows_math(
    tmp_path: Path,
) -> None:
    safe_src = (
        "import json, math, sys\n"
        "data = json.loads(sys.stdin.read() or '{}')\n"
        "print(json.dumps({'ok': True, 'val': math.sqrt(16)}))\n"
    )
    assert validate_script_ast(tmp_path / "safe.py", safe_src) == []

    unsafe_samples = [
        "import subprocess\nsubprocess.run(['ls'])\n",
        "import os\nos.system('whoami')\n",
        "eval('1 + 1')\n",
        "exec('x = 1')\n",
        "import urllib.request\n",
        "x = ().__class__.__base__.__subclasses__()\n",
    ]
    for idx, bad_src in enumerate(unsafe_samples):
        errs = validate_script_ast(tmp_path / f"bad_{idx}.py", bad_src)
        assert errs, f"Expected AST sandbox error for sample {idx}: {bad_src}"
        with pytest.raises(ScriptValidationError):
            validate_script_ast(tmp_path / f"bad_{idx}.py", bad_src, raise_on_error=True)


def test_hook_runner_executes_pre_post_and_stop_hooks() -> None:
    substrate = GodViewSubstrate(domain="application")
    tree = HarnessTree(substrate.base_tree(), GODVIEW_SURFACE)
    runner = HookRunner(tree)

    pre_res = runner.run_pre_tool_use(
        "logging.query",
        {"task_id": "inc-001"},
        context={"total_requests": 200, "error_count": 20, "error_threshold": 0.05},
    )
    assert len(pre_res) == 1
    assert pre_res[0].passed
    assert pre_res[0].parsed_output["error_rate"] == 0.1
    assert pre_res[0].parsed_output["breached"] is True

    post_res = runner.run_post_tool_use(
        "logging.query",
        {"task_id": "inc-001"},
        {"status": "ok"},
        context={"total_requests": 100, "error_count": 2, "error_threshold": 0.05},
    )
    assert len(post_res) == 2
    assert all(r.passed for r in post_res)

    stop_res = runner.run_stop_hooks(
        {"verdict": "ready"},
        context={"expected_fields": ["id", "amount"], "actual_fields": ["id", "amount"]},
    )
    assert len(stop_res) == 2
    assert all(r.passed for r in stop_res)


def test_end_to_end_recursion_parallel_sequential_and_cloud_library_sync(
    tmp_path: Path,
) -> None:
    adapter = GodViewCourtAdapter(default_domain="application")
    adapter.register_scenario(
        "incident_evolve_schema",
        {
            "domain": "application",
            "description": "Inbound request payload schema drift requiring updated validation.",
            "required_script": "check_payload_schema.py",
            "require_adaptation_on_h0": True,
        },
    )
    proposer = VertexGeminiProposer()
    assert proposer.resolve_active_model() == "gemini-4-argon"

    lib = CloudSyncedLibrary(root=tmp_path / "library")
    res_par = run_parallel(
        adapter=adapter,
        proposer=proposer,
        library=lib,
        work_root=tmp_path / "runs_par",
        task_id="incident_evolve_schema",
        k=1,
    )
    assert res_par.h0.score == 0.55
    assert res_par.winner.passed is True
    assert res_par.winner.score >= 0.85
    assert res_par.adopted_path is not None
    assert (res_par.adopted_path / "EXPERIENCE.md").is_file()
    assert (lib.domain_dir("application") / "INDEX.md").is_file()
    assert any("INDEX.md" in key for key in lib.synced_objects)
    assert any("EXPERIENCE.md" in key for key in lib.synced_objects)

    # Sequential refinement + trivial selection
    res_seq = run_sequential(
        adapter=adapter,
        proposer=proposer,
        library=lib,
        work_root=tmp_path / "runs_seq",
        task_id="incident_distribution_shift",
        k=1,
    )
    chosen = trivial_selection(res_seq.candidates)
    assert chosen.passed is True
    assert chosen.score >= 0.85

    # Explicit reflect_adoption call
    ledger = BudgetLedger()
    adopted_dir = reflect_adoption(
        lib,
        domain="data",
        task_id="incident_distribution_shift",
        task_md=adapter.task_description("incident_distribution_shift"),
        winner=chosen,
        all_candidates=res_seq.candidates,
        proposer=proposer,
        ledger=ledger,
        work_dir=tmp_path / "reflect_work",
    )
    assert (adopted_dir / "EXPERIENCE.md").is_file()
    summary = json.loads((res_par.winner.log_dir / "summary.txt").read_text(encoding="utf-8"))
    assert summary["shadow_tier2_blocked"] is True
    assert summary["model_id"] == "gemini-4-argon"
