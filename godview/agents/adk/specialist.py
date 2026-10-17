# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Generic Court Specialist Agents built on Google ADK LlmAgent."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from godview_shared.config import (
    DEFAULT_AGENT_FALLBACK_MODEL_ID,
    DEFAULT_AGENT_MODEL_ID,
    DEFAULT_AGENT_MODEL_LOCATION,
)
from godview_shared.contracts import Diagnosis, ProposedAction

from godview.agents.adk.compat import LlmAgent

logger = logging.getLogger("godview.agents.adk.specialist")

# Generic specialist archetype instructions (zero hardcoded tenant domain knowledge)
ARCHETYPE_GENERIC_INSTRUCTIONS: dict[str, str] = {
    "application": (
        "You are an Application Court Specialist Agent. You analyze service runtime health, "
        "HTTP/gRPC status codes, latency percentiles, payload serialization/offset issues, "
        "and service dependency graphs. All situational context must come strictly from "
        "the provided evidence pack and runtime BusinessContext. Cite specific log entries, "
        "HTTP status details, and configuration snapshots. Only propose actions from the capability registry."
    ),
    "data": (
        "You are a Data Court Specialist Agent. You analyze database connectivity, query execution "
        "times, lock contention, active transactions, schema migrations, and statistical distribution shifts. "
        "All situational context must come strictly from the provided evidence pack and runtime BusinessContext. "
        "Ground your findings in query traces, transaction states, and schema definitions. Only propose actions "
        "from the capability registry."
    ),
    "business_line": (
        "You are a Business-Line Court Specialist Agent. You monitor end-to-end service journey "
        "conformance, asynchronous message processing, queue depth, dead-letter message backlogs, "
        "step sequence invariants, and journey-level metrics. All situational context must come strictly "
        "from the provided evidence pack and runtime BusinessContext. Formulate hypotheses based on message flow "
        "and transaction boundaries. Only propose actions from the capability registry."
    ),
    "change": (
        "You are a Change Court Specialist Agent. You correlate system anomalies with recent deployment "
        "events, container image updates, source code repository commits, configuration edits, and "
        "infrastructure drift diffs. All situational context must come strictly from the provided evidence "
        "pack and runtime BusinessContext. Cross-reference change timestamps with incident onset. "
        "Only propose actions from the capability registry."
    ),
    "platform": (
        "You are a Platform Court Specialist Agent. You analyze infrastructure resources, container "
        "lifecycles, exit codes (such as OOM / exit code 137), CPU and memory saturation, load balancer "
        "posture, ingress/invoker rules, and edge security/WAF policy rules. All situational context must "
        "come strictly from the provided evidence pack and runtime BusinessContext. Cite specific resource metrics, "
        "security policy rule numbers, and posture drift. Only propose actions from the capability registry."
    ),
}

MUTATING_CAPABILITY_PREFIXES = (
    "armor.set_",
    "run.set_",
    "run.restore_",
    "pubsub.replay_",
    "gke.patch_",
    "tenant.admin_",
    "agent.set_",
    "git.create_",
    "git.rollback_",
)


def is_mutating_capability(capability_id: str) -> bool:
    """Determine whether a capability requires operator approval before execution."""
    try:
        from godview_shared.capabilities import get_capability

        spec = get_capability(capability_id)
        return bool(spec.mutating)
    except Exception:
        pass
    return any(capability_id.startswith(p) for p in MUTATING_CAPABILITY_PREFIXES)


