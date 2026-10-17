# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""GodViewCourtAdapter: BenchmarkAdapter bridging Harness² to God-View Court Agents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from godview_shared.capabilities import get_capability
from godview_shared.config import DEFAULT_AGENT_MODEL_ID
from godview_shared.fakes import InMemoryFirestore, InMemoryPubSub

from godview.harness.core.spec import BenchmarkAdapter, ExecutionResult
from godview.harness.core.tree import (
    HarnessTree,
    ValidationReport,
    autotrigger,
    estimate_tokens,
    validate,
)
from godview.harness.substrate import (
    GODVIEW_SURFACE,
    GodViewSubstrate,
    HookRunner,
    validate_script_ast,
)
from godview.invariants.skills import SkillLoader, SkillValidationError
from godview.sovereign.trust_ladder import TrustGate

COURT_DOMAINS: tuple[str, ...] = (
    "application",
    "data",
    "business_line",
    "change",
    "platform",
)

# Canonical read-only capabilities covering the Six Senses in `CAPABILITY_REGISTRY`
SIX_SENSES_CAPABILITIES: tuple[str, ...] = (
    "obs.query_logs_scoped",
    "obs.list_timeseries",
    "obs.get_trace",
    "k8s.get_resource",
    "sql.list_tables",
    "git.list_commits",
)


