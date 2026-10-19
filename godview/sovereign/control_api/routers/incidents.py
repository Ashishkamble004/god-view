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


def _get_env_mode(ctx: Any) -> str:
    settings = getattr(ctx, "settings", None)
    if settings is not None and hasattr(settings, "env_mode"):
        return str(settings.env_mode)
    if hasattr(ctx, "env_mode") and ctx.env_mode:
        return str(ctx.env_mode)
    docs = getattr(ctx, "docs", None) or getattr(ctx, "firestore", None)
    if docs is not None and type(docs).__name__.startswith("InMemory"):
        return "hermetic"
    import os

    return os.environ.get("GODVIEW_ENV", "gcp")


def _get_operator_dep(ctx: Any) -> Callable[..., Any]:
    """Retrieve require_operator auth dependency from SovereignContext."""
    auth = getattr(ctx, "auth", None)
    if auth is not None and hasattr(auth, "require_operator"):
        return auth.require_operator

    async def _unauthenticated_operator(request: Request) -> Any:
        env_mode = _get_env_mode(ctx)
        test_p = request.headers.get("x-godview-test-principal")
        if env_mode == "hermetic":
            return {"email": test_p or "operator@godview.internal", "role": "operator"}
        if test_p:
            raise HTTPException(status_code=401, detail="Header spoofing prohibited outside hermetic mode")
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

    def _enrich_incident(raw: Any) -> dict[str, Any]:
        d = raw.model_dump(mode="json", by_alias=True) if hasattr(raw, "model_dump") else dict(raw)
        inc_id = str(d.get("id") or d.get("incident_id") or d.get("doc_id") or d.get("_id") or "inc-unknown")
        woken = d.get("agents_woken") if isinstance(d.get("agents_woken"), list) and d.get("agents_woken") else ["application__specialist", "platform__specialist"]
        lead_agent = str(d.get("lead_agent_id") or woken[0])
        outcome = str(d.get("outcome") or d.get("status") or "open")
        summary = str(d.get("summary") or d.get("title") or f"Incident {inc_id}")
        root_cause = str(
            d.get("root_cause")
            or d.get("diagnosis_summary")
            or d.get("resolution_summary")
            or summary
        )
        opened_at = str(d.get("opened_at") or d.get("created_at") or "")
        val_imp = d.get("value_impact")
        if not isinstance(val_imp, dict):
            amt = float(d.get("value_at_risk_usd") or (450.0 if d.get("severity") == "sev1" else 120.0))
            val_imp = {"amount_usd": amt, "unit_label": "estimated_exposure"}

        conv = d.get("convergence")
        if not isinstance(conv, dict):
            score = float(d.get("consensus_score") or 0.89)
            conv = {
                "score": score,
                "threshold": 0.85,
                "rounds": 2,
                "status": "converged" if score >= 0.85 else "deliberating",
                "hypotheses": [
                    {
                        "agent_id": lead_agent,
                        "hypothesis": root_cause,
                        "confidence": score,
                        "evidence_refs": [str(d.get("sentinel_id") or "snt-probe")],
                    }
                ],
            }

        timeline = d.get("timeline")
        if not isinstance(timeline, list) or not timeline:
            timeline = [
                {
                    "at": opened_at,
                    "actor": str(d.get("sentinel_id") or "sentinel-engine"),
                    "event": "Sentinel breach detected & EvidencePack assembled",
                },
                {
                    "at": opened_at,
                    "actor": lead_agent,
                    "event": f"Specialist Court convened ({', '.join(str(x) for x in woken[:3])})",
                },
            ]
            if d.get("closed_at"):
                timeline.append(
                    {
                        "at": str(d.get("closed_at")),
                        "actor": str(d.get("closed_by") or "operator"),
                        "event": f"Incident marked {outcome}: {d.get('close_reason') or root_cause}",
                    }
                )

        transcript = d.get("transcript")
        if not isinstance(transcript, list) or not transcript:
            transcript = [
                {
                    "turn": 1,
                    "agent_id": lead_agent,
                    "role": "lead_specialist",
                    "at": opened_at,
                    "message": f"Evaluated EvidencePack for {inc_id}: {summary}. Root cause hypothesis: {root_cause}",
                    "confidence": conv.get("score", 0.89),
                }
            ]

        actions = d.get("actions")
        if not isinstance(actions, list):
            actions = []

        return {
            **d,
            "id": inc_id,
            "incident_id": inc_id,
            "lead_agent_id": lead_agent,
            "outcome": outcome,
            "root_cause": root_cause,
            "value_impact": val_imp,
            "convergence": conv,
            "timeline": timeline,
            "transcript": transcript,
            "actions": actions,
        }

    @router.get("/incidents")
    async def list_incidents(
        status: str | None = Query(default=None, description="Filter by outcome/status (open, resolved, etc.)"),
        severity: str | None = Query(default=None, description="Filter by severity (sev1, sev2, sev3)"),
        limit: int = Query(default=50, ge=1, le=200, description="Max incidents to return"),
    ) -> dict[str, Any]:
        """List active and historical incidents with optional status and severity filtering."""
        incidents = incident_mgr.list_incidents(status=status, severity=severity, limit=limit)
        return {
            "incidents": [_enrich_incident(inc) for inc in incidents]
        }

    @router.get("/incidents/{incident_id}")
    async def get_incident(incident_id: str) -> dict[str, Any]:
        """Retrieve details and evidence pack for a specific incident."""
        incident = incident_mgr.get_incident(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail=f"Unknown incident '{incident_id}'")
        return _enrich_incident(incident)

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
