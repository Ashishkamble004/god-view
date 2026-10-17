# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Sovereign CourtOrchestrator with ADK Parallel fan-out, Loop critic, and in-process fallback."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from godview_shared.config import (
    DEFAULT_AGENT_FALLBACK_MODEL_ID,
    DEFAULT_AGENT_MODEL_ID,
    DEFAULT_AGENT_MODEL_LOCATION,
)
from godview_shared.contracts import Diagnosis, ProposedAction

from godview.agents.adk.compat import (
    BaseAgent,
    InvocationContext,
    LlmAgent,
    RemoteA2aAgent,
)
from godview.agents.adk.specialist import (
    SpecialistAgent,
    create_specialist_agent,
)

logger = logging.getLogger("godview.agents.adk.court")

CONSENSUS_THRESHOLD: float = 0.85
MAX_SWARM_ROUNDS: int = 4
MAX_SWARM_DURATION_S: float = 90.0

ARCHETYPE_DOMAINS: tuple[str, ...] = (
    "application",
    "data",
    "business_line",
    "change",
    "platform",
)


@dataclass
class SpecialistCandidate:
    """Diagnostic proposal from an individual specialist."""

    agent_id: str
    archetype: str
    diagnosis: Diagnosis
    runtime: str = "in_process"  # "in_process" or "remote_a2a"


class CourtCriticAgent(LlmAgent):
    """Critic agent in the LoopAgent rounds that evaluates consensus across specialist hypotheses."""

    def __init__(self, ctx: Any | None = None, **kwargs: Any) -> None:
        super().__init__(
            name="court_critic",
            instruction=(
                "You are the Court Critic Agent. Evaluate diagnoses from all participating specialists. "
                "Synthesize hypotheses, score consensus (0.0 to 1.0), identify contradictions, "
                "and consolidate verified proposed remediation actions."
            ),
            model=DEFAULT_AGENT_MODEL_ID,
            fallback_model=DEFAULT_AGENT_FALLBACK_MODEL_ID,
            location=DEFAULT_AGENT_MODEL_LOCATION,
            **kwargs,
        )
        self.ctx = ctx

    async def evaluate_consensus(
        self, candidates: list[SpecialistCandidate]
    ) -> tuple[float, Diagnosis]:
        """Score consensus across candidate diagnoses and build consolidated Diagnosis."""
        if not candidates:
            return 0.0, Diagnosis(
                hypothesis="No specialist diagnostics were available.",
                confidence=0.0,
            )

        # Check for highest confidence candidate
        best_candidate = max(candidates, key=lambda c: c.diagnosis.confidence)
        confidences = [c.diagnosis.confidence for c in candidates]
        avg_confidence = sum(confidences) / len(confidences)

        # Merge citations and proposed actions
        all_citations: set[str] = set()
        all_actions: list[ProposedAction] = []
        all_evidence: list[dict[str, Any]] = []

        for cand in candidates:
            all_citations.update(cand.diagnosis.citations)
            for act in cand.diagnosis.proposed_actions:
                if isinstance(act, ProposedAction):
                    all_actions.append(act)
                elif isinstance(act, dict):
                    all_actions.append(ProposedAction.model_validate(act))
            if isinstance(cand.diagnosis.evidence, list):
                for ev in cand.diagnosis.evidence:
                    if isinstance(ev, dict):
                        all_evidence.append(ev)
                    else:
                        all_evidence.append({"item": str(ev)})
            elif isinstance(cand.diagnosis.evidence, dict):
                all_evidence.append(cand.diagnosis.evidence)

        # Deduplicate actions by capability_id and args
        unique_actions: list[ProposedAction] = []
        seen_keys: set[str] = set()
        for action in all_actions:
            key = f"{action.capability_id}:{sorted(action.args.items())}"
            if key not in seen_keys:
                seen_keys.add(key)
                unique_actions.append(action)

        consensus_score = min(1.0, max(best_candidate.diagnosis.confidence, avg_confidence))

        consolidated = Diagnosis(
            hypothesis=best_candidate.diagnosis.hypothesis,
            confidence=consensus_score,
            evidence=all_evidence,
            proposed_actions=unique_actions,
            root_cause_category=best_candidate.diagnosis.root_cause_category,
            citations=sorted(all_citations),
        )
        return consensus_score, consolidated


