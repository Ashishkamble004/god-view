"""Approvals router (Plan §3.4 & §1.2).

Exposes:
- GET /approvals: list approval cards (pending, decided, expired)
- GET /approvals/{approval_id}: get approval card details
- POST /approvals: create an approval request card (TTL 30 min)
- POST /approvals/{approval_id}/approve: approve card with IAP operator identity
- POST /approvals/{approval_id}/reject: reject card with IAP operator identity
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from godview_shared.contracts import ApprovalCard, TrustViolation
from pydantic import BaseModel, Field

from godview.sovereign.trust_ladder import TrustGate

logger = logging.getLogger(__name__)


class ApprovalCreateRequest(BaseModel):
    """Payload to create an approval card."""

    incident_id: str
    agent_id: str
    capability_id: str
    target_resource: str
    trust_level_required: int = Field(default=2, ge=1, le=4)
    narrative: str
    evidence_pack_id: str = "evp-remediation"
    rupee_impact: float = 0.0
    reversal_recipe: dict[str, Any] | None = None


class ApprovalDecisionRequest(BaseModel):
    """Optional payload when deciding an approval."""

    reason: str | None = None


def _get_operator_email(operator: Any, header_email: str | None) -> str:
    if operator is not None:
        if isinstance(operator, str):
            email = operator
        elif hasattr(operator, "email"):
            email = str(operator.email)
        elif hasattr(operator, "principal"):
            email = str(operator.principal)
        else:
            email = str(operator)
        clean = email.split(":")[-1].strip()
        if clean:
            return clean

    if header_email:
        clean = header_email.split(":")[-1].strip()
        if clean:
            return clean

    return "operator@local.dev"


def build_router(ctx: Any) -> APIRouter:
    """Construct and return the FastAPI APIRouter for approvals."""
    router = APIRouter(tags=["Approvals"])

    # Determine auth dependency
    operator_dep = (
        ctx.auth.require_operator
        if (ctx and hasattr(ctx, "auth") and ctx.auth and hasattr(ctx.auth, "require_operator"))
        else lambda: None
    )

    # Initialize TrustGate instance attached to ctx
    trust_gate: TrustGate
    if hasattr(ctx, "trust_gate") and ctx.trust_gate is not None:
        trust_gate = ctx.trust_gate
    else:
        trust_gate = TrustGate(docs=getattr(ctx, "docs", None), bus=getattr(ctx, "bus", None))
        if hasattr(ctx, "__dict__"):
            ctx.trust_gate = trust_gate

    @router.get("/approvals")
    async def list_approvals(
        status: Literal["pending", "approved", "rejected", "snoozed", "expired"] | None = Query(
            default=None
        ),
        incident_id: str | None = Query(default=None),
        agent_id: str | None = Query(default=None),
    ) -> list[dict[str, Any]]:
        """List persisted approval cards, auto-expiring overdue pending items."""
        docs_store = getattr(ctx, "docs", None)
        if docs_store is None:
            return []

        where_filter: dict[str, Any] = {}
        if status:
            where_filter["decision"] = status
        if incident_id:
            where_filter["incident_id"] = incident_id
        if agent_id:
            where_filter["agent_id"] = agent_id

        raw_docs = docs_store.list_docs("approvals", where=where_filter if where_filter else None)
        now = datetime.now(tz=UTC)
        cards: list[dict[str, Any]] = []

        for item in raw_docs:
            # Check expiry for pending cards
            if item.get("decision") == "pending":
                exp_str = item.get("expires_at")
                if exp_str:
                    try:
                        exp_dt = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
                        if exp_dt.tzinfo is None:
                            exp_dt = exp_dt.replace(tzinfo=UTC)
                        if now >= exp_dt:
                            item["decision"] = "expired"
                            item["decided_at"] = now.isoformat()
                            item["decided_by"] = "system:ttl-expiry"
                            docs_store.set("approvals", item["approval_id"], item, merge=True)
                    except Exception:
                        pass
            cards.append(item)

        # Sort descending by requested_at
        cards.sort(key=lambda c: str(c.get("requested_at", "")), reverse=True)
        return cards

    @router.get("/approvals/{approval_id}")
    async def get_approval(approval_id: str) -> dict[str, Any]:
        """Fetch single approval card by ID."""
        docs_store = getattr(ctx, "docs", None)
        if docs_store is None:
            raise HTTPException(status_code=404, detail="No document store configured")
        raw = docs_store.get("approvals", approval_id)
        if raw is None:
            raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found")
        return raw

    @router.post("/approvals")
    async def create_approval(body: ApprovalCreateRequest) -> dict[str, Any]:
        """Create an approval request card (TTL 30 min)."""
        card: ApprovalCard = trust_gate.create_approval_request(
            incident_id=body.incident_id,
            agent_id=body.agent_id,
            capability_id=body.capability_id,
            target_resource=body.target_resource,
            trust_level_required=body.trust_level_required,
            narrative=body.narrative,
            evidence_pack_id=body.evidence_pack_id,
            rupee_impact=body.rupee_impact,
            reversal_recipe=body.reversal_recipe,
        )
        return card.model_dump(mode="json")

    @router.post("/approvals/{approval_id}/approve")
    async def approve_card(
        approval_id: str,
        body: ApprovalDecisionRequest | None = None,
        operator: Annotated[Any, Depends(operator_dep)] = None,
        x_goog_authenticated_user_email: Annotated[
            str | None, Header(alias="X-Goog-Authenticated-User-Email")
        ] = None,
    ) -> dict[str, Any]:
        """Approve an approval card with operator identity."""
        actor = _get_operator_email(operator, x_goog_authenticated_user_email)
        try:
            updated_card, audit = trust_gate.decide_approval(
                approval_id,
                decision="approved",
                decided_by=actor,
            )
            return {
                "ok": True,
                "approval": updated_card.model_dump(mode="json"),
                "audit": audit.model_dump(mode="json"),
            }
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found") from None
        except TrustViolation as tv:
            raise HTTPException(status_code=400, detail=str(tv)) from None

    @router.post("/approvals/{approval_id}/reject")
    async def reject_card(
        approval_id: str,
        body: ApprovalDecisionRequest | None = None,
        operator: Annotated[Any, Depends(operator_dep)] = None,
        x_goog_authenticated_user_email: Annotated[
            str | None, Header(alias="X-Goog-Authenticated-User-Email")
        ] = None,
    ) -> dict[str, Any]:
        """Reject an approval card with operator identity."""
        actor = _get_operator_email(operator, x_goog_authenticated_user_email)
        try:
            updated_card, audit = trust_gate.decide_approval(
                approval_id,
                decision="rejected",
                decided_by=actor,
            )
            return {
                "ok": True,
                "approval": updated_card.model_dump(mode="json"),
                "audit": audit.model_dump(mode="json"),
            }
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found") from None
        except TrustViolation as tv:
            raise HTTPException(status_code=400, detail=str(tv)) from None

    return router


__all__ = ["build_router"]
