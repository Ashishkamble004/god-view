"""Approvals router (Plan §3.4 & §1.2).

Exposes:
- GET /approvals: list approval cards (auto-expiring overdue items)
- GET /approvals/{approval_id}: fetch approval card by ID
- POST /approvals: create an approval request card (TTL 30 min, single use)
- POST /approvals/{approval_id}/approve & :approve: approve card with operator identity,
  immediately executing the approved invocation as godview-act-sa, verifying effect,
  persisting the execution result, and closing associated incident.
- POST /approvals/{approval_id}/reject & :reject: reject card with operator identity
"""

from __future__ import annotations

import inspect
import logging
import os
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from godview_shared.contracts import ApprovalCard, TrustViolation
from pydantic import BaseModel, Field

from godview.sovereign.remediation.registry import RemediationDispatcher
from godview.sovereign.remediation.types import ExecutionResult
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
    args: dict[str, Any] = Field(default_factory=dict)
    verification_plan: list[dict[str, Any]] | list[str] | None = None
    evidence_pack_id: str = "evp-remediation"
    financial_impact: float = 0.0
    reversal_recipe: dict[str, Any] | None = None


class ApprovalDecisionRequest(BaseModel):
    """Optional payload when deciding an approval."""

    reason: str | None = None


def _get_operator_email(operator: Any) -> str:
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

    raise HTTPException(status_code=401, detail="Cannot determine authenticated operator email")


def _get_env_mode(ctx: Any) -> str:
    settings = getattr(ctx, "settings", None)
    if settings is not None and hasattr(settings, "env_mode"):
        return str(settings.env_mode)
    if hasattr(ctx, "env_mode") and ctx.env_mode:
        return str(ctx.env_mode)
    return os.environ.get("GODVIEW_ENV", "gcp")