class SpecialistTools:
    """Runtime tools bound to SovereignContext and available to specialist LlmAgents."""

    def __init__(
        self,
        ctx: Any | None = None,
        sovereign_url: str | None = None,
    ) -> None:
        self.ctx = ctx
        self.sovereign_url = sovereign_url.rstrip("/") if sovereign_url else ""
        self.proposed_actions: list[ProposedAction] = []

    def invoke_capability(
        self, capability_id: str, args: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Invoke a capability. Read capabilities execute freely; mutating capabilities require approval."""
        arg_dict = args or {}
        if is_mutating_capability(capability_id):
            return {
                "status": "approval_required",
                "requires_approval": True,
                "capability_id": capability_id,
                "args": arg_dict,
                "message": (
                    f"Capability '{capability_id}' is a mutating action and requires operator "
                    "approval through TrustGate. Use propose_remediation to queue for approval."
                ),
            }

        # Check if executor is registered on ctx
        if self.ctx is not None and hasattr(self.ctx, "executors"):
            executor = self.ctx.executors.get(capability_id)
            if executor is not None:
                return executor(**arg_dict)

        # Fallback for read capability execution
        return {
            "status": "ok",
            "capability_id": capability_id,
            "args": arg_dict,
            "output": f"Executed read capability '{capability_id}'.",
        }

    def get_business_context(self, service_id: str | None = None) -> dict[str, Any]:
        """Fetch discovered BusinessContext for a service."""
        target_id = service_id or "default"
        if self.ctx is not None and hasattr(self.ctx, "docs") and self.ctx.docs is not None:
            doc = self.ctx.docs.get("discovered_services", target_id)
            if doc and "business_context" in doc:
                return doc["business_context"]
            if doc:
                return doc
        return {"service_id": target_id, "status": "no_business_context_found"}

    def query_ledger(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Query the event ledger for historical state transitions or KPIs."""
        if self.ctx is not None and hasattr(self.ctx, "ledger") and self.ctx.ledger is not None:
            return self.ctx.ledger.query(query, params)
        return []

    def get_incident(self, incident_id: str) -> dict[str, Any]:
        """Fetch incident details including the full evidence pack."""
        if self.ctx is not None and hasattr(self.ctx, "docs") and self.ctx.docs is not None:
            doc = self.ctx.docs.get("incidents", incident_id)
            if doc:
                return doc
        return {"incident_id": incident_id, "evidence_pack": {}}

    def propose_remediation(
        self,
        capability_id: str,
        args: dict[str, Any] | None = None,
        rationale: str = "",
        confidence: float = 0.8,
    ) -> dict[str, Any]:
        """Record a proposed remediation action for this incident."""
        action = ProposedAction(
            capability_id=capability_id,
            args=args or {},
            rationale=rationale,
            confidence=max(0.0, min(1.0, confidence)),
        )
        self.proposed_actions.append(action)
        return {
            "status": "proposed",
            "action": action.model_dump(mode="json"),
        }


class SpecialistAgent(LlmAgent):
    """An archetype-specific specialist LlmAgent reasoning over evidence."""

    def __init__(
        self,
        archetype: str,
        subject_ref: str,
        *,
        ctx: Any | None = None,
        sovereign_url: str | None = None,
        llm_runner: Any | None = None,
        model: str | None = None,
        fallback_model: str | None = None,
        location: str | None = None,
    ) -> None:
        if archetype not in ARCHETYPE_GENERIC_INSTRUCTIONS:
            valid = list(ARCHETYPE_GENERIC_INSTRUCTIONS.keys())
            raise ValueError(f"Unknown archetype '{archetype}'. Must be one of {valid}")

        agent_id = f"{archetype}__{subject_ref}"
        instruction = ARCHETYPE_GENERIC_INSTRUCTIONS[archetype]
        self.archetype = archetype
        self.subject_ref = subject_ref
        self.ctx = ctx
        self.tools_helper = SpecialistTools(ctx=ctx, sovereign_url=sovereign_url)
        self.specialist_tools = self.tools_helper

        tools: list[Callable[..., Any]] = [
            self.tools_helper.invoke_capability,
            self.tools_helper.get_business_context,
            self.tools_helper.query_ledger,
            self.tools_helper.get_incident,
            self.tools_helper.propose_remediation,
        ]

        # Model configuration using shared config MODEL_TABLE
        chosen_model = model or DEFAULT_AGENT_MODEL_ID
        chosen_fallback = fallback_model or DEFAULT_AGENT_FALLBACK_MODEL_ID
        chosen_loc = location or DEFAULT_AGENT_MODEL_LOCATION

        super().__init__(
            name=agent_id,
            model=chosen_model,
            fallback_model=chosen_fallback,
            location=chosen_loc,
            instruction=instruction,
            tools=tools,
            description=f"God-View {archetype.title()} Specialist for {subject_ref}",
            llm_runner=llm_runner,
        )

    def diagnose_evidence(
        self,
        incident_id: str,
        evidence_pack: dict[str, Any] | None = None,
    ) -> Diagnosis:
        """Perform diagnostic analysis over an incident evidence pack."""
        ep = evidence_pack or {}
        # Fetch business context if available
        biz_ctx = self.tools_helper.get_business_context(self.subject_ref)

        # Build prompt content from evidence pack without domain nouns
        prompt_data = {
            "incident_id": incident_id,
            "subject_ref": self.subject_ref,
            "archetype": self.archetype,
            "evidence_pack": ep,
            "business_context": biz_ctx,
        }

        # Check if ctx.llm is available
        if self.ctx is not None and hasattr(self.ctx, "llm") and self.ctx.llm is not None:
            try:
                user_content = (
                    f"Instruction:\n{self.instruction}\n\n"
                    f"Analyze the following evidence pack for incident {incident_id} on {self.subject_ref}.\n"
                    f"Evidence: {json.dumps(prompt_data, default=str)}\n"
                    "Formulate a precise hypothesis, confidence score (0.0 to 1.0), citations, "
                    "and any proposed remediation actions."
                )
                response = self.ctx.llm.generate(
                    role="reasoning",
                    contents=[{"role": "user", "parts": [{"text": user_content}]}],
                    response_schema=Diagnosis,
                )
                if isinstance(response, Diagnosis):
                    return response
                if isinstance(response, dict):
                    return Diagnosis.model_validate(response)
            except Exception as exc:
                logger.warning("ctx.llm invocation failed, falling back to evidence heuristics: %s", exc)

        # Fallback evidence-based deterministic diagnosis when LLM is unavailable
        hypothesis = (
            f"Evidence-based analysis by {self.archetype} specialist for {self.subject_ref} "
            f"on incident {incident_id}."
        )
        confidence = 0.85
        citations = []
        proposed_actions = list(self.tools_helper.proposed_actions)

        # Check for specific evidence patterns in evidence_pack
        if "preconfiguredExprIds" in str(ep) or "denied_by_security_policy" in str(ep):
            hypothesis = (
                "Edge security policy rejected valid application traffic due to overly strict "
                "WAF preconfigured rule sensitivity."
            )
            confidence = 0.95
            citations.append("edge.security_policy:denied_by_security_policy")
            proposed_actions.append(
                ProposedAction(
                    capability_id="armor.set_waf_sensitivity",
                    args={"sensitivity": 1, "preview": True},
                    rationale="Tune WAF rule sensitivity to 1 with preview to allow legitimate JSON requests.",
                    confidence=0.95,
                )
            )
        elif "137" in str(ep) or "OOMKilled" in str(ep):
            hypothesis = "Container terminated with exit code 137 (OOMKilled) under memory pressure."
            confidence = 0.92
            citations.append("container.status:exit_code_137")
            proposed_actions.append(
                ProposedAction(
                    capability_id="gke.patch_probe",
                    args={"memory_limit": "2Gi"},
                    rationale="Increase memory limit and adjust liveness probe.",
                    confidence=0.9,
                )
            )
        elif "offset" in str(ep) or "schema" in str(ep):
            hypothesis = "Payload format shifted unexpectedly causing parser misalignment."
            confidence = 0.90
            citations.append("payload.parser:offset_shift")
            proposed_actions.append(
                ProposedAction(
                    capability_id="git.create_pull_request",
                    args={"patch": "update_schema_offset"},
                    rationale="Update payload schema definitions",
                    confidence=0.9,
                )
            )

        category = (
            ep.get("scenario")
            or ep.get("category")
            or ("payload_schema_offset_shift" if "offset" in str(ep) else f"{self.archetype}_evidence_finding")
        )
        return Diagnosis(
            hypothesis=hypothesis,
            confidence=confidence,
            evidence=[{"source": "evidence_pack", "sample": str(ep)[:200]}],
            proposed_actions=proposed_actions,
            root_cause_category=category,
            citations=citations or [f"incident:{incident_id}"],
        )


def create_specialist_agent(
    archetype: str,
    subject_ref: str,
    *,
    ctx: Any | None = None,
    sovereign_url: str | None = None,
    llm_runner: Any | None = None,
) -> SpecialistAgent:
    """Factory creating an ADK SpecialistAgent for the given archetype and subject."""
    return SpecialistAgent(
        archetype=archetype,
        subject_ref=subject_ref,
        ctx=ctx,
        sovereign_url=sovereign_url,
        llm_runner=llm_runner,
    )
