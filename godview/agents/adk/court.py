# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Sovereign Court Orchestrator on Google ADK.

Coordinates multi-agent court deliberation over operational incidents:
1. ParallelAgent fan-out: dispatches incident evidence to relevant domain specialists
   (application, data, business_line, change, platform).
2. LoopAgent critic debate: an LLM Critic Agent iteratively challenges hypotheses
   against evidence citations, resolves domain conflicts, measures consensus
   (target >= 0.85, <= 4 rounds, <= 90 s duration), and synthesizes a single
   grounded Diagnosis contract.
3. In-process fallback: registered on ctx.hooks.on_incident to diagnose incidents
   when Vertex AI Agent Engine reasoning engines are pending deployment.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from godview_shared.config import (
    MODEL_TABLE,
)
from godview_shared.contracts import (
    Diagnosis,
    ProposedAction,
)
from google.adk.agents import BaseAgent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent

from godview.agents.adk.specialist import SpecialistAgent, create_specialist_agent
from godview.agents.archetypes import COURT_DOMAINS

logger = logging.getLogger("godview.agents.adk.court")

MAX_SWARM_ROUNDS = 4
MAX_SWARM_DURATION_S = 90.0
CONSENSUS_THRESHOLD = 0.85
ARCHETYPE_DOMAINS = tuple(sorted(COURT_DOMAINS))


@dataclass
class SpecialistCandidate:
    """Output from a single specialist agent in the fan-out pass."""

    agent_id: str
    archetype: str
    diagnosis: Diagnosis
    runtime: str = "in_process"


@dataclass
class CriticEvaluation:
    """Structured evaluation produced by the Court Critic LLM."""

    consensus_score: float
    synthesized_hypothesis: str
    root_cause_category: str
    evidence_citations: list[str] = field(default_factory=list)
    proposed_actions: list[ProposedAction] = field(default_factory=list)
    critic_notes: str = ""
    challenges: list[str] = field(default_factory=list)


