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
"""CourtAgent and CourtSwarmSession powered by the Harness² capability tree."""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from godview_shared.config import (
    DEFAULT_AGENT_FALLBACK_MODEL_ID,
    DEFAULT_AGENT_MODEL_ID,
    DEFAULT_AGENT_MODEL_LOCATION,
    validate_model_location,
)
from godview_shared.fakes import InMemoryFirestore, InMemoryPubSub

from godview.agents.archetypes import (
    COURT_DOMAINS,
    ArchetypeSpec,
    get_archetype,
    resolve_domain,
)
from godview.harness.adapter import GodViewCourtAdapter
from godview.harness.cloud_library import CloudSyncedLibrary
from godview.harness.core import (
    BudgetSpec,
    HarnessTree,
    RecursionResult,
    autotrigger,
    estimate_tokens,
    run_parallel,
    run_sequential,
)
from godview.harness.substrate import GODVIEW_SURFACE, GodViewSubstrate, HookRunner
from godview.harness.vertex_proposer import VertexGeminiProposer
from godview.sovereign.court_manager.token_governor import TokenBudgetGovernor
from godview.sovereign.trust_ladder import TrustGate

CONSENSUS_THRESHOLD: float = 0.85
MAX_SWARM_ROUNDS: int = 4
MAX_SWARM_DURATION_S: float = 90.0


@dataclass
class CourtHypothesis:
    """Structured diagnostic hypothesis produced by a Court Specialist Agent."""

    agent_id: str
    domain: str
    incident_id: str
    hypothesis: str
    root_cause_category: str
    confidence: float
    evidence_citations: list[str]
    recommended_capability: str
    script_verifications: list[dict[str, Any]] = field(default_factory=list)
    triggered_skills: list[str] = field(default_factory=list)
    model_used: str = DEFAULT_AGENT_MODEL_ID
    location: str = DEFAULT_AGENT_MODEL_LOCATION
    tokens_used: int = 0


@dataclass
class SwarmConsensusVerdict:
    """Final consensus verdict produced by a CourtSwarmSession."""

    incident_id: str
    consensus_reached: bool
    consensus_score: float
    rounds_completed: int
    duration_s: float
    winning_hypothesis: CourtHypothesis
    all_hypotheses: list[CourtHypothesis]
    escalated_to_argon: bool
    harness_run_summary: dict[str, Any] = field(default_factory=dict)


