# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Specialist Court LlmAgents on Google ADK for God-View.

Five archetype specialist agents:
1. application: microservice runtime, HTTP status codes, latency, payload schema, dependencies
2. data: persistence tiers, query latency, schema drift, replication, distribution shifts
3. business_line: end-to-end journey conformance, queue backlog, step ordering, KPI shifts
4. change: deployment regressions, git commit correlation, config drift, revision diffs
5. platform: container lifecycle, memory/CPU saturation, ingress/WAF, cloud infrastructure

All instructions are generic. All situational knowledge comes at runtime from
the evidence pack and BusinessContext. Tools provide Sovereign capability invocation
(reads execute freely; mutating actions require operator approval).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import uuid
from collections.abc import AsyncGenerator, Callable
from typing import Any

from godview_shared.capabilities import get_capability
from godview_shared.config import (
    DEFAULT_AGENT_FALLBACK_MODEL_ID,
    DEFAULT_AGENT_MODEL_ID,
    DEFAULT_AGENT_MODEL_LOCATION,
    MODEL_TABLE,
)
from godview_shared.contracts import (
    Diagnosis,
    ProposedAction,
)
from google.adk.a2a.utils.agent_to_a2a import to_a2a
from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm, LlmRequest
from google.adk.models import LlmResponse as AdkLlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.genai import types
from pydantic import ConfigDict

logger = logging.getLogger("godview.agents.adk.specialist")

ARCHETYPE_GENERIC_INSTRUCTIONS: dict[str, str] = {
    "application": (
        "You are the Application Specialist in the God-View Sovereign Court. "
        "Analyze service-tier telemetry, HTTP response status codes, route latency, "
        "payload schema contracts, and downstream dependency errors. "
        "Rely strictly on provided runtime evidence and discovered BusinessContext; "
        "never assume tenant domain vocabulary. Formulate a precise hypothesis, "
        "grounded citations, confidence score (0.0 to 1.0), and proposed remediation actions."
    ),
    "data": (
        "You are the Data Specialist in the God-View Sovereign Court. "
        "Analyze database and storage-tier telemetry, query execution latency, lock contention, "
        "schema evolution, and statistical distribution shifts across data entities. "
        "Rely strictly on provided runtime evidence and discovered BusinessContext; "
        "never assume tenant domain vocabulary. Formulate a precise hypothesis, "
        "grounded citations, confidence score (0.0 to 1.0), and proposed remediation actions."
    ),
    "business_line": (
        "You are the Business-Line Specialist in the God-View Sovereign Court. "
        "Analyze cross-service journey progression, queue message backlog, step sequencing, "
        "and business KPI invariants derived from OTel spans and ledger events. "
        "Rely strictly on provided runtime evidence and discovered BusinessContext; "
        "never assume tenant domain vocabulary. Formulate a precise hypothesis, "
        "grounded citations, confidence score (0.0 to 1.0), and proposed remediation actions."
    ),
    "change": (
        "You are the Change Specialist in the God-View Sovereign Court. "
        "Analyze deployment revision timelines, git commit diffs, configuration drift, "
        "infrastructure posture mutations, and rollout timing relative to incident onset. "
        "Rely strictly on provided runtime evidence and discovered BusinessContext; "
        "never assume tenant domain vocabulary. Formulate a precise hypothesis, "
        "grounded citations, confidence score (0.0 to 1.0), and proposed remediation actions."
    ),
    "platform": (
        "You are the Platform Specialist in the God-View Sovereign Court. "
        "Analyze container lifecycle events, crash terminations, OOMKilled restarts, "
        "compute resource limits, edge load balancer routing, and WAF security policies. "
        "Rely strictly on provided runtime evidence and discovered BusinessContext; "
        "never assume tenant domain vocabulary. Formulate a precise hypothesis, "
        "grounded citations, confidence score (0.0 to 1.0), and proposed remediation actions."
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
        spec = get_capability(capability_id)
        return bool(spec.mutating)
    except Exception:
        pass
    return any(capability_id.startswith(p) for p in MUTATING_CAPABILITY_PREFIXES)


class CtxLlm(BaseLlm):
    """ADK BaseLlm adapter delegating to ctx.llm with Argon->Flash fallback & token metering."""

    model: str = DEFAULT_AGENT_MODEL_ID
    ctx: Any = None
    agent_id: str = "specialist"
    model_config = ConfigDict(arbitrary_types_allowed=True)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[AdkLlmResponse]:
        if self.ctx is not None and hasattr(self.ctx, "llm") and self.ctx.llm is not None:
            contents = llm_request.contents
            tools = list(llm_request.tools_dict.values()) if getattr(llm_request, "tools_dict", None) else None

            call_kwargs: dict[str, Any] = {
                "role": "reasoning",
                "contents": contents,
            }
            try:
                sig = inspect.signature(self.ctx.llm.generate)
                if "agent_id" in sig.parameters or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                ):
                    call_kwargs["agent_id"] = self.agent_id
                if "tools" in sig.parameters or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                ):
                    call_kwargs["tools"] = tools
            except Exception:
                pass

            res = self.ctx.llm.generate(**call_kwargs)
            if inspect.isawaitable(res):
                res = await res

            if isinstance(res, AdkLlmResponse):
                yield res
                return
            if isinstance(res, types.GenerateContentResponse):
                if res.candidates and res.candidates[0].content:
                    yield AdkLlmResponse(content=res.candidates[0].content)
                    return

            func_calls = getattr(res, "function_calls", []) or []
            if func_calls:
                parts = []
                for fc in func_calls:
                    name = getattr(fc, "name", str(fc))
                    args = dict(getattr(fc, "args", {}) or {})
                    parts.append(types.Part.from_function_call(name=name, args=args))
                yield AdkLlmResponse(content=types.Content(parts=parts, role="model"))
                return

            if hasattr(res, "model_dump_json"):
                text = res.model_dump_json()
            elif isinstance(res, dict):
                text = json.dumps(res)
            elif hasattr(res, "text") and res.text:
                text = res.text
            else:
                text = str(res)

            part = types.Part.from_text(text=text)
            yield AdkLlmResponse(content=types.Content(parts=[part], role="model"))
        else:
            part = types.Part.from_text(
                text='{"hypothesis": "not diagnosed: model unavailable", "confidence": 0.0, "root_cause_category": "model_unavailable"}'
            )
            yield AdkLlmResponse(content=types.Content(parts=[part], role="model"))


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

        # Dispatch via Sovereign capability registry executors
        if self.ctx is not None and hasattr(self.ctx, "executors") and self.ctx.executors is not None:
            executor = self.ctx.executors.get(capability_id)
            if executor is not None:
                res = executor(**arg_dict)
                return res if isinstance(res, dict) else {"status": "ok", "result": res}

        if self.ctx is not None and hasattr(self.ctx, "remediation_dispatcher") and self.ctx.remediation_dispatcher is not None:
            dispatcher = self.ctx.remediation_dispatcher
            if hasattr(dispatcher, "invoke_read"):
                return dispatcher.invoke_read(capability_id, arg_dict)

        # Zero Echo: Never return fabricated status string when unhandled. Fail closed with NoExecutorError.
        from godview.sovereign.remediation.types import NoExecutorError

        raise NoExecutorError(capability_id)

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
        return {"incident_id": incident_id, "status": "incident_not_found"}

    def propose_remediation(
        self,
        capability_id: str,
        args: dict[str, Any] | None = None,
        rationale: str = "",
        confidence: float = 0.9,
    ) -> dict[str, Any]:
        """Queue a proposed remediation action for TrustGate evaluation and operator approval."""
        arg_dict = args or {}
        action = ProposedAction(
            capability_id=capability_id,
            args=arg_dict,
            rationale=rationale,
            confidence=confidence,
        )
        self.proposed_actions.append(action)
        return {
            "status": "proposed",
            "action": action.model_dump(mode="json"),
            "approval_required": is_mutating_capability(capability_id),
        }

    def build_tool_callables(self) -> list[Callable[..., Any]]:
        """Return the list of tool functions."""
        return [
            self.invoke_capability,
            self.get_business_context,
            self.query_ledger,
            self.get_incident,
            self.propose_remediation,
        ]

    def build_adk_tools(self) -> list[FunctionTool]:
        """Wrap each tool function in an ADK FunctionTool."""
        return [FunctionTool(func=f) for f in self.build_tool_callables()]


