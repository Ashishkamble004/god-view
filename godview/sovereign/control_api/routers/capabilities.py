"""Capabilities router (Plan §3.4 & §1.2).

Exposes:
- GET /capabilities: list all registered capabilities
- GET /capabilities/{capability_id}: describe a specific capability
- POST /capabilities/invoke: dispatch capability to registered executor (never echo)
- POST /capabilities/{capability_id}:invoke: alternate invocation endpoint
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from godview_shared.capabilities import CAPABILITY_REGISTRY, get_capability
from godview_shared.contracts import TrustViolation
from pydantic import BaseModel, Field

from godview.sovereign.remediation import (
    ExecutionResult,
    NoExecutorError,
    RemediationDispatcher,
    ScopeViolationError,
)
from godview.sovereign.trust_ladder import TrustGate

logger = logging.getLogger(__name__)


class CapabilityInvokeRequest(BaseModel):
    """Payload to invoke a capability through TrustGate and the remediation dispatcher."""

    capability_id: str | None = None
    agent_id: str
    target_resource: str = "resource"
    args: dict[str, Any] = Field(default_factory=dict)
    approval_id: str | None = None
    incident_id: str | None = None
    blast_radius_resources: int = 1
    novel_skill_first_step: bool = False
    probe_url: str | None = None


def build_router(ctx: Any) -> APIRouter:
    """Construct and return the FastAPI APIRouter for capabilities."""
    router = APIRouter(tags=["Capabilities"])

    # Attach or reuse dispatcher
    dispatcher: RemediationDispatcher
    if hasattr(ctx, "dispatcher") and ctx.dispatcher is not None:
        dispatcher = ctx.dispatcher
    else:
        dispatcher = RemediationDispatcher()
        if hasattr(ctx, "__dict__"):
            ctx.dispatcher = dispatcher

    # Attach or reuse trust_gate
    trust_gate: TrustGate
    if hasattr(ctx, "trust_gate") and ctx.trust_gate is not None:
        trust_gate = ctx.trust_gate
    else:
        trust_gate = TrustGate(docs=getattr(ctx, "docs", None), bus=getattr(ctx, "bus", None))
        if hasattr(ctx, "__dict__"):
            ctx.trust_gate = trust_gate

    @router.get("/capabilities")
    async def list_capabilities() -> list[dict[str, Any]]:
        """List all capabilities in the canonical registry."""
        return [spec.model_dump(mode="json") for spec in CAPABILITY_REGISTRY.values()]

    @router.get("/capabilities/{capability_id}")
    async def get_capability_spec(capability_id: str) -> dict[str, Any]:
        """Fetch specification for a capability."""
        try:
            spec = get_capability(capability_id)
            return spec.model_dump(mode="json")
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Capability '{capability_id}' not found") from None

    async def _handle_invoke(
        cap_id: str,
        body: CapabilityInvokeRequest,
    ) -> dict[str, Any]:
        try:
            result: ExecutionResult = await dispatcher.invoke(
                ctx=ctx,
                trust_gate=trust_gate,
                capability_id=cap_id,
                target_resource=body.target_resource,
                args=body.args,
                agent_id=body.agent_id,
                approval_id=body.approval_id,
                novel_skill_first_step=body.novel_skill_first_step,
                probe_url=body.probe_url,
            )
            return result.model_dump(mode="json")
        except NoExecutorError as nee:
            raise HTTPException(status_code=501, detail=str(nee)) from None
        except ScopeViolationError as sve:
            raise HTTPException(status_code=403, detail=str(sve)) from None
        except TrustViolation as tv:
            raise HTTPException(status_code=403, detail=str(tv)) from None
        except KeyError as ke:
            raise HTTPException(status_code=404, detail=str(ke)) from None

    @router.post("/capabilities/invoke")
    async def invoke_capability_root(body: CapabilityInvokeRequest) -> dict[str, Any]:
        """Dispatch capability execution through TrustGate and registered executors."""
        cap_id = body.capability_id
        if not cap_id:
            raise HTTPException(status_code=400, detail="Missing 'capability_id' in invocation body")
        return await _handle_invoke(cap_id, body)

    @router.post("/capabilities/{capability_id}:invoke")
    async def invoke_capability_path(
        capability_id: str,
        body: CapabilityInvokeRequest,
    ) -> dict[str, Any]:
        """Dispatch named capability execution through TrustGate and registered executors."""
        return await _handle_invoke(capability_id, body)

    return router


__all__ = ["build_router"]