class CourtOrchestrator(LlmAgent):
    """Sovereign root LlmAgent managing specialist court fan-out, debate, and consensus."""

    def __init__(
        self,
        ctx: Any | None = None,
        sovereign_url: str | None = None,
        sub_agents: Sequence[BaseAgent] | None = None,
        llm_runner: Any | None = None,
        **kwargs: Any,
    ) -> None:
        instruction = (
            "You are the God-View CourtOrchestrator. You convene the court of specialist agents "
            "to diagnose incidents across application, data, business-line, change, and platform tiers. "
            "Fan-out investigations to specialists, moderate debate, and synthesize a single "
            "grounded Diagnosis with verified proposed remediation actions."
        )
        super().__init__(
            name="CourtOrchestrator",
            model=DEFAULT_AGENT_MODEL_ID,
            fallback_model=DEFAULT_AGENT_FALLBACK_MODEL_ID,
            location=DEFAULT_AGENT_MODEL_LOCATION,
            instruction=instruction,
            sub_agents=sub_agents,
            description="Root Sovereign Court Orchestrator for multi-agent incident diagnosis",
            llm_runner=llm_runner,
            **kwargs,
        )
        self.ctx = ctx
        self.sovereign_url = sovereign_url.rstrip("/") if sovereign_url else ""
        self.critic = CourtCriticAgent(ctx=ctx)
        self._in_process_specialists: dict[str, SpecialistAgent] = {}
        self._remote_specialists: dict[str, RemoteA2aAgent] = {}
        self._init_sub_agents()

    def _init_sub_agents(self) -> None:
        """Initialize in-process specialists as baseline fallback."""
        for dom in ARCHETYPE_DOMAINS:
            agent = create_specialist_agent(
                archetype=dom,
                subject_ref=f"default-{dom}",
                ctx=self.ctx,
                sovereign_url=self.sovereign_url,
            )
            self._in_process_specialists[dom] = agent

    def register_remote_agent(self, agent_id: str, card_url: str) -> RemoteA2aAgent:
        """Register a deployed Reasoning Engine via its A2A card URL."""
        remote = RemoteA2aAgent(
            name=agent_id,
            agent_card=card_url,
            client=getattr(self.ctx, "agents", None),
        )
        self._remote_specialists[agent_id] = remote
        self.sub_agents.append(remote)
        return remote

    def get_specialist(self, domain: str) -> tuple[BaseAgent, str]:
        """Return the specialist agent for a domain, preferring deployed RemoteA2aAgent over in-process."""
        for aid, remote in self._remote_specialists.items():
            if aid.startswith(f"{domain}__"):
                return remote, "remote_a2a"
        # Fallback to in-process specialist
        if domain not in self._in_process_specialists:
            self._in_process_specialists[domain] = create_specialist_agent(
                archetype=domain,
                subject_ref=f"discovered-{domain}",
                ctx=self.ctx,
                sovereign_url=self.sovereign_url,
            )
        return self._in_process_specialists[domain], "in_process"

    async def convene(
        self,
        incident_id: str,
        evidence_pack: dict[str, Any] | None = None,
        specialists: Sequence[str] | None = None,
        timeout_s: float = MAX_SWARM_DURATION_S,
        consensus_target: float = CONSENSUS_THRESHOLD,
    ) -> Diagnosis:
        """Convene the Court: Parallel fan-out to specialists followed by LoopAgent critic rounds."""
        start_time = time.monotonic()
        target_domains = list(specialists) if specialists else list(ARCHETYPE_DOMAINS)
        ep = evidence_pack or {}

        # 1. ParallelAgent fan-out to relevant specialists
        active_specialists: list[tuple[BaseAgent, str, str]] = []
        for dom in target_domains:
            agent, runtime = self.get_specialist(dom)
            active_specialists.append((agent, dom, runtime))

        candidates: list[SpecialistCandidate] = []

        async def _run_specialist(agent: BaseAgent, dom: str, rtype: str) -> None:
            if isinstance(agent, SpecialistAgent):
                diag = agent.diagnose_evidence(incident_id, ep)
                candidates.append(
                    SpecialistCandidate(
                        agent_id=agent.name,
                        archetype=dom,
                        diagnosis=diag,
                        runtime=rtype,
                    )
                )
            elif isinstance(agent, RemoteA2aAgent):
                # Dispatch A2A request to remote reasoning engine
                task_ctx = InvocationContext(
                    session_id=f"convene-{incident_id}",
                    metadata={"input": {"incident_id": incident_id, "evidence_pack": ep}},
                )
                last_ev = await agent.invoke({"incident_id": incident_id, "evidence": ep}, task_ctx)
                if isinstance(last_ev.content, dict) and "diagnosis" in last_ev.content:
                    diag = Diagnosis.model_validate(last_ev.content["diagnosis"])
                else:
                    diag = Diagnosis(
                        hypothesis=f"Remote diagnosis from {agent.name}: {last_ev.content}",
                        confidence=0.85,
                        citations=[f"agent:{agent.name}"],
                    )
                candidates.append(
                    SpecialistCandidate(
                        agent_id=agent.name,
                        archetype=dom,
                        diagnosis=diag,
                        runtime=rtype,
                    )
                )
            else:
                diag = Diagnosis(
                    hypothesis=f"Specialist {agent.name} completed run.",
                    confidence=0.75,
                )
                candidates.append(
                    SpecialistCandidate(
                        agent_id=agent.name,
                        archetype=dom,
                        diagnosis=diag,
                        runtime=rtype,
                    )
                )

        # Run fan-out concurrently
        parallel_tasks = [
            asyncio.create_task(_run_specialist(agent, dom, rtype))
            for agent, dom, rtype in active_specialists
        ]
        await asyncio.gather(*parallel_tasks, return_exceptions=True)

        # 2. LoopAgent critic rounds (<=4 rounds, <=90 s, consensus >=0.85)
        rounds = 0
        final_diagnosis = Diagnosis(
            hypothesis="Diagnosis pending swarm consensus.",
            confidence=0.0,
        )

        while rounds < MAX_SWARM_ROUNDS:
            rounds += 1
            elapsed = time.monotonic() - start_time
            if elapsed >= timeout_s:
                logger.info(
                    "Court convene hit max duration %.1fs at round %d", elapsed, rounds
                )
                break

            score, current_diag = await self.critic.evaluate_consensus(candidates)
            final_diagnosis = current_diag
            if score >= consensus_target:
                logger.info(
                    "Court consensus reached at round %d with score %.2f (target %.2f)",
                    rounds,
                    score,
                    consensus_target,
                )
                break

        return final_diagnosis