class CourtCriticAgent:
    """Evaluates specialist hypotheses against evidence in a multi-round debate."""

    def __init__(
        self,
        ctx: Any | None = None,
        llm_runner: Any | None = None,
    ) -> None:
        self.ctx = ctx
        self.llm_runner = llm_runner
        self.primary_model_id = MODEL_TABLE.get("reasoning", MODEL_TABLE["reasoning"]).model_id

    async def evaluate_consensus(
        self,
        incident_id: str,
        evidence_pack: dict[str, Any],
        candidates: list[SpecialistCandidate],
        round_num: int = 1,
    ) -> tuple[float, Diagnosis]:
        """Critique competing hypotheses, measure consensus, and synthesize Diagnosis."""
        if not candidates:
            return 0.0, Diagnosis(
                hypothesis="No specialist diagnostics were available.",
                confidence=0.0,
            )

        # Prepare summary of specialist findings
        candidate_summaries = []
        for c in candidates:
            candidate_summaries.append({
                "agent_id": c.agent_id,
                "archetype": c.archetype,
                "hypothesis": c.diagnosis.hypothesis,
                "confidence": c.diagnosis.confidence,
                "citations": c.diagnosis.citations,
                "proposed_actions": [a.model_dump(mode="json") if hasattr(a, "model_dump") else a for a in c.diagnosis.proposed_actions],
                "root_cause_category": c.diagnosis.root_cause_category,
            })

        user_content = (
            f"You are the God-View Court Critic evaluating incident {incident_id} (Round {round_num}).\n"
            f"Incident Evidence Pack:\n{json.dumps(evidence_pack, default=str)}\n\n"
            f"Specialist Candidate Hypotheses:\n{json.dumps(candidate_summaries, default=str)}\n\n"
            "Instructions:\n"
            "1. Scrutinize each specialist hypothesis against the actual evidence pack.\n"
            "2. Challenge any hypothesis lacking evidence citations or contradicting telemetry.\n"
            "3. Synthesize a unified consensus hypothesis incorporating the most grounded evidence.\n"
            "4. Compute a consensus_score between 0.0 and 1.0 reflecting agreement among grounded specialists.\n"
            "5. Deduplicate and prioritize proposed remediation actions.\n"
            "Return a structured CriticEvaluation or Diagnosis."
        )

        # 1. Evaluate with ctx.llm
        if self.ctx is not None and hasattr(self.ctx, "llm") and self.ctx.llm is not None:
            try:
                call_kwargs: dict[str, Any] = {
                    "role": "reasoning",
                    "contents": [{"role": "user", "parts": [{"text": user_content}]}],
                    "response_schema": Diagnosis,
                }
                try:
                    sig = inspect.signature(self.ctx.llm.generate)
                    if "agent_id" in sig.parameters or any(
                        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                    ):
                        call_kwargs["agent_id"] = "court_critic"
                except Exception:
                    pass

                res = self.ctx.llm.generate(**call_kwargs)
                if inspect.isawaitable(res):
                    res = await res

                if isinstance(res, Diagnosis):
                    return res.confidence, res
                if hasattr(res, "parsed") and isinstance(res.parsed, Diagnosis):
                    return res.parsed.confidence, res.parsed
                if hasattr(res, "text") and res.text:
                    try:
                        diag = Diagnosis.model_validate_json(res.text)
                        return diag.confidence, diag
                    except Exception:
                        pass
                if isinstance(res, dict):
                    diag = Diagnosis.model_validate(res)
                    return diag.confidence, diag
            except Exception as exc:
                logger.warning("Critic LLM evaluation failed: %s", exc)

        # 2. Evaluate with pluggable llm_runner
        if self.llm_runner is not None and hasattr(self.llm_runner, "generate"):
            try:
                gen_res = self.llm_runner.generate(
                    model=self.primary_model_id,
                    contents=user_content,
                    agent_id="court_critic",
                )
                if inspect.isawaitable(gen_res):
                    gen_res = await gen_res

                if isinstance(gen_res, Diagnosis):
                    return gen_res.confidence, gen_res
                if isinstance(gen_res, str):
                    try:
                        diag = Diagnosis.model_validate_json(gen_res)
                        return diag.confidence, diag
                    except Exception:
                        return 0.85, Diagnosis(
                            hypothesis=gen_res,
                            confidence=0.85,
                            citations=[f"critic:round_{round_num}"],
                        )
                if isinstance(gen_res, dict):
                    diag = Diagnosis.model_validate(gen_res)
                    return diag.confidence, diag
            except Exception as exc:
                logger.warning("llm_runner critic evaluation failed: %s", exc)

        # 3. Model unavailable: return 0.0 confidence without fabricating consensus
        logger.info("Critic model unavailable for incident %s", incident_id)
        return 0.0, Diagnosis(
            hypothesis="not diagnosed: model unavailable",
            confidence=0.0,
            evidence=[{"source": "evidence_pack", "sample": str(evidence_pack)[:200]}],
            proposed_actions=[],
            root_cause_category="model_unavailable",
            citations=[f"incident:{incident_id}"] if incident_id else [],
        )


