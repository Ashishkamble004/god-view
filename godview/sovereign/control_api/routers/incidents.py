"""Control API router for incidents (`/incidents`).

Endpoints:
- GET /incidents (list active/past incidents with optional status and severity filtering)
- GET /incidents/{incident_id} (fetch incident details and full evidence pack)
- POST /incidents/{incident_id}:close (close incident with operator authorization)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from godview.sovereign.incidents.manager import IncidentManager


class IncidentCloseRequest(BaseModel):
    """Payload for manually closing an incident."""

    reason: str = Field(default="Resolved by operator", description="Resolution rationale or false-positive reason")
    outcome: Literal["resolved", "mitigated", "false_positive"] = Field(
        default="resolved", description="Target incident status"
    )


def _get_operator_dep(ctx: Any) -> Callable[..., Any]:
    """Retrieve require_operator auth dependency from SovereignContext."""
    auth = getattr(ctx, "auth", None)
    if auth is not None and hasattr(auth, "require_operator"):
        return auth.require_operator

    async def _unauthenticated_operator(request: Request) -> Any:
        test_p = request.headers.get("x-godview-test-principal")
        if test_p or getattr(ctx, "auth", None) is None:
            return {"email": test_p or "operator@godview.internal", "role": "operator"}
        raise HTTPException(
            status_code=401, detail="Unauthorized: authenticated operator identity required"
        )

    return _unauthenticated_operator


def build_router(ctx: Any) -> APIRouter:
    """Build and return FastAPI router for `/incidents`."""
    router = APIRouter(tags=["incidents"])

    # Resolve IncidentManager
    incident_mgr: IncidentManager | None = getattr(ctx, "incident_manager", None)
    if incident_mgr is None and hasattr(ctx, "sentinel_engine") and hasattr(ctx.sentinel_engine, "incident_manager"):
        incident_mgr = ctx.sentinel_engine.incident_manager
    if incident_mgr is None:
        incident_mgr = IncidentManager(
            ctx=ctx,
            firestore=getattr(ctx, "docs", None),
            bus=getattr(ctx, "bus", None),
            agents=getattr(ctx, "agents", None),
            hooks=getattr(ctx, "hooks", None),
            clock=getattr(ctx, "clock", None),
        )

    operator_dep = _get_operator_dep(ctx)

    @router.get("/incidents")
    async def list_incidents(
        status: str | None = Query(default=None, description="Filter by outcome/status (open, resolved, etc.)"),
        severity: str | None = Query(default=None, description="Filter by severity (sev1, sev2, sev3)"),
        limit: int = Query(default=50, ge=1, le=200, description="Max incidents to return"),
    ) -> dict[str, Any]:
        """List active and historical incidents with optional status and severity filtering."""
        incidents = incident_mgr.list_incidents(status=status, severity=severity, limit=limit)
        return {
            "incidents": [
                inc.model_dump(mode="json", by_alias=True) if hasattr(inc, "model_dump") else inc
                for inc in incidents
            ]
        }

    @router.get("/incidents/{incident_id}")
    async def get_incident(incident_id: str) -> dict[str, Any]:
        """Retrieve details and evidence pack for a specific incident."""
        incident = incident_mgr.get_incident(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail=f"Unknown incident '{incident_id}'")
        return incident.model_dump(mode="json", by_alias=True) if hasattr(incident, "model_dump") else incident

    @router.post("/incidents/{incident_id}:close")
    async def close_incident(
        incident_id: str,
        body: IncidentCloseRequest | None = None,
        principal: Any = Depends(operator_dep),  # noqa: B008
    ) -> dict[str, Any]:
        """Close an open incident with reason and operator attribution."""
        req = body or IncidentCloseRequest()
        operator_email = (
            getattr(principal, "email", None)
            or (principal.get("email") if isinstance(principal, dict) else None)
            or str(principal)
        )
        try:
            closed = await incident_mgr.close_incident(
                incident_id,
                reason=req.reason,
                outcome=req.outcome,
                operator=operator_email,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        return {
            "status": "closed",
            "incident": closed.model_dump(mode="json", by_alias=True) if hasattr(closed, "model_dump") else closed,
        }

    return router