class CourtAgent:
    """Single Court Specialist Agent operating over a Harness² (c1..c8) capability tree."""

    def __init__(
        self,
        agent_id: str,
        *,
        domain: str | None = None,
        tree_dir: Path | None = None,
        governor: TokenBudgetGovernor | None = None,
        trust_gate: TrustGate | None = None,
        model_id: str = DEFAULT_AGENT_MODEL_ID,
        fallback_model_id: str = DEFAULT_AGENT_FALLBACK_MODEL_ID,
        location: str = DEFAULT_AGENT_MODEL_LOCATION,
    ) -> None:
        validate_model_location(model_id, location)
        validate_model_location(fallback_model_id, location)
        self.agent_id = agent_id
        self.domain = domain or resolve_domain(agent_id)
        self.archetype: ArchetypeSpec = get_archetype(self.domain)
        self.substrate = GodViewSubstrate(
            domain=self.domain,
            base_tree_dir=tree_dir or self.archetype.base_tree_dir,
        )
        self.tree = HarnessTree(self.substrate.base_tree(), GODVIEW_SURFACE)
        self.default_model_id = model_id
        self.fallback_model_id = fallback_model_id
        self.location = location

        fs = InMemoryFirestore()
        ps = InMemoryPubSub()
        self.governor = governor or TokenBudgetGovernor(firestore=fs, pubsub=ps)
        self.trust_gate = trust_gate or TrustGate(
            firestore=fs, pubsub=ps, token_governor=self.governor
        )
        self.last_recursion_result: RecursionResult | None = None

    def resolve_active_model(self, *, quota_or_429_fallback: bool = False) -> str:
        """Return `gemini-4-argon` @ `global` by default, or `gemini-3.8-flash` on 429/budget fallback."""
        model_id, loc = self.governor.resolve_agent_model(
            self.agent_id, quota_or_429_fallback=quota_or_429_fallback
        )
        validate_model_location(model_id, loc)
        return model_id

    def inspect_harness(self) -> dict[str, Any]:
        """Return a structured inspection snapshot of this agent's active HarnessTree (`c1..c8`)."""
        adapter = GodViewCourtAdapter(
            default_domain=self.domain,
            substrate=self.substrate,
            trust_gate=self.trust_gate,
            model_id=self.resolve_active_model(),
        )
        report = adapter.validate_tree(self.tree)
        ao_paths = self.tree.always_on_paths()
        always_on_info = {
            cat: {
                "path": p.relative_to(self.tree.root).as_posix(),
                "tokens": estimate_tokens(p.read_text(encoding="utf-8")) if p.is_file() else 0,
            }
            for cat, p in ao_paths.items()
        }
        skills_info = []
        for sp in self.tree.skills():
            raw = sp.read_text(encoding="utf-8")
            skills_info.append(
                {
                    "slug": sp.parent.name,
                    "path": sp.relative_to(self.tree.root).as_posix(),
                    "tokens": estimate_tokens(raw),
                    "autotrigger": autotrigger(raw),
                }
            )
        scripts_info = [
            {
                "name": sc.name,
                "path": sc.relative_to(self.tree.root).as_posix(),
                "lines": len(sc.read_text(encoding="utf-8").splitlines()),
            }
            for sc in self.tree.scripts()
        ]
        sub_agents_info = [
            {
                "name": sa.parent.name,
                "path": sa.relative_to(self.tree.root).as_posix(),
            }
            for sa in self.tree.sub_agents()
        ]
        hp = self.tree.hooks_path()
        hooks_dict = self.tree.hooks()
        hooks_info = {
            "path": hp.relative_to(self.tree.root).as_posix(),
            "configured": bool(hooks_dict.get("hooks", hooks_dict)),
            "events": list(hooks_dict.get("hooks", hooks_dict).keys()),
        }
        categories = {
            "c1_identity": always_on_info.get("identity", {}).get("path", ""),
            "c2_tool_descriptions": always_on_info.get("tool_descriptions", {}).get("path", ""),
            "c3_guardrails": always_on_info.get("guardrails", {}).get("path", ""),
            "c4_long_term_memory": always_on_info.get("memory", {}).get("path", ""),
            "c5_sub_agents": [s["name"] for s in sub_agents_info],
            "c6_skills": [s["slug"] for s in skills_info],
            "c7_scripts": [s["name"] for s in scripts_info],
            "c8_hooks": hooks_info,
        }
        return {
            "surface": "godview",
            "model_id": self.resolve_active_model(),
            "agent_id": self.agent_id,
            "domain": self.domain,
            "active_model_id": self.resolve_active_model(),
            "always_on_tokens": report.always_on_tokens,
            "validation": {
                "ok": report.ok,
                "always_on_tokens": report.always_on_tokens,
                "errors": report.errors,
            },
            "categories": categories,
            "c1_to_c4_always_on": always_on_info,
            "c5_sub_agents": sub_agents_info,
            "c6_skills": skills_info,
            "c7_scripts": scripts_info,
            "c8_hooks": hooks_info,
            "tree_root": str(self.tree.root),
        }

    def diagnose_incident(
        self,
        incident_id: str,
        *,
        telemetry: dict[str, Any] | None = None,
        quota_or_429_fallback: bool = False,
        force_low_confidence: bool = False,
    ) -> CourtHypothesis:
        """Execute a diagnostic pass over evidence and c7/c8 hooks."""
        active_model = self.resolve_active_model(quota_or_429_fallback=quota_or_429_fallback)
        ctx = dict(telemetry or {})

        runner = HookRunner(self.tree)
        script_verifications: list[dict[str, Any]] = []

        for cap_id in self.archetype.primary_capabilities:
            decision = self.trust_gate.authorize(
                agent_id=self.agent_id,
                capability_id=cap_id,
                target_resource=incident_id,
            )
            if not decision.authorized:
                continue
            pre_hooks = runner.run_pre_tool_use(cap_id, {"incident_id": incident_id}, context=ctx)
            post_hooks = runner.run_post_tool_use(
                cap_id,
                {"incident_id": incident_id},
                {"status": "ok", "capability": cap_id},
                context=ctx,
            )
            for h in pre_hooks + post_hooks:
                if h.parsed_output and h.parsed_output not in script_verifications:
                    script_verifications.append(h.parsed_output)

        stop_hooks = runner.run_stop_hooks(
            {"incident_id": incident_id, "agent_id": self.agent_id},
            context=ctx,
        )
        for h in stop_hooks:
            if h.parsed_output and h.parsed_output not in script_verifications:
                script_verifications.append(h.parsed_output)

        triggered_skills = [sp.parent.name for sp in self.tree.skills()]
        sys_inst = self.substrate.compile_system_instruction(self.tree)
        tokens_used = max(
            150,
            estimate_tokens(sys_inst)
            + len(self.archetype.primary_capabilities) * 50
            + len(stop_hooks) * 40,
        )
        self._meter_usage(tokens_used, active_model)

        # Without live LLM generation, return zero confidence and never fabricate a story
        return CourtHypothesis(
            agent_id=self.agent_id,
            domain=self.domain,
            incident_id=incident_id,
            hypothesis="not diagnosed: model unavailable",
            root_cause_category="model_unavailable",
            confidence=0.0,
            evidence_citations=[f"incident:{incident_id}"],
            recommended_capability="",
            script_verifications=script_verifications,
            triggered_skills=triggered_skills,
            model_used=active_model,
            location=self.location,
            tokens_used=tokens_used,
        )

    def evolve_harness(
        self,
        task_id: str,
        *,
        work_root: Path,
        library: CloudSyncedLibrary | None = None,
        scenarios: dict[str, dict[str, Any]] | None = None,
        k: int = 1,
        mode: str = "parallel",
        budget: BudgetSpec | None = None,
    ) -> RecursionResult:
        """Run the Harness² recursion loop for `task_id` and adopt the winning tree."""
        adapter = GodViewCourtAdapter(
            default_domain=self.domain,
            substrate=self.substrate,
            scenarios=scenarios,
            trust_gate=self.trust_gate,
            model_id=self.resolve_active_model(),
        )
        proposer = VertexGeminiProposer(
            model=self.default_model_id,
            fallback_model=self.fallback_model_id,
            location=self.location,
            governor=self.governor,
            agent_id=self.agent_id,
        )
        lib = library or CloudSyncedLibrary(root=work_root / "library")
        if mode == "sequential":
            result = run_sequential(
                adapter=adapter,
                proposer=proposer,
                library=lib,
                work_root=work_root,
                task_id=task_id,
                k=k,
                budget=budget,
            )
        else:
            result = run_parallel(
                adapter=adapter,
                proposer=proposer,
                library=lib,
                work_root=work_root,
                task_id=task_id,
                k=k,
                budget=budget,
            )
        self.tree = HarnessTree(result.winner.tree_dir, GODVIEW_SURFACE)
        self.last_recursion_result = result
        return result

    def _meter_usage(self, tokens_used: int, model_used: str) -> None:
        usd_cost = (Decimal(str(tokens_used)) * Decimal("0.000001")).quantize(Decimal("0.000001"))
        coro = self.governor.record_usage(
            agent_id=self.agent_id,
            model_id=model_used,
            location=self.location,
            tokens=tokens_used,
            usd_cost=usd_cost,
        )
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
        except RuntimeError:
            asyncio.run(coro)