class GodViewCourtAdapter(BenchmarkAdapter):
    """Runs Court Agent rollouts over the Six Senses + CapabilityRegistry + HookRunner + TrustGate."""

    name: str = "godview"

    def __init__(
        self,
        *,
        default_domain: str = "application",
        substrate: GodViewSubstrate | None = None,
        scenarios: dict[str, dict[str, Any]] | None = None,
        trust_gate: TrustGate | None = None,
        model_id: str = DEFAULT_AGENT_MODEL_ID,
    ) -> None:
        self.default_domain = default_domain if default_domain in COURT_DOMAINS else "application"
        self.substrate = substrate or GodViewSubstrate(domain=self.default_domain)
        self.scenarios: dict[str, dict[str, Any]] = dict(scenarios or {})
        self.trust_gate = trust_gate or TrustGate(
            firestore=InMemoryFirestore(),
            pubsub=InMemoryPubSub(),
        )
        self.model_id = model_id

    def register_scenario(self, task_id: str, scenario: dict[str, Any]) -> None:
        """Register or override an incident/task scenario payload for `task_id`."""
        self.scenarios[task_id] = dict(scenario)

    def domain_of(self, task_id: str) -> str:
        """Map `task_id` or incident identifier to one of the 5 Court Agent domains."""
        if task_id in self.scenarios and "domain" in self.scenarios[task_id]:
            dom = str(self.scenarios[task_id]["domain"])
            if dom in COURT_DOMAINS:
                return dom

        lower = task_id.lower()
        for dom in COURT_DOMAINS:
            if lower.startswith(f"{dom}:") or lower.startswith(f"{dom}__") or f"_{dom}_" in lower:
                return dom

        if any(
            k in lower
            for k in (
                "cusum",
                "drift",
                "schema",
                "bq",
                "sql",
                "cibil_score",
                "offset",
                "segment",
                "900",
            )
        ):
            if "offset" in lower or "segment" in lower or "parser" in lower:
                return "application"
            return "data"
        if any(k in lower for k in ("saga", "dlq", "vehicle_loan_origination", "funnel", "sla")):
            return "business_line"
        if any(k in lower for k in ("commit", "pr", "deploy", "git", "regression", "change")):
            return "change"
        if any(k in lower for k in ("oom", "pod", "gke", "node", "quota", "platform", "memory")):
            return "platform"
        return self.default_domain

    def task_description(self, task_id: str) -> str:
        """Return the incident/task problem statement shown to the proposer."""
        if task_id in self.scenarios and "description" in self.scenarios[task_id]:
            return str(self.scenarios[task_id]["description"])

        lower = task_id.lower()
        if any(
            k in lower
            for k in (
                "scenario1",
                "scenario_1",
                "offset",
                "segment",
                "900",
                "cibil_score",
            )
        ):
            return (
                f"Incident `{task_id}`: Fixed-width bureau response parser shifted by +120 bytes "
                "after upstream record expansion, causing `cibil_score` extraction to read filler "
                "bytes and trigger silent `cibil_stub_900` fallback across `loan_applications`. "
                "Engineer the harness so the Court Agent deterministically verifies byte offsets "
                "via `scripts/check_cibil_score_offsets.py` and attributes the 120-byte shift."
            )
        if any(k in lower for k in ("scenario2", "scenario_2", "cusum", "drift", "armor")):
            return (
                f"Incident `{task_id}`: Unpinned model routing (`gemini-3.8-flash` vs `gemini-4-argon`) "
                "and disabled Model Armor template caused two-sided CUSUM prompt drift > 4.0 and "
                "`step_order_insurance_before_kyc` sequence violations in `underwriting_audits`. "
                "Engineer the harness so the Court Agent runs `scripts/check_prompt_drift_cusum.py` "
                "and verifies CUSUM drift + Model Armor enforcement."
            )
        if any(k in lower for k in ("scenario3", "scenario_3", "oom", "dlq", "pdf", "saga")):
            return (
                f"Incident `{task_id}`: High-resolution PDF attachment surge caused GKE pod "
                "OOMKilled (exit code 137) and stalled the `vehicle_loan_origination` saga with "
                "unacknowledged messages accumulating in the Pub/Sub DLQ (`insurance_no_policy_write`). "
                "Engineer the harness so the Court Agent runs `scripts/check_dlq_saga_stall.py` "
                "and correlates container exit 137 with the DLQ backlog."
            )
        return (
            f"Incident `{task_id}` in domain `{self.domain_of(task_id)}`: Diagnose root cause "
            "across the Six Senses (`obs.query_logs_scoped`, `obs.list_timeseries`, "
            "`obs.get_trace`, `k8s.get_resource`, `sql.list_tables`, `git.list_commits`) "
            "and verify invariants with deterministic c7 scripts."
        )

    def validate_tree(self, tree: HarnessTree) -> ValidationReport:
        """Chain `harness2.core.tree.validate` + `validate_script_ast` + `SkillLoader.parse_markdown`."""
        base_report = validate(tree, require_autotrigger=True)
        errors = list(base_report.errors)

        # AST sandbox validation for every Python script in `scripts/`
        for script_path in tree.scripts():
            if script_path.suffix == ".py":
                ast_errs = validate_script_ast(script_path)
                errors.extend(ast_errs)

        # Validate every SKILL.md against `SkillLoader.parse_markdown` when frontmatter includes `id:`
        skill_loader = SkillLoader()
        for skill_path in tree.skills():
            raw = skill_path.read_text(encoding="utf-8")
            rel = skill_path.relative_to(tree.root)
            if autotrigger(raw) is None:
                msg = f"skill {rel} is missing a non-empty `autotrigger:` declaration"
                if msg not in errors:
                    errors.append(msg)
            header_slice = "\n".join(raw.splitlines()[:30]).lower()
            if "owner_agent_id:" in header_slice and "required_capabilities:" in header_slice:
                try:
                    skill_loader.parse_markdown(raw)
                except SkillValidationError as exc:
                    errors.append(f"skill {rel} failed SkillLoader schema check: {exc}")

        return ValidationReport(
            ok=not errors,
            always_on_tokens=base_report.always_on_tokens,
            errors=errors,
        )

    def execute(self, task_id: str, harness_dir: Path, run_id: str) -> ExecutionResult:
        """Install `harness_dir` onto the Court Agent substrate, run the 6-Senses rollout, and score."""
        tree = HarnessTree(harness_dir, GODVIEW_SURFACE)
        val_report = self.validate_tree(tree)
        log_dir = harness_dir.parent / "logs" / run_id
        log_dir.mkdir(parents=True, exist_ok=True)

        if not val_report.ok:
            summary = "Validation failed before rollout:\n" + "\n".join(
                f"- {e}" for e in val_report.errors
            )
            (log_dir / "summary.txt").write_text(summary, encoding="utf-8")
            return ExecutionResult(
                passed=False,
                score=0.0,
                rollout_tokens=max(1, val_report.always_on_tokens),
                log_dir=log_dir,
                summary=summary,
            )

        domain = self.domain_of(task_id)
        scenario_ctx = self._build_scenario_context(task_id, domain)
        hook_runner = HookRunner(tree)
        agent_id = f"{domain}__{run_id}"

        trace_events: list[dict[str, Any]] = []
        hook_results_all = []
        for cap_id in SIX_SENSES_CAPABILITIES:
            spec = get_capability(cap_id)
            decision = self.trust_gate.authorize(
                agent_id=agent_id,
                capability_id=cap_id,
                target_resource=task_id,
            )
            if not decision.authorized or spec.mutating:
                continue

            tool_input = {"task_id": task_id, "domain": domain, "capability": cap_id}
            pre_hooks = hook_runner.run_pre_tool_use(cap_id, tool_input, context=scenario_ctx)
            hook_results_all.extend(pre_hooks)

            tool_output = {
                "capability": cap_id,
                "status": "ok",
                "telemetry": scenario_ctx,
            }
            post_hooks = hook_runner.run_post_tool_use(
                cap_id, tool_input, tool_output, context=scenario_ctx
            )
            hook_results_all.extend(post_hooks)
            trace_events.append(
                {
                    "capability": cap_id,
                    "authorized": decision.authorized,
                    "pre_hooks": len(pre_hooks),
                    "post_hooks": len(post_hooks),
                }
            )

        # Verify TrustGate shadow/probe boundary: candidate rollouts cannot execute L3 mutating actions unapproved
        mutating_decision = self.trust_gate.authorize(
            agent_id=agent_id,
            capability_id="k8s.rollout_restart",
            target_resource=task_id,
            novel_skill_first_step=True,
        )
        shadow_blocked = not mutating_decision.authorized

        # Match skills whose `autotrigger:` aligns with the task/scenario
        task_text = (self.task_description(task_id) + " " + json.dumps(scenario_ctx)).lower()
        triggered_skills: list[str] = []
        for sp in tree.skills():
            body = sp.read_text(encoding="utf-8")
            trig = autotrigger(body) or ""
            trig_words = [w.lower() for w in trig.split() if len(w) >= 4]
            if (
                any(w in task_text for w in trig_words)
                or sp.parent.name.replace("-", " ") in task_text
            ):
                triggered_skills.append(sp.parent.name)

        stop_hooks = hook_runner.run_stop_hooks(
            final_output={"task_id": task_id, "domain": domain, "model": self.model_id},
            context=scenario_ctx,
        )
        hook_results_all.extend(stop_hooks)

        required_script = scenario_ctx.get("required_script", "")
        script_names = {p.name for p in tree.scripts()}
        has_required_script = (
            required_script in script_names if required_script else bool(script_names)
        )
        stop_hooks_ran = len(stop_hooks) > 0 and all(h.passed for h in stop_hooks)
        post_hooks_ran = any(h.event == "PostToolUse" and h.passed for h in hook_results_all)
        require_adapt = bool(scenario_ctx.get("require_adaptation_on_h0", False))

        score = 0.35
        if triggered_skills:
            score += 0.20
        if has_required_script:
            score += 0.20
        if stop_hooks_ran and post_hooks_ran:
            score += 0.25

        if run_id == "h0" and require_adapt:
            score = min(score, 0.55)

        score = round(min(1.0, score), 3)
        passed = score >= 0.85 and shadow_blocked

        sys_inst = self.substrate.compile_system_instruction(tree)
        rollout_tokens = max(
            120,
            estimate_tokens(sys_inst) + len(trace_events) * 60 + len(hook_results_all) * 40,
        )

        summary_obj = {
            "task_id": task_id,
            "run_id": run_id,
            "domain": domain,
            "model_id": self.model_id,
            "passed": passed,
            "score": score,
            "shadow_tier2_blocked": shadow_blocked,
            "triggered_skills": triggered_skills,
            "scripts_present": sorted(script_names),
            "stop_hooks_executed": [
                {"script": h.script_rel, "passed": h.passed, "output": h.parsed_output}
                for h in stop_hooks
            ],
        }
        summary_str = json.dumps(summary_obj, indent=2)
        (log_dir / "summary.txt").write_text(summary_str, encoding="utf-8")
        (log_dir / "trace.json").write_text(json.dumps(trace_events, indent=2), encoding="utf-8")

        return ExecutionResult(
            passed=passed,
            score=score,
            rollout_tokens=rollout_tokens,
            log_dir=log_dir,
            summary=summary_str,
        )

    def _build_scenario_context(self, task_id: str, domain: str) -> dict[str, Any]:
        if task_id in self.scenarios:
            return dict(self.scenarios[task_id])

        lower = task_id.lower()
        if any(
            k in lower
            for k in (
                "scenario1",
                "scenario_1",
                "offset",
                "segment",
                "900",
                "cibil_score",
            )
        ):
            return {
                "scenario": "scenario_1_bureau_offset_shift",
                "domain": domain,
                "expected_score_offset": 108,
                "actual_score_offset": 228,
                "cibil_score": 900,
                "invariant_id": "cibil_stub_900",
                "required_script": "check_cibil_score_offsets.py",
                "require_adaptation_on_h0": "adapt" in lower or "evolve" in lower,
            }
        if any(k in lower for k in ("scenario2", "scenario_2", "cusum", "drift", "armor")):
            return {
                "scenario": "scenario_2_prompt_drift_cusum",
                "domain": domain,
                "cusum_score": 5.4,
                "cusum_threshold": 4.0,
                "model_armor_enabled": True,
                "invariant_id": "step_order_insurance_before_kyc",
                "required_script": "check_prompt_drift_cusum.py",
                "require_adaptation_on_h0": "adapt" in lower or "evolve" in lower,
            }
        if any(k in lower for k in ("scenario3", "scenario_3", "oom", "dlq", "pdf", "saga")):
            return {
                "scenario": "scenario_3_pdf_oom_dlq_stall",
                "domain": domain,
                "container_exit_code": 137,
                "dlq_backlog_depth": 42,
                "invariant_id": "vehicle_loan_origination",
                "required_script": "check_dlq_saga_stall.py",
                "require_adaptation_on_h0": "adapt" in lower or "evolve" in lower,
            }
        return {
            "scenario": "generic_incident",
            "domain": domain,
            "expected_score_offset": 108,
            "actual_score_offset": 120,
            "cibil_score": 900,
            "cusum_score": 5.4,
            "cusum_threshold": 4.0,
            "container_exit_code": 137,
            "dlq_backlog_depth": 42,
            "require_adaptation_on_h0": "adapt" in lower or "evolve" in lower,
        }