def _clean_adk_identifier(name: str) -> str:
    """Ensure a string is a valid Python identifier for ADK Node name."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    if not cleaned or (not cleaned[0].isalpha() and cleaned[0] != "_"):
        cleaned = f"agent_{cleaned}"
    return cleaned


class SpecialistAgent:
    """God-View Court Specialist LlmAgent built on Google ADK."""

    def __init__(
        self,
        archetype: str,
        subject_ref: str,
        *,
        ctx: Any | None = None,
        sovereign_url: str | None = None,
        llm_runner: Any | None = None,
    ) -> None:
        self.archetype = archetype
        self.subject_ref = subject_ref
        self.ctx = ctx
        self.sovereign_url = sovereign_url.rstrip("/") if sovereign_url else ""
        self.tools_helper = SpecialistTools(ctx=ctx, sovereign_url=self.sovereign_url)
        self.name = f"{archetype}__{subject_ref}"
        self.instruction = ARCHETYPE_GENERIC_INSTRUCTIONS.get(
            archetype,
            f"Analyze operational incidents affecting {subject_ref} using available evidence.",
        )
        self.primary_model_id = MODEL_TABLE.get("reasoning", MODEL_TABLE["reasoning"]).model_id
        self.fallback_model_id = DEFAULT_AGENT_FALLBACK_MODEL_ID
        self.model_location = DEFAULT_AGENT_MODEL_LOCATION

        adk_tools = self.tools_helper.build_adk_tools()
        adk_node_name = _clean_adk_identifier(self.name)

        if llm_runner is not None and hasattr(llm_runner, "generate_content_async"):
            model_obj = llm_runner
        elif self.ctx is not None and hasattr(self.ctx, "llm") and self.ctx.llm is not None:
            model_obj = CtxLlm(ctx=self.ctx, agent_id=self.name, model=self.primary_model_id)
        else:
            model_obj = CtxLlm(ctx=None, agent_id=self.name, model=self.primary_model_id)

        self.adk_agent = LlmAgent(
            name=adk_node_name,
            model=model_obj,
            instruction=self.instruction,
            tools=adk_tools,
            description=f"God-View {archetype} specialist agent for {subject_ref}",
        )
        self.llm_runner = llm_runner

    async def diagnose(
        self,
        incident_id: str,
        evidence_pack: dict[str, Any] | None = None,
        challenges: list[str] | None = None,
    ) -> Diagnosis:
        """Asynchronously analyze evidence and formulate a grounded Diagnosis using ADK Runner."""
        ep = evidence_pack or {}
        biz_ctx = self.tools_helper.get_business_context(self.subject_ref)
        prompt_data = {
            "incident_id": incident_id,
            "subject_ref": self.subject_ref,
            "archetype": self.archetype,
            "evidence_pack": ep,
            "business_context": biz_ctx,
        }

        user_content = (
            f"Instruction:\n{self.instruction}\n\n"
            f"Analyze the following evidence pack for incident {incident_id} on {self.subject_ref}.\n"
            f"Evidence: {json.dumps(prompt_data, default=str)}\n"
        )
        if challenges:
            user_content += (
                f"\nCRITIC CHALLENGES FROM PREVIOUS DEBATE ROUND:\n"
                f"{json.dumps(challenges, default=str)}\n"
                "Address each challenge directly, re-verify telemetry citations, and refine your hypothesis.\n"
            )
        user_content += (
            "\nReturn a valid JSON Diagnosis contract with keys:\n"
            "- hypothesis: precise root-cause hypothesis\n"
            "- confidence: float from 0.0 to 1.0\n"
            "- citations: list of evidence strings\n"
            "- proposed_actions: list of remediation actions\n"
            "- root_cause_category: category identifier\n"
        )

        try:
            session_service = InMemorySessionService()
            session_id = f"sess_{incident_id}_{uuid.uuid4().hex[:8]}"
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

            if last_text:
                try:
                    cleaned = last_text.strip()
                    if cleaned.startswith("```"):
                        lines = cleaned.splitlines()
                        if lines[0].startswith("```"):
                            lines = lines[1:]
                        if lines and lines[-1].startswith("```"):
                            lines = lines[:-1]
                        cleaned = "\n".join(lines).strip()

                    diag = Diagnosis.model_validate_json(cleaned)
                    if self.tools_helper.proposed_actions and not diag.proposed_actions:
                        diag.proposed_actions = list(self.tools_helper.proposed_actions)
                    return diag
                except Exception as parse_exc:
                    logger.warning(
                        "Failed to parse diagnosis JSON from specialist %s: %s",
                        self.name,
                        parse_exc,
                    )
                    # Zero Invented Confidence: 0.0 on parse error
                    return Diagnosis(
                        hypothesis="not diagnosed: parse error",
                        confidence=0.0,
                        evidence=[{"source": "raw_response", "sample": last_text[:200]}],
                        proposed_actions=[],
                        root_cause_category="parse_error",
                        citations=[f"incident:{incident_id}"],
                    )
        except Exception as exc:
            logger.warning("ADK Runner execution failed in specialist %s: %s", self.name, exc)

        return Diagnosis(
            hypothesis="not diagnosed: model unavailable",
            confidence=0.0,
            evidence=[{"source": "evidence_pack", "sample": str(ep)[:200]}],
            proposed_actions=[],
            root_cause_category="model_unavailable",
            citations=[f"incident:{incident_id}"] if incident_id else [],
        )

    async def revise_diagnosis(
        self,
        incident_id: str,
        evidence_pack: dict[str, Any] | None = None,
        challenges: list[str] | None = None,
    ) -> Diagnosis:
        """Revise diagnosis in response to critic debate challenges."""
        return await self.diagnose(incident_id, evidence_pack, challenges=challenges)

    def diagnose_evidence(
        self,
        incident_id: str,
        evidence_pack: dict[str, Any] | None = None,
        challenges: list[str] | None = None,
    ) -> Diagnosis:
        """Synchronous wrapper for diagnose()."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    lambda: asyncio.run(self.diagnose(incident_id, evidence_pack, challenges=challenges))
                )
                return future.result(timeout=60)
        return asyncio.run(self.diagnose(incident_id, evidence_pack, challenges=challenges))


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


def create_specialist_a2a_app(
    specialist: SpecialistAgent,
    host: str = "localhost",
    port: int = 8000,
) -> Any:
    """Convert specialist agent to an ADK A2A Starlette application."""
    return to_a2a(specialist.adk_agent, host=host, port=port)