class CourtSwarmSession:
    """Multi-agent Court debate and consensus orchestrator (consensus >= 0.85, <= 4 rounds, <= 90 s)."""

    def __init__(
        self,
        agents: list[CourtAgent] | None = None,
        *,
        governor: TokenBudgetGovernor | None = None,
        trust_gate: TrustGate | None = None,
        consensus_threshold: float = CONSENSUS_THRESHOLD,
        max_rounds: int = MAX_SWARM_ROUNDS,
        max_duration_s: float = MAX_SWARM_DURATION_S,
    ) -> None:
        fs = InMemoryFirestore()
        ps = InMemoryPubSub()
        self.governor = governor or TokenBudgetGovernor(firestore=fs, pubsub=ps)
        self.trust_gate = trust_gate or TrustGate(
            firestore=fs, pubsub=ps, token_governor=self.governor
        )
        self.agents = agents or [
            CourtAgent(
                f"{dom}__specialist",
                domain=dom,
                governor=self.governor,
                trust_gate=self.trust_gate,
            )
            for dom in COURT_DOMAINS
        ]
        self.consensus_threshold = consensus_threshold
        self.max_rounds = min(max_rounds, MAX_SWARM_ROUNDS)
        self.max_duration_s = min(max_duration_s, MAX_SWARM_DURATION_S)

    def convene(
        self,
        incident_id: str,
        *,
        telemetry: dict[str, Any] | None = None,
        simulate_low_confidence_until_round_2: bool = False,
    ) -> SwarmConsensusVerdict:
        """Run up to `max_rounds` (<= 4) within `max_duration_s` (<= 90s) until consensus >= 0.85."""
        start_ts = time.monotonic()
        all_hypotheses: list[CourtHypothesis] = []
        escalated_to_argon = False
        rounds_completed = 0

        for round_idx in range(1, self.max_rounds + 1):
            if (time.monotonic() - start_ts) >= self.max_duration_s:
                break
            rounds_completed = round_idx

            force_low = simulate_low_confidence_until_round_2 and round_idx <= 2
            if round_idx == 3 and simulate_low_confidence_until_round_2:
                if self.governor.can_escalate_to_argon("sovereign"):
                    escalated_to_argon = True
                    force_low = False

            round_hyps: list[CourtHypothesis] = []
            for agent in self.agents:
                hyp = agent.diagnose_incident(
                    incident_id,
                    telemetry=telemetry,
                    force_low_confidence=force_low,
                )
                round_hyps.append(hyp)
            all_hypotheses = round_hyps

            best_hyp = max(round_hyps, key=lambda h: h.confidence)
            avg_conf = sum(h.confidence for h in round_hyps) / max(1, len(round_hyps))
            consensus_score = round((best_hyp.confidence * 0.6) + (avg_conf * 0.4), 3)

            if consensus_score >= self.consensus_threshold and not force_low:
                duration = round(min(time.monotonic() - start_ts, self.max_duration_s), 4)
                return SwarmConsensusVerdict(
                    incident_id=incident_id,
                    consensus_reached=True,
                    consensus_score=consensus_score,
                    rounds_completed=rounds_completed,
                    duration_s=duration,
                    winning_hypothesis=best_hyp,
                    all_hypotheses=all_hypotheses,
                    escalated_to_argon=escalated_to_argon
                    or (best_hyp.model_used == "gemini-4-argon"),
                    harness_run_summary={
                        "incident_id": incident_id,
                        "winner_agent_id": best_hyp.agent_id,
                        "winner_domain": best_hyp.domain,
                        "model_used": best_hyp.model_used,
                        "script_verifications": best_hyp.script_verifications,
                        "triggered_skills": best_hyp.triggered_skills,
                        "rounds": rounds_completed,
                    },
                )

        best_hyp = max(all_hypotheses, key=lambda h: h.confidence)
        avg_conf = sum(h.confidence for h in all_hypotheses) / max(1, len(all_hypotheses))
        consensus_score = round((best_hyp.confidence * 0.6) + (avg_conf * 0.4), 3)
        duration = round(min(time.monotonic() - start_ts, self.max_duration_s), 4)
        return SwarmConsensusVerdict(
            incident_id=incident_id,
            consensus_reached=consensus_score >= self.consensus_threshold,
            consensus_score=consensus_score,
            rounds_completed=rounds_completed,
            duration_s=duration,
            winning_hypothesis=best_hyp,
            all_hypotheses=all_hypotheses,
            escalated_to_argon=escalated_to_argon,
            harness_run_summary={
                "incident_id": incident_id,
                "winner_agent_id": best_hyp.agent_id,
                "winner_domain": best_hyp.domain,
                "model_used": best_hyp.model_used,
                "script_verifications": best_hyp.script_verifications,
                "triggered_skills": best_hyp.triggered_skills,
                "rounds": rounds_completed,
            },
        )

    @staticmethod
    def serialize_verdict(verdict: SwarmConsensusVerdict) -> dict[str, Any]:
        return asdict(verdict)
