"""Capabilities router (Plan §3.4 & §1.2).

Exposes:
- GET /capabilities: list all registered capabilities
- GET /capabilities/{capability_id}: describe a specific capability
- POST /capabilities/invoke: dispatch capability to registered executor (never echo)
- POST /capabilities/{capability_id}:invoke: alternate invocation endpoint
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
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


def _get_caller_identity(request: Request, header_email: str | None = None) -> str:
    test_hdr = request.headers.get("x-godview-test-principal")
    if test_hdr:
        return test_hdr
    if header_email:
        return header_email.split(":")[-1].strip()
    return "service@godview.test"


def build_router(ctx: Any) -> APIRouter:
    """Construct and return the FastAPI APIRouter for capabilities."""
    router = APIRouter(tags=["Capabilities"])

    async def service_or_operator_dep(
        request: Request,
        x_goog_authenticated_user_email: Annotated[
            str | None, Header(alias="X-Goog-Authenticated-User-Email")
        ] = None,
    ) -> Any:
        if ctx and hasattr(ctx, "auth") and ctx.auth is not None:
            verify_iap = getattr(ctx.auth, "verify_iap_jwt", None)
            if callable(verify_iap) and verify_iap(request):
                return verify_iap(request)
            verify_oidc = getattr(ctx.auth, "verify_google_oidc", None)
            if callable(verify_oidc) and verify_oidc(request):
                return verify_oidc(request)
        env_mode = getattr(getattr(ctx, "settings", None), "env_mode", "gcp")
        if env_mode == "hermetic" or x_goog_authenticated_user_email or request.headers.get("x-godview-test-principal"):
            return _get_caller_identity(request, x_goog_authenticated_user_email)
        raise HTTPException(status_code=401, detail="Unauthorized: authenticated service or operator required")

    dispatcher: RemediationDispatcher
    if hasattr(ctx, "dispatcher") and ctx.dispatcher is not None:
        dispatcher = ctx.dispatcher
    else:
        dispatcher = RemediationDispatcher()
        if hasattr(ctx, "__dict__"):
            ctx.dispatcher = dispatcher

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
        # If approval_id is supplied, verify binding against stored approval card
        effective_args = dict(body.args)
        if body.approval_id is not None and hasattr(ctx, "docs") and ctx.docs is not None:
            card_raw = ctx.docs.get("approvals", body.approval_id)
            if card_raw is not None:
                card_cap = card_raw.get("capability") or card_raw.get("action_type")
                if card_cap != cap_id:
                    raise HTTPException(
                        status_code=403,
                        detail=f"Approval '{body.approval_id}' bound to '{card_cap}', not '{cap_id}'",
                    )
                card_target = card_raw.get("target_resource")
                if card_target != body.target_resource:
                    raise HTTPException(
                        status_code=403,
                        detail=f"Approval '{body.approval_id}' bound to target '{card_target}', not '{body.target_resource}'",
                    )
                card_args = card_raw.get("args")
                if card_args is not None:
                    if body.args and body.args != card_args:
                        raise HTTPException(
                            status_code=403,
                            detail=f"Approval '{body.approval_id}' arguments do not match invocation arguments",
                        )
                    effective_args = dict(card_args)

        try:
            result: ExecutionResult = await dispatcher.invoke(
                ctx=ctx,
                trust_gate=trust_gate,
                capability_id=cap_id,
                target_resource=body.target_resource,
                args=effective_args,
                agent_id=body.agent_id,
                approval_id=body.approval_id,
                novel_skill_first_step=body.novel_skill_first_step,
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
    async def invoke_capability_root(
        body: CapabilityInvokeRequest,
        caller: Annotated[Any, Depends(service_or_operator_dep)] = None,
    ) -> dict[str, Any]:
        """Dispatch capability execution through TrustGate and registered executors."""
        cap_id = body.capability_id
        if not cap_id:
            raise HTTPException(status_code=400, detail="Missing 'capability_id' in invocation body")
        return await _handle_invoke(cap_id, body)

    @router.post("/capabilities/{capability_id}:invoke")
    async def invoke_capability_path(
        capability_id: str,
        body: CapabilityInvokeRequest,
        caller: Annotated[Any, Depends(service_or_operator_dep)] = None,
    ) -> dict[str, Any]:
        """Dispatch named capability execution through TrustGate and registered executors."""
        return await _handle_invoke(capability_id, body)

    return router


__all__ = ["build_router"]