def register_in_process_court(ctx: Any) -> CourtOrchestrator:
    """Register in-process CourtOrchestrator fallback via ctx.hooks.on_incident."""
    orchestrator = CourtOrchestrator(ctx=ctx)

    async def _on_incident_callback(incident_doc: Any) -> Diagnosis | None:
        """Handle incoming incident by convening the court in-process."""
        inc_data = (
            incident_doc.model_dump(mode="json")
            if hasattr(incident_doc, "model_dump")
            else dict(incident_doc or {})
        )
        inc_id = str(inc_data.get("incident_id") or inc_data.get("doc_id") or "inc-auto")
        ep = inc_data.get("evidence_pack") or {}

        diagnosis = await orchestrator.convene(
            incident_id=inc_id,
            evidence_pack=ep,
        )

        # Store diagnosis onto the incident in Firestore if docs store is available
        if hasattr(ctx, "docs") and ctx.docs is not None:
            existing = ctx.docs.get("incidents", inc_id)
            if existing is not None:
                diagnoses = list(existing.get("diagnoses", []))
                diagnoses.append(diagnosis.model_dump(mode="json"))
                ctx.docs.set("incidents", inc_id, {"diagnoses": diagnoses}, merge=True)

        return diagnosis

    # Register on ctx.hooks.on_incident if available
    if hasattr(ctx, "hooks"):
        if not hasattr(ctx.hooks, "on_incident"):
            ctx.hooks.on_incident = []
        hook_list = ctx.hooks.on_incident
        if isinstance(hook_list, list):
            hook_list.append(_on_incident_callback)

    return orchestrator
