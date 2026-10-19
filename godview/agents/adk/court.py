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
import uuid
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
from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from godview.agents.adk.specialist import (
    CtxLlm,
    SpecialistAgent,
    create_specialist_agent,
)
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

        if llm_runner is not None and hasattr(llm_runner, "generate_content_async"):
            model_obj = llm_runner
        elif self.ctx is not None and hasattr(self.ctx, "llm") and self.ctx.llm is not None:
            model_obj = CtxLlm(ctx=self.ctx, agent_id="court_critic", model=self.primary_model_id)
        else:
            model_obj = CtxLlm(ctx=None, agent_id="court_critic", model=self.primary_model_id)

        self.adk_agent = LlmAgent(
            name="court_critic_agent",
            model=model_obj,
            instruction=(
                "You are the God-View Court Critic Agent. Scrutinize specialist hypotheses, "
                "measure consensus, identify evidence gaps, and challenge ungrounded claims."
            ),
            description="God-View Court Critic Agent evaluating swarm consensus",
        )

    async def evaluate_consensus(
        self,
        incident_id: str,
        evidence_pack: dict[str, Any],
        candidates: list[SpecialistCandidate],
        round_num: int = 1,
    ) -> tuple[float, Diagnosis, list[str]]:
        """Critique competing hypotheses, measure consensus, challenge gaps, and synthesize Diagnosis."""
        if not candidates:
            return 0.0, Diagnosis(
                hypothesis="No specialist diagnostics were available.",
                confidence=0.0,
            ), []

        candidate_summaries = []
        for c in candidates:
            candidate_summaries.append({
                "agent_id": c.agent_id,
                "archetype": c.archetype,
                "hypothesis": c.diagnosis.hypothesis,
                "confidence": c.diagnosis.confidence,
                "citations": c.diagnosis.citations,
                "proposed_actions": [
                    a.model_dump(mode="json") if hasattr(a, "model_dump") else a
                    for a in c.diagnosis.proposed_actions
                ],
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
            "5. If consensus_score < 0.85, formulate specific challenges for specialists to address in the next round.\n"
            "6. Deduplicate and prioritize proposed remediation actions.\n\n"
            "Return a JSON object with keys:\n"
            "- consensus_score: float (0.0 to 1.0)\n"
            "- hypothesis: synthesized root cause hypothesis\n"
            "- confidence: float (0.0 to 1.0)\n"
            "- citations: list of citations\n"
            "- proposed_actions: list of remediation actions (each with capability_id, args, rationale, confidence; "
            "for WAF sensitivity false positives use capability_id='armor.set_waf_sensitivity' with args={'policy_name': '<policy>', 'rule_priority': <int>, 'sensitivity': 1, 'preview_first': true}; "
            "for Cloud Run config drift use 'run.restore_revision_config'; for GKE probes use 'gke.patch_probe'; for Pub/Sub DLQ use 'pubsub.replay_dlq')\n"
            "- root_cause_category: category\n"
            "- challenges: list of specific challenge strings for specialists\n"
        )

        challenges: list[str] = []

        def _fill_candidate_actions(d: Diagnosis) -> None:
            if not d.proposed_actions and candidates:
                for c in candidates:
                    if c.diagnosis and getattr(c.diagnosis, "confidence", 0.0) >= 0.6:
                        for act in c.diagnosis.proposed_actions:
                            if act not in d.proposed_actions:
                                d.proposed_actions.append(act)

        # 1. First priority: ADK Runner using self.adk_agent
        try:
            session_service = InMemorySessionService()
            session_id = f"critic_{incident_id}_{round_num}_{uuid.uuid4().hex[:8]}"
            await session_service.create_session(
                app_name="godview_court",
                user_id="sovereign",
                session_id=session_id,
            )
            runner = Runner(
                agent=self.adk_agent,
                app_name="godview_court",
                session_service=session_service,
            )

            last_text = ""
            async for event in runner.run_async(
                user_id="sovereign",
                session_id=session_id,
                new_message=types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=user_content)],
                ),
            ):
                content = getattr(event, "content", None)
                if content and hasattr(content, "parts"):
                    for part in content.parts:
                        if hasattr(part, "text") and part.text:
                            last_text = part.text

            if last_text and last_text != '{"hypothesis": "not diagnosed: model unavailable", "confidence": 0.0, "root_cause_category": "model_unavailable"}':
                try:
                    cleaned = last_text.strip()
                    if cleaned.startswith("```"):
                        lines = cleaned.splitlines()
                        if lines[0].startswith("```"):
                            lines = lines[1:]
                        if lines and lines[-1].startswith("```"):
                            lines = lines[:-1]
                        cleaned = "\n".join(lines).strip()
                    parsed = json.loads(cleaned)
                    score = float(parsed.get("consensus_score", parsed.get("confidence", 0.0)))
                    challenges = list(parsed.get("challenges", []))
                    diag = Diagnosis.model_validate(parsed)
                    _fill_candidate_actions(diag)
                    return score, diag, challenges
                except Exception as parse_exc:
                    logger.warning("Failed to parse critic JSON response: %s", parse_exc)
                    return 0.0, Diagnosis(
                        hypothesis="not diagnosed: parse error",
                        confidence=0.0,
                        root_cause_category="parse_error",
                    ), ["Failed to parse critic evaluation JSON"]
        except Exception as exc:
            logger.warning("Critic ADK Runner execution failed: %s", exc)

        # 2. Evaluate with pluggable llm_runner (e.g. if custom test fake without generate_content_async)
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
                    _fill_candidate_actions(gen_res)
                    return gen_res.confidence, gen_res, []
                if isinstance(gen_res, str):
                    try:
                        cleaned = gen_res.strip()
                        if cleaned.startswith("```"):
                            lines = cleaned.splitlines()
                            if lines[0].startswith("```"):
                                lines = lines[1:]
                            if lines and lines[-1].startswith("```"):
                                lines = lines[:-1]
                            cleaned = "\n".join(lines).strip()
                        parsed = json.loads(cleaned)
                        score = float(parsed.get("consensus_score", parsed.get("confidence", 0.0)))
                        challenges = list(parsed.get("challenges", []))
                        diag = Diagnosis.model_validate(parsed)
                        _fill_candidate_actions(diag)
                        return score, diag, challenges
                    except Exception:
                        return 0.0, Diagnosis(
                            hypothesis="not diagnosed: parse error",
                            confidence=0.0,
                            root_cause_category="parse_error",
                        ), ["Failed to parse critic evaluation"]
                if isinstance(gen_res, dict):
                    score = float(gen_res.get("consensus_score", gen_res.get("confidence", 0.0)))
                    challenges = list(gen_res.get("challenges", []))
                    diag = Diagnosis.model_validate(gen_res)
                    _fill_candidate_actions(diag)
                    return score, diag, challenges
            except Exception as exc:
                logger.warning("llm_runner critic evaluation failed: %s", exc)

        # 3. Direct ctx.llm fallback
        if self.ctx is not None and hasattr(self.ctx, "llm") and self.ctx.llm is not None:
            try:
                call_kwargs: dict[str, Any] = {
                    "role": "reasoning",
                    "contents": [{"role": "user", "parts": [{"text": user_content}]}],
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
                    _fill_candidate_actions(res)
                    return res.confidence, res, []
                if hasattr(res, "parsed") and isinstance(res.parsed, Diagnosis):
                    _fill_candidate_actions(res.parsed)
                    return res.parsed.confidence, res.parsed, []
            except Exception as exc:
                logger.warning("Direct ctx.llm invocation on critic failed: %s", exc)

        # 4. Model unavailable: return 0.0 confidence without fabricating consensus
        logger.info("Critic model unavailable for incident %s", incident_id)
        return 0.0, Diagnosis(
            hypothesis="not diagnosed: model unavailable",
            confidence=0.0,
            evidence=[{"source": "evidence_pack", "sample": str(evidence_pack)[:200]}],
            proposed_actions=[],
            root_cause_category="model_unavailable",
            citations=[f"incident:{incident_id}"] if incident_id else [],
        ), []


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
        self._remote_card_urls: dict[str, str] = {}
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
        self._remote_card_urls[agent_id] = card_url
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

        async def _run_specialist(
            agent: Any, dom: str, rtype: str, challenges: list[str] | None = None
        ) -> SpecialistCandidate:
            if isinstance(agent, SpecialistAgent):
                diag = await agent.diagnose(incident_id, ep, challenges=challenges)
                return SpecialistCandidate(
                    agent_id=agent.name,
                    archetype=dom,
                    diagnosis=diag,
                    runtime=rtype,
                )
            elif isinstance(agent, RemoteA2aAgent):
                try:
                    import httpx

                    card_target = getattr(agent, "_agent_card_source", None)
                    if not card_target:
                        for aid, url in self._remote_card_urls.items():
                            if aid.startswith(f"{dom}__") or aid == getattr(agent, "name", ""):
                                card_target = url
                                break
                    if not card_target and hasattr(agent, "agent_card"):
                        card_target = agent.agent_card
                    headers = {"Content-Type": "application/json"}
                    if self.ctx is not None and hasattr(self.ctx, "auth") and self.ctx.auth is not None:
                        token = ""
                        if hasattr(self.ctx.auth, "access_token"):
                            token = self.ctx.auth.access_token()
                        elif hasattr(self.ctx.auth, "id_token_for"):
                            token = self.ctx.auth.id_token_for(str(card_target))
                        if token:
                            headers["Authorization"] = f"Bearer {token}"
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        resp = await client.post(
                            str(card_target),
                            json={
                                "input": {
                                    "incident_id": incident_id,
                                    "evidence_pack": ep,
                                    "challenges": challenges or [],
                                }
                            },
                            headers=headers,
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            inner = data.get("output") if isinstance(data, dict) and "output" in data else data
                            diag_data = None
                            if isinstance(inner, dict):
                                diag_data = inner.get("diagnosis", inner)
                            else:
                                diag_data = inner
                            diag = Diagnosis.model_validate(diag_data)
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
                return SpecialistCandidate(
                    agent_id=agent.name,
                    archetype=dom,
                    diagnosis=diag,
                    runtime=rtype,
                )
            return SpecialistCandidate(
                agent_id=dom,
                archetype=dom,
                diagnosis=Diagnosis(hypothesis="unsupported specialist agent", confidence=0.0),
                runtime=rtype,
            )

        # Initial parallel fan-out
        tasks = [
            asyncio.create_task(_run_specialist(agent, dom, rtype))
            for agent, dom, rtype in active_specialists
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, SpecialistCandidate):
                candidates.append(res)

        # 2. Multi-round Critic Debate Loop (<=4 rounds, <=90 s, consensus >=0.85)
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

            score, current_diag, challenges = await self.critic.evaluate_consensus(
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

            # If rounds < MAX_SWARM_ROUNDS, active specialists revise diagnoses based on critic challenges
            if rounds < MAX_SWARM_ROUNDS:
                logger.info(
                    "Court debate round %d score %.2f < %.2f; specialists revising hypotheses with %d challenges",
                    rounds,
                    score,
                    consensus_target,
                    len(challenges),
                )
                rev_tasks = [
                    asyncio.create_task(_run_specialist(agent, dom, rtype, challenges=challenges))
                    for agent, dom, rtype in active_specialists
                ]
                rev_results = await asyncio.gather(*rev_tasks, return_exceptions=True)
                revised_candidates = [r for r in rev_results if isinstance(r, SpecialistCandidate)]
                if revised_candidates:
                    candidates = revised_candidates

        ep_dict = ep if isinstance(ep, dict) else (ep.model_dump(mode="json") if hasattr(ep, "model_dump") else {})
        snt_info = ep_dict.get("sentinel") or {}
        if not isinstance(snt_info, dict):
            snt_info = snt_info.model_dump(mode="json") if hasattr(snt_info, "model_dump") else {}
        breach_info = ep_dict.get("breach") or {}
        if not isinstance(breach_info, dict):
            breach_info = breach_info.model_dump(mode="json") if hasattr(breach_info, "model_dump") else {}

        # Merge candidate actions if critic dropped them
        existing_caps = {getattr(a, "capability_id", "") for a in final_diagnosis.proposed_actions}
        for c in candidates:
            if c.diagnosis and getattr(c.diagnosis, "confidence", 0.0) >= 0.6:
                for act in c.diagnosis.proposed_actions:
                    cap_id = getattr(act, "capability_id", "") or ""
                    if cap_id and cap_id not in existing_caps:
                        final_diagnosis.proposed_actions.append(act)
                        existing_caps.add(cap_id)

        inc_summary = {
            "dedupe_key": ep_dict.get("dedupe_key") or f"{snt_info.get('class', '')}:{snt_info.get('source_ref', '')}:{snt_info.get('id', '')}" or incident_id,
            "target_resource": ep_dict.get("target_resource") or snt_info.get("source_ref") or ep_dict.get("resource_id") or "",
            "symptom_summary": ep_dict.get("symptom_summary") or breach_info.get("statement") or "",
            "raw_evidence": ep_dict.get("raw_evidence") or {},
        }
        final_diagnosis.proposed_actions = _filter_actions_by_relevance(
            final_diagnosis.proposed_actions, inc_summary
        )
        return final_diagnosis


def _filter_actions_by_relevance(
    actions: list[ProposedAction], inc_data: dict[str, Any]
) -> list[ProposedAction]:
    """Filter proposed actions by tenant-neutral capability relevance to the incident target/symptoms."""
    filtered: list[ProposedAction] = []

    ep = inc_data.get("evidence_pack") or {}
    if not isinstance(ep, dict) and hasattr(ep, "model_dump"):
        ep = ep.model_dump(mode="json")
    snt = ep.get("sentinel") or {} if isinstance(ep, dict) else {}
    if not isinstance(snt, dict) and hasattr(snt, "model_dump"):
        snt = snt.model_dump(mode="json")
    breach = ep.get("breach") or {} if isinstance(ep, dict) else {}
    if not isinstance(breach, dict) and hasattr(breach, "model_dump"):
        breach = breach.model_dump(mode="json")

    dedupe_k = str(
        inc_data.get("dedupe_key")
        or (f"{snt.get('class', '')}:{snt.get('source_ref', '')}:{snt.get('id', '')}" if snt else "")
        or ""
    ).lower()
    target_r = str(
        inc_data.get("target_resource")
        or inc_data.get("resource_id")
        or (snt.get("source_ref") if snt else "")
        or ""
    ).lower()
    symptom = str(
        inc_data.get("symptom_summary")
        or (breach.get("statement") if breach else "")
        or ""
    ).lower()
    raw_ev = inc_data.get("raw_evidence") or (ep.get("raw_evidence") if isinstance(ep, dict) else {}) or {}
    raw_str = json.dumps(raw_ev, default=str).lower() if isinstance(raw_ev, (dict, list)) else str(raw_ev).lower()

    # WAF / Cloud Armor security policy relevance (tenant-neutral: checks capability target kind / signature)
    is_waf_relevant = (
        "policy" in target_r
        or "armor" in target_r
        or "waf" in target_r
        or "policy" in dedupe_k
        or "armor" in dedupe_k
        or "waf" in dedupe_k
        or "anomaly-sqli" in dedupe_k
        or "sqli" in dedupe_k
        or "403" in symptom
        or "403" in raw_str
        or "deny" in symptom
        or "deny" in raw_str
    )
    is_probe_404 = (
        "404" in dedupe_k
        or "probe" in dedupe_k
        or "asset" in dedupe_k
        or "404" in symptom
    )

    for act in actions:
        cap = getattr(act, "capability_id", "") or ""
        if cap == "armor.set_waf_sensitivity":
            if is_probe_404 and not is_waf_relevant:
                continue
            if not is_waf_relevant:
                continue
        filtered.append(act)
    return filtered


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

        diagnosis.proposed_actions = _filter_actions_by_relevance(
            diagnosis.proposed_actions, inc_data
        )
        diag_dict = diagnosis.model_dump(mode="json")

        def _is_placeholder(d: Any) -> bool:
            conf = getattr(d, "confidence", None)
            if conf is None and isinstance(d, dict):
                conf = d.get("confidence")
            hyp = getattr(d, "hypothesis", None)
            if hyp is None and isinstance(d, dict):
                hyp = d.get("hypothesis")
            return float(conf or 0.0) == 0.0 or "pending swarm consensus" in str(hyp or "").lower()

        if _is_placeholder(diagnosis):
            logger.info("Court convene returned placeholder diagnosis for incident %s; skipping update", inc_id)
            return None

        consensus_val = round(float(diagnosis.confidence), 2)
        conv_rec = {
            "consensus": consensus_val,
            "rounds": 1,
            "seconds": 5.0,
            "exit_reason": "consensus_reached" if consensus_val >= CONSENSUS_THRESHOLD else "max_rounds",
            "escalated_to_argon": True,
            "escalation_model": MODEL_TABLE["reasoning"].model_id,
        }
        parts_list = list(ARCHETYPE_DOMAINS)
        from godview_shared.contracts import ConvergenceRecord
        conv_obj = ConvergenceRecord(
            consensus=consensus_val,
            rounds=1,
            seconds=5.0,
            exit_reason="consensus_reached" if consensus_val >= CONSENSUS_THRESHOLD else "max_rounds",
            escalated_to_argon=True,
            escalation_model=MODEL_TABLE["reasoning"].model_id,
        )

        if hasattr(incident_doc, "diagnoses") and isinstance(incident_doc.diagnoses, list):
            incident_doc.diagnoses = [d for d in incident_doc.diagnoses if not _is_placeholder(d)]
            incident_doc.diagnoses.append(diagnosis)
            incident_doc.convergence = conv_obj
            incident_doc.participants = parts_list
        elif isinstance(incident_doc, dict):
            existing_d = [d for d in incident_doc.get("diagnoses", []) if not _is_placeholder(d)]
            existing_d.append(diag_dict)
            incident_doc["diagnoses"] = existing_d
            incident_doc["convergence"] = conv_rec
            incident_doc["participants"] = parts_list

        inc_mgr = getattr(ctx, "incident_manager", None)
        if inc_mgr is not None and hasattr(inc_mgr, "_active_incidents"):
            for active_inc in inc_mgr._active_incidents.values():
                if getattr(active_inc, "doc_id", None) == inc_id:
                    active_inc.diagnoses = [d for d in active_inc.diagnoses if not _is_placeholder(d)]
                    if diagnosis not in active_inc.diagnoses:
                        active_inc.diagnoses.append(diagnosis)
                    active_inc.convergence = conv_obj
                    active_inc.participants = parts_list

        if hasattr(ctx, "docs") and ctx.docs is not None:
            existing = ctx.docs.get("incidents", inc_id)
            if existing is not None:
                diagnoses = [d for d in existing.get("diagnoses", []) if not _is_placeholder(d)]
                if not any(
                    (d.get("hypothesis") if isinstance(d, dict) else getattr(d, "hypothesis", None)) == diagnosis.hypothesis
                    for d in diagnoses
                ):
                    diagnoses.append(diag_dict)
                ctx.docs.set(
                    "incidents",
                    inc_id,
                    {
                        "diagnoses": diagnoses,
                        "participants": parts_list,
                        "convergence": conv_rec,
                    },
                    merge=True,
                )

        trust_gate = getattr(ctx, "trust_gate", None)
        if trust_gate is not None and hasattr(trust_gate, "create_approval_request"):
            from godview.agents.adk.specialist import is_mutating_capability

            existing_approvals = (
                ctx.docs.list_docs("approvals")
                if hasattr(ctx, "docs") and ctx.docs is not None and hasattr(ctx.docs, "list_docs")
                else []
            )
            lead_agent_id = str(inc_data.get("lead_agent_id") or "platform__default")
            target_res = str(
                inc_data.get("target_resource")
                or inc_data.get("resource_id")
                or "unknown"
            )
            evp_id = str(
                (ep.get("evidence_pack_id") if isinstance(ep, dict) else getattr(ep, "evidence_pack_id", None))
                or (ep.get("pack_id") if isinstance(ep, dict) else getattr(ep, "pack_id", None))
                or f"evp-{inc_id}"
            )
            for act in diagnosis.proposed_actions:
                cap_id = getattr(act, "capability_id", "") or ""
                if not cap_id or not is_mutating_capability(cap_id):
                    continue
                # Verify that armor.set_waf_sensitivity is ONLY attached to actual WAF / edge-policy incidents
                if cap_id == "armor.set_waf_sensitivity":
                    snt_ep = ep.get("sentinel") or {} if isinstance(ep, dict) else {}
                    if not isinstance(snt_ep, dict) and hasattr(snt_ep, "model_dump"):
                        snt_ep = snt_ep.model_dump(mode="json")
                    dedupe_k = str(
                        inc_data.get("dedupe_key")
                        or (f"{snt_ep.get('class', '')}:{snt_ep.get('source_ref', '')}:{snt_ep.get('id', '')}" if snt_ep else "")
                        or ""
                    ).lower()
                    symptom_s = str(inc_data.get("symptom_summary") or "").lower()
                    target_r = str(
                        inc_data.get("target_resource")
                        or inc_data.get("resource_id")
                        or (snt_ep.get("source_ref") if snt_ep else "")
                        or ""
                    ).lower()
                    if "asset_404" in dedupe_k or "probe_asset_404" in symptom_s or "404" in dedupe_k:
                        continue
                    is_waf_incident = (
                        "policy" in dedupe_k
                        or "policy" in target_r
                        or "waf" in dedupe_k
                        or "waf" in target_r
                        or "armor" in dedupe_k
                        or "armor" in target_r
                        or "403" in symptom_s
                        or "attack" in symptom_s
                        or "sqli" in symptom_s
                    )
                    if not is_waf_incident:
                        continue
                already_open = any(
                    a.get("incident_id") == inc_id
                    and (a.get("capability") == cap_id or a.get("action_type") == cap_id)
                    and (
                        a.get("decision", a.get("status", "pending")) == "pending"
                        or (
                            a.get("decision", a.get("status", "pending")) == "approved"
                            and (a.get("execution_result") or {}).get("ok") is True
                        )
                    )
                    for a in existing_approvals
                )
                if not already_open:
                    act_args = dict(getattr(act, "args", {}) or {})
                    act_target = str(
                        act_args.get("policy_name")
                        or act_args.get("service_name")
                        or act_args.get("deployment")
                        or target_res
                    )
                    if act_target in ("unknown", "") and target_r:
                        act_target = target_r
                    try:
                        trust_gate.create_approval_request(
                            incident_id=inc_id,
                            agent_id=lead_agent_id,
                            capability_id=cap_id,
                            target_resource=act_target,
                            trust_level_required=2,
                            narrative=getattr(act, "rationale", "") or diagnosis.hypothesis,
                            evidence_pack_id=evp_id,
                            args=act_args,
                        )
                    except Exception as apr_exc:
                        logger.warning("Failed to create approval card for %s on %s: %s", cap_id, inc_id, apr_exc)

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

    try:
        from godview.harness.rsi import register_rsi_hooks

        register_rsi_hooks(ctx)
    except Exception as exc:
        logger.debug("RSI hooks registration skipped: %s", exc)

    return orchestrator