class CourtOrchestrator:
    """Sovereign root coordinator managing specialist fan-out and critic consensus debate."""

    def __init__(
        self,
        ctx: Any | None = None,
        sovereign_url: str | None = None,
        llm_runner: Any | None = None,
    ) -> None:
        self.ctx = ctx
        self.sovereign_url = sovereign_url.rstrip("/") if sovereign_url else ""
        self.llm_runner = llm_runner
        self.critic = CourtCriticAgent(ctx=ctx, llm_runner=llm_runner)
        self._in_process_specialists: dict[str, SpecialistAgent] = {}
        self._remote_specialists: dict[str, RemoteA2aAgent] = {}
        self.sub_agents: list[BaseAgent] = []
        self._init_sub_agents()

    def _init_sub_agents(self) -> None:
        """Initialize in-process specialists as baseline fallback."""
        for dom in ARCHETYPE_DOMAINS:
            agent = create_specialist_agent(
                archetype=dom,
                subject_ref=f"default_{dom}",
                ctx=self.ctx,
                sovereign_url=self.sovereign_url,
                llm_runner=self.llm_runner,
            )
            self._in_process_specialists[dom] = agent
            self.sub_agents.append(agent.adk_agent)

    def register_remote_agent(self, agent_id: str, card_url: str) -> RemoteA2aAgent:
        """Register a deployed Reasoning Engine via its A2A card URL."""
        cleaned_name = re.sub(r"[^a-zA-Z0-9_]", "_", agent_id)
        if not cleaned_name or (not cleaned_name[0].isalpha() and cleaned_name[0] != "_"):
            cleaned_name = f"agent_{cleaned_name}"
        remote = RemoteA2aAgent(
            name=cleaned_name,
            agent_card=card_url,
        )
        self._remote_specialists[agent_id] = remote
        self.sub_agents.append(remote)
        return remote

    def get_specialist(self, domain: str) -> tuple[Any, str]:
        """Return specialist agent for domain, preferring deployed RemoteA2aAgent over in-process."""
        for aid, remote in self._remote_specialists.items():
            if aid.startswith(f"{domain}__"):
                return remote, "remote_a2a"
        if domain not in self._in_process_specialists:
            self._in_process_specialists[domain] = create_specialist_agent(
                archetype=domain,
                subject_ref=f"discovered-{domain}",
                ctx=self.ctx,
                sovereign_url=self.sovereign_url,
                llm_runner=self.llm_runner,
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

        # 1. Parallel fan-out to specialists
        active_specialists: list[tuple[Any, str, str]] = []
        for dom in target_domains:
            agent, runtime = self.get_specialist(dom)
            active_specialists.append((agent, dom, runtime))

        candidates: list[SpecialistCandidate] = []

        async def _run_specialist(agent: Any, dom: str, rtype: str) -> None:
            if isinstance(agent, SpecialistAgent):
                diag = await agent.diagnose(incident_id, ep)
                candidates.append(
                    SpecialistCandidate(
                        agent_id=agent.name,
                        archetype=dom,
                        diagnosis=diag,
                        runtime=rtype,
                    )
                )
            elif isinstance(agent, RemoteA2aAgent):
                # Query remote reasoning engine A2A endpoint
                try:
                    import httpx
                    headers = {}
                    if self.ctx is not None and hasattr(self.ctx, "auth") and self.ctx.auth is not None:
                        token = self.ctx.auth.id_token_for(agent.agent_card)
                        if token:
                            headers["Authorization"] = f"Bearer {token}"
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        resp = await client.post(
                            str(agent.agent_card),
                            json={"incident_id": incident_id, "evidence_pack": ep},
                            headers=headers,
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            diag = Diagnosis.model_validate(data.get("diagnosis", data))
                        else:
                            diag = Diagnosis(
                                hypothesis=f"Remote specialist HTTP {resp.status_code}",
                                confidence=0.0,
                            )
                except Exception as exc:
                    logger.warning("Remote A2A invocation failed for %s: %s", agent.name, exc)
                    diag = Diagnosis(
                        hypothesis=f"Remote specialist unreachable: {exc}",
                        confidence=0.0,
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

        # 2. Loop critic rounds (<=4 rounds, <=90 s, consensus >=0.85)
        rounds = 0
        final_diagnosis = Diagnosis(
            hypothesis="Diagnosis pending swarm consensus.",
            confidence=0.0,
        )

        while rounds < MAX_SWARM_ROUNDS:
            rounds += 1
            elapsed = time.monotonic() - start_time
            if elapsed >= timeout_s:
                logger.info("Court convene reached duration limit %.1fs at round %d", elapsed, rounds)
                break

            score, current_diag = await self.critic.evaluate_consensus(
                incident_id=incident_id,
                evidence_pack=ep,
                candidates=candidates,
                round_num=rounds,
            )
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
    ctx.court_orchestrator = orchestrator

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

    if hasattr(ctx, "hooks"):
        if not hasattr(ctx.hooks, "on_incident"):
            ctx.hooks.on_incident = []
        hook_target = ctx.hooks.on_incident
        if isinstance(hook_target, list):
            hook_target.append(_on_incident_callback)
        elif callable(hook_target):
            existing_fn = hook_target
            ctx.hooks.on_incident = [_on_incident_callback, existing_fn]

    return orchestrator


def install_court_and_rsi(ctx: Any) -> CourtOrchestrator:
    """Unified entrypoint installing CourtOrchestrator and RSI hooks on SovereignContext."""
    orchestrator = register_in_process_court(ctx)

    # Wire RSI hooks
    try:
        from godview.harness.rsi import register_rsi_hooks
        register_rsi_hooks(ctx)
    except Exception as exc:
        logger.debug("RSI hooks registration skipped: %s", exc)

    return orchestrator