def build_router(ctx: Any) -> APIRouter:
    """Construct and return the FastAPI APIRouter for approvals."""
    router = APIRouter(tags=["Approvals"])

    async def operator_dep(
        request: Request,
    ) -> Any:
        if ctx and hasattr(ctx, "auth") and ctx.auth is not None:
            require_op = getattr(ctx.auth, "require_operator", None)
            if callable(require_op):
                try:
                    return await require_op(request)
                except HTTPException as he:
                    logger.warning("require_operator raised HTTPException: status=%s detail=%s", he.status_code, he.detail)
                    if _get_env_mode(ctx) != "hermetic":
                        raise
                except Exception as exc:
                    logger.error("require_operator unexpected exception in approvals router (%s): %s", type(exc).__name__, exc, exc_info=True)
                    if _get_env_mode(ctx) != "hermetic":
                        raise
        env_mode = _get_env_mode(ctx)
        if env_mode == "hermetic":
            header_email = (
                request.headers.get("x-goog-authenticated-user-email")
                or request.headers.get("x-godview-test-principal")
                or "operator@local.hermetic"
            )
            return _get_operator_email(header_email)
        raise HTTPException(status_code=401, detail="Unauthorized: valid operator identity required")

    async def service_or_operator_dep(
        request: Request,
    ) -> Any:
        if ctx and hasattr(ctx, "auth") and ctx.auth is not None:
            verify_iap = getattr(ctx.auth, "verify_iap_jwt", None)
            if callable(verify_iap) and verify_iap(request):
                return verify_iap(request)
            verify_oidc = getattr(ctx.auth, "verify_google_oidc", None)
            if callable(verify_oidc) and verify_oidc(request):
                return verify_oidc(request)
            require_svc = getattr(ctx.auth, "require_service", None)
            if callable(require_svc):
                try:
                    return await require_svc(request)
                except HTTPException:
                    pass
        env_mode = _get_env_mode(ctx)
        if env_mode == "hermetic":
            header_email = (
                request.headers.get("x-goog-authenticated-user-email")
                or request.headers.get("x-godview-test-principal")
                or "service@local.hermetic"
            )
            return _get_operator_email(header_email)
        # In live mode without credentials, fail closed
        raise HTTPException(status_code=401, detail="Unauthorized: authenticated service or operator required")

    # Initialize or reuse TrustGate
    trust_gate: TrustGate
    if hasattr(ctx, "trust_gate") and ctx.trust_gate is not None:
        trust_gate = ctx.trust_gate
    else:
        trust_gate = TrustGate(docs=getattr(ctx, "docs", None), bus=getattr(ctx, "bus", None))
        if hasattr(ctx, "__dict__"):
            ctx.trust_gate = trust_gate

    # Initialize or reuse RemediationDispatcher
    dispatcher: RemediationDispatcher
    if hasattr(ctx, "dispatcher") and ctx.dispatcher is not None:
        dispatcher = ctx.dispatcher
    else:
        dispatcher = RemediationDispatcher()
        if hasattr(ctx, "__dict__"):
            ctx.dispatcher = dispatcher

    async def _trigger_approval_decided_hook(card: ApprovalCard) -> None:
        hooks = getattr(ctx, "hooks", None)
        if hooks is None:
            return
        listeners = getattr(hooks, "on_approval_decided", None)
        if not listeners:
            return
        if hasattr(listeners, "__iter__") and not isinstance(listeners, (str, bytes, dict)):
            cb_list = list(listeners)
        elif callable(listeners):
            cb_list = [listeners]
        else:
            cb_list = []
        for listener in cb_list:
            try:
                res = listener(card)
                if inspect.isawaitable(res):
                    await res
            except Exception as e:
                logger.warning("Error invoking on_approval_decided hook: %s", e)

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
    async def create_approval(
        body: ApprovalCreateRequest,
        caller: Any = Depends(service_or_operator_dep),  # noqa: B008
    ) -> dict[str, Any]:
        """Create an approval request card (TTL 30 min, single use)."""
        card: ApprovalCard = trust_gate.create_approval_request(
            incident_id=body.incident_id,
            agent_id=body.agent_id,
            capability_id=body.capability_id,
            target_resource=body.target_resource,
            trust_level_required=body.trust_level_required,
            narrative=body.narrative,
            evidence_pack_id=body.evidence_pack_id,
            financial_impact=body.financial_impact,
            reversal_recipe=body.reversal_recipe,
            args=body.args,
            verification_plan=body.verification_plan,
        )
        return card.model_dump(mode="json")

    @router.post("/approvals/{approval_id}/approve")
    @router.post("/approvals/{approval_id}:approve")
    async def approve_card(
        approval_id: str,
        body: ApprovalDecisionRequest | None = None,
        operator: Any = Depends(operator_dep),  # noqa: B008
    ) -> dict[str, Any]:
        """Approve an approval card with operator identity, then immediately execute the approved remediation."""
        actor = _get_operator_email(operator)
        try:
            updated_card, audit = trust_gate.decide_approval(
                approval_id,
                decision="approved",
                decided_by=actor,
            )

            await _trigger_approval_decided_hook(updated_card)

            # Execute the approved remediation using the card's stored parameters
            card_args = getattr(updated_card, "args", {}) or {}
            exec_result: ExecutionResult | None = None
            try:
                exec_result = await dispatcher.invoke(
                    ctx=ctx,
                    trust_gate=trust_gate,
                    capability_id=updated_card.capability,
                    target_resource=updated_card.target_resource,
                    args=card_args,
                    agent_id=updated_card.agent_id,
                    approval_id=approval_id,
                )
            except Exception as exec_err:
                logger.error("Remediation execution failed after approval: %s", exec_err)
                exec_result = ExecutionResult(
                    ok=False,
                    before={},
                    after={},
                    verification=[],
                    error=str(exec_err),
                )

            # Record trust credit strictly after verified execution (Coordinator #95, Verifier-Core #129 item 5)
            if exec_result.ok:
                try:
                    trust_gate.record_recommendation_correct(updated_card.agent_id, updated_card.capability)
                except Exception as e:
                    logger.warning("Failed to record recommendation correct in TrustGate: %s", e)

            # Write ExecutionResult onto the card in docs store
            docs_store = getattr(ctx, "docs", None)
            card_dict = updated_card.model_dump(mode="json")
            card_dict["execution_result"] = exec_result.model_dump(mode="json")
            card_dict["executed_at"] = datetime.now(tz=UTC).isoformat()
            card_dict["audit_id"] = getattr(audit, "audit_id", None) or getattr(audit, "doc_id", "aud-approved")
            if docs_store is not None:
                docs_store.set("approvals", approval_id, card_dict, merge=True)

            # If execution verified and card has incident_id, resolve incident
            if exec_result.ok and updated_card.incident_id:
                incident_mgr = getattr(ctx, "incident_manager", None) or getattr(ctx, "incidents", None)
                if incident_mgr and hasattr(incident_mgr, "close_incident"):
                    try:
                        res = incident_mgr.close_incident(
                            updated_card.incident_id,
                            reason=f"Remediation executed and verified: {updated_card.narrative}",
                            outcome="resolved",
                            operator=actor,
                            remediation_capability=updated_card.capability,
                            resolution_summary=f"Remediation {updated_card.capability} verified on {updated_card.target_resource}",
                        )
                        if inspect.isawaitable(res):
                            await res
                    except Exception as inc_err:
                        logger.warning("Failed to close incident %s: %s", updated_card.incident_id, inc_err)

            return {
                "ok": exec_result.ok,
                "decision": "approved",
                "decided_by": actor,
                "audit_id": getattr(audit, "audit_id", None) or getattr(audit, "doc_id", "aud-approved"),
                "approval": card_dict,
                "execution_result": exec_result.model_dump(mode="json"),
                "audit": audit.model_dump(mode="json"),
            }
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found") from None
        except TrustViolation as tv:
            raise HTTPException(status_code=400, detail=str(tv)) from None

    @router.post("/approvals/{approval_id}/reject")
    @router.post("/approvals/{approval_id}:reject")
    async def reject_card(
        approval_id: str,
        body: ApprovalDecisionRequest | None = None,
        operator: Any = Depends(operator_dep),  # noqa: B008
    ) -> dict[str, Any]:
        """Reject an approval card with operator identity."""
        actor = _get_operator_email(operator)
        try:
            updated_card, audit = trust_gate.decide_approval(
                approval_id,
                decision="rejected",
                decided_by=actor,
            )

            await _trigger_approval_decided_hook(updated_card)
            return {
                "ok": True,
                "decision": "rejected",
                "approval": updated_card.model_dump(mode="json"),
                "audit": audit.model_dump(mode="json"),
            }
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found") from None
        except TrustViolation as tv:
            raise HTTPException(status_code=400, detail=str(tv)) from None

    @router.post("/approvals/{approval_id}/snooze")
    @router.post("/approvals/{approval_id}:snooze")
    async def snooze_card(
        approval_id: str,
        body: ApprovalDecisionRequest | None = None,
        operator: Any = Depends(operator_dep),  # noqa: B008
    ) -> dict[str, Any]:
        """Snooze an approval card with operator identity."""
        actor = _get_operator_email(operator)
        try:
            updated_card, audit = trust_gate.decide_approval(
                approval_id,
                decision="snoozed",
                decided_by=actor,
            )

            await _trigger_approval_decided_hook(updated_card)
            return {
                "ok": True,
                "decision": "snoozed",
                "approval": updated_card.model_dump(mode="json"),
                "audit": audit.model_dump(mode="json"),
            }
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found") from None
        except TrustViolation as tv:
            raise HTTPException(status_code=400, detail=str(tv)) from None

    return router


__all__ = ["build_router"]
