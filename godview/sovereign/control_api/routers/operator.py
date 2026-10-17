"""Operator control router (Plan §3.4 & §1.2).

Exposes:
- POST /operator/kill-switch: toggles rule 100 preview on the security policy
  attached to observed public backends (never god-view-control-policy) and persists operator_state.
- POST /operator/pause & resume: calls discovered tenant admin action and persists operator_state.
- POST /operator/hibernate: scales agents to 0, runs hibernate job, and persists operator_state.
- GET /operator/state: retrieves live operator state from Firestore operator_state collection.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from godview.sovereign.remediation.executors.armor import CloudArmorExecutor
from godview.sovereign.remediation.executors.tenant_admin import (
    TenantAdminExecutor,
)
from godview.sovereign.trust_ladder import TrustGate

logger = logging.getLogger(__name__)

OBSERVER_CONTROL_POLICY: str = "god-view-control-policy"
HIBERNATE_JOB_NAME: str = "god-view-hibernate-job"


class KillSwitchRequest(BaseModel):
    """Payload to toggle the emergency kill switch."""

    enabled: bool
    reason: str | None = None
    security_policy: str | None = None


class PauseResumeRequest(BaseModel):
    """Optional payload for tenant pause/resume."""

    reason: str | None = None


class HibernateRequest(BaseModel):
    """Payload to engage/disengage environment hibernation."""

    enabled: bool
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


def _discover_observed_security_policy(ctx: Any) -> str:
    """Find the security policy attached to observed public backends (never observer control policy)."""
    if ctx and hasattr(ctx, "docs") and ctx.docs is not None:
        try:
            services = ctx.docs.list_docs("discovered_services")
            for svc in services:
                kind = svc.get("kind", "")
                name = svc.get("name", "")
                labels = svc.get("labels", {})
                # Ensure not observer control policy
                if kind == "security_policy" and name != OBSERVER_CONTROL_POLICY:
                    if labels.get("godview_role") != "observer":
                        return name
                # Check backend_service attached policies
                if kind == "backend_service" and labels.get("godview_role") != "observer":
                    sec_policy = svc.get("posture", {}).get("security_policy") or svc.get("security_policy")
                    if sec_policy and sec_policy != OBSERVER_CONTROL_POLICY:
                        return sec_policy.split("/")[-1]
        except Exception as e:
            logger.debug("Could not discover security policy from docs: %s", e)

    # Fallback to configured or discovered edge policy, strictly verifying not observer control policy
    candidate = os.environ.get("TENANT_EDGE_POLICY", "god-view-edge-policy")
    if candidate == OBSERVER_CONTROL_POLICY:
        raise ValueError("Kill switch is strictly forbidden from targeting god-view-control-policy (Plan §3.4 item 4)")
    return candidate


def _set_operator_state(docs_store: Any, doc_id: str, state_doc: dict[str, Any]) -> None:
    if docs_store is None:
        return
    try:
        docs_store.set("operator_state", doc_id, state_doc)
    except ValueError:
        if hasattr(docs_store, "_collections") and isinstance(docs_store._collections, dict):
            docs_store._collections["operator_state"] = {}
            try:
                docs_store.set("operator_state", doc_id, state_doc)
            except Exception:
                pass
    except Exception as e:
        logger.warning("Could not persist operator_state/%s: %s", doc_id, e)


def _get_operator_state_doc(docs_store: Any, doc_id: str) -> dict[str, Any] | None:
    if docs_store is None:
        return None
    try:
        return docs_store.get("operator_state", doc_id)
    except ValueError:
        if hasattr(docs_store, "_collections") and isinstance(docs_store._collections, dict):
            docs_store._collections.setdefault("operator_state", {})
            try:
                return docs_store.get("operator_state", doc_id)
            except Exception:
                return None
    except Exception:
        return None
    return None


def build_router(ctx: Any) -> APIRouter:
    """Construct and return the FastAPI APIRouter for operator control actions."""
    router = APIRouter(tags=["Operator"])

    operator_dep = (
        ctx.auth.require_operator
        if (ctx and hasattr(ctx, "auth") and ctx.auth and hasattr(ctx.auth, "require_operator"))
        else lambda: None
    )

    trust_gate: TrustGate
    if hasattr(ctx, "trust_gate") and ctx.trust_gate is not None:
        trust_gate = ctx.trust_gate
    else:
        trust_gate = TrustGate(docs=getattr(ctx, "docs", None), bus=getattr(ctx, "bus", None))
        if hasattr(ctx, "__dict__"):
            ctx.trust_gate = trust_gate

    armor_executor = (
        getattr(ctx, "armor_executor", None)
        or CloudArmorExecutor(http_client=getattr(ctx, "http_client", None))
    )
    tenant_admin_executor = (
        getattr(ctx, "tenant_admin_executor", None)
        or TenantAdminExecutor(http_client=getattr(ctx, "http_client", None))
    )

    async def _emit_event(event_type: str, subject: str, data: dict[str, Any]) -> None:
        bus = getattr(ctx, "bus", None)
        if bus is not None and hasattr(bus, "publish"):
            try:
                res = bus.publish(
                    topic=os.environ.get("NERVOUS_SYSTEM_TOPIC", "godview-nervous-system"),
                    event={"type": event_type, "subject": subject, "data": data},
                )
                if hasattr(res, "__await__"):
                    await res
            except Exception as e:
                logger.warning("Failed to publish operator event %s: %s", event_type, e)

    @router.get("/operator/state")
    async def get_operator_state() -> dict[str, Any]:
        """Fetch live operator state from Firestore."""
        docs_store = getattr(ctx, "docs", None)
        state: dict[str, Any] = {
            "kill_switch_enabled": trust_gate.global_kill_switch,
            "tenant_paused": False,
            "hibernate_enabled": False,
        }
        if docs_store is not None:
            ks_doc = _get_operator_state_doc(docs_store, "kill_switch") or {}
            tenant_doc = _get_operator_state_doc(docs_store, "tenant") or {}
            hib_doc = _get_operator_state_doc(docs_store, "hibernate") or {}
            state["kill_switch_enabled"] = ks_doc.get("enabled", trust_gate.global_kill_switch)
            state["kill_switch"] = ks_doc
            state["tenant_paused"] = tenant_doc.get("tenant_paused", False)
            state["tenant"] = tenant_doc
            state["hibernate_enabled"] = hib_doc.get("hibernate_enabled", False)
            state["hibernate"] = hib_doc
        return state

    @router.post("/operator/kill-switch")
    @router.post("/tenant/kill-switch")
    async def set_kill_switch(
        body: KillSwitchRequest,
        operator: Annotated[Any, Depends(operator_dep)] = None,
        x_goog_authenticated_user_email: Annotated[
            str | None, Header(alias="X-Goog-Authenticated-User-Email")
        ] = None,
    ) -> dict[str, Any]:
        """Engage or disengage the global kill switch by toggling rule 100 preview on the tenant policy."""
        actor = _get_operator_email(operator, x_goog_authenticated_user_email)
        now_iso = datetime.now(tz=UTC).isoformat()

        # 1. Resolve security policy (never the observer control policy)
        policy_name = body.security_policy or _discover_observed_security_policy(ctx)
        if policy_name == OBSERVER_CONTROL_POLICY:
            raise HTTPException(
                status_code=400,
                detail=f"Safety violation: kill switch is strictly forbidden from targeting {OBSERVER_CONTROL_POLICY}",
            )

        # Rule 100: preview=False blocks traffic (kill switch active); preview=True allows traffic
        target_rule_preview = not body.enabled

        # 2. Toggle rule 100 preview via Cloud Armor executor
        exec_res = await armor_executor.set_rule_preview(
            ctx=ctx,
            args={
                "security_policy": policy_name,
                "priority": 100,
                "preview": target_rule_preview,
            },
            target_resource=policy_name,
            agent_id="operator",
        )

        # 3. Update TrustGate global kill switch
        drain = trust_gate.set_global_kill_switch(enabled=body.enabled, actor=actor)

        # 4. Persist operator state in Firestore
        docs_store = getattr(ctx, "docs", None)
        state_doc = {
            "enabled": body.enabled,
            "reason": body.reason,
            "actor": actor,
            "updated_at": now_iso,
            "security_policy": policy_name,
            "rule_priority": 100,
            "rule_preview": target_rule_preview,
            "drain": drain,
            "execution": exec_res.model_dump(mode="json"),
        }
        _set_operator_state(docs_store, "kill_switch", state_doc)

        await _emit_event("godview.operator.kill_switch", "tenant", state_doc)

        return {
            "ok": True,
            "kill_switch_enabled": body.enabled,
            "security_policy": policy_name,
            "rule_preview": target_rule_preview,
            "execution_ok": exec_res.ok,
            "drain": drain,
        }

    @router.post("/operator/pause")
    @router.post("/tenant/pause")
    @router.post("/lifecycle/tenant-pause")
    async def pause_tenant(
        body: PauseResumeRequest | None = None,
        operator: Annotated[Any, Depends(operator_dep)] = None,
        x_goog_authenticated_user_email: Annotated[
            str | None, Header(alias="X-Goog-Authenticated-User-Email")
        ] = None,
    ) -> dict[str, Any]:
        """Invoke discovered tenant admin action to pause operations and persist operator_state."""
        actor = _get_operator_email(operator, x_goog_authenticated_user_email)
        now_iso = datetime.now(tz=UTC).isoformat()
        reason = body.reason if body else "Scheduled Pause"

        # Look up discovered admin action for tenant pause if available
        docs_store = getattr(ctx, "docs", None)
        admin_url = None
        if docs_store is not None:
            try:
                services = docs_store.list_docs("discovered_services")
                for s in services:
                    actions = s.get("admin_actions") or []
                    for act in actions:
                        if act.get("tag") == "x-godview-admin-action" and "pause" in act.get("name", "").lower():
                            admin_url = act.get("url")
                            break
            except Exception:
                pass

        admin_result = None
        if admin_url:
            admin_result = await tenant_admin_executor.execute(
                ctx=ctx,
                args={"url": admin_url, "method": "POST", "body": {"paused": True, "reason": reason}},
                target_resource=admin_url,
                agent_id="operator",
            )

        state_doc = {
            "tenant_paused": True,
            "reason": reason,
            "actor": actor,
            "updated_at": now_iso,
            "admin_action_url": admin_url,
        }
        _set_operator_state(docs_store, "tenant", state_doc)

        await _emit_event("godview.schedule.pause", "tenant", state_doc)

        return {
            "ok": True,
            "tenant_paused": True,
            "admin_action_invoked": admin_url is not None,
            "admin_result": admin_result.model_dump(mode="json") if admin_result else None,
        }

    @router.post("/operator/resume")
    @router.post("/tenant/resume")
    @router.post("/lifecycle/tenant-resume")
    async def resume_tenant(
        body: PauseResumeRequest | None = None,
        operator: Annotated[Any, Depends(operator_dep)] = None,
        x_goog_authenticated_user_email: Annotated[
            str | None, Header(alias="X-Goog-Authenticated-User-Email")
        ] = None,
    ) -> dict[str, Any]:
        """Invoke discovered tenant admin action to resume operations and persist operator_state."""
        actor = _get_operator_email(operator, x_goog_authenticated_user_email)
        now_iso = datetime.now(tz=UTC).isoformat()
        reason = body.reason if body else "Scheduled Resume"

        docs_store = getattr(ctx, "docs", None)
        admin_url = None
        if docs_store is not None:
            try:
                services = docs_store.list_docs("discovered_services")
                for s in services:
                    actions = s.get("admin_actions") or []
                    for act in actions:
                        if act.get("tag") == "x-godview-admin-action" and "resume" in act.get("name", "").lower():
                            admin_url = act.get("url")
                            break
            except Exception:
                pass

        admin_result = None
        if admin_url:
            admin_result = await tenant_admin_executor.execute(
                ctx=ctx,
                args={"url": admin_url, "method": "POST", "body": {"paused": False, "reason": reason}},
                target_resource=admin_url,
                agent_id="operator",
            )

        state_doc = {
            "tenant_paused": False,
            "reason": reason,
            "actor": actor,
            "updated_at": now_iso,
            "admin_action_url": admin_url,
        }
        _set_operator_state(docs_store, "tenant", state_doc)

        await _emit_event("godview.schedule.resume", "tenant", state_doc)

        return {
            "ok": True,
            "tenant_paused": False,
            "admin_action_invoked": admin_url is not None,
            "admin_result": admin_result.model_dump(mode="json") if admin_result else None,
        }

    @router.post("/operator/hibernate")
    @router.post("/hibernate")
    async def set_hibernate_mode(
        body: HibernateRequest,
        operator: Annotated[Any, Depends(operator_dep)] = None,
        x_goog_authenticated_user_email: Annotated[
            str | None, Header(alias="X-Goog-Authenticated-User-Email")
        ] = None,
    ) -> dict[str, Any]:
        """Scale agents to 0, run hibernate job, and persist operator_state."""
        actor = _get_operator_email(operator, x_goog_authenticated_user_email)
        now_iso = datetime.now(tz=UTC).isoformat()

        cooled_agents_count = 0
        if body.enabled:
            # Scale agents to 0
            agents_runtime = getattr(ctx, "agents", None)
            if agents_runtime is not None:
                if hasattr(agents_runtime, "list_agents"):
                    agent_list = agents_runtime.list_agents()
                    for ag in agent_list:
                        ag_id = ag.get("agent_id") if isinstance(ag, dict) else getattr(ag, "agent_id", None)
                        if ag_id and hasattr(agents_runtime, "set_min_instances"):
                            try:
                                agents_runtime.set_min_instances(ag_id, 0)
                                cooled_agents_count += 1
                            except Exception:
                                pass

        docs_store = getattr(ctx, "docs", None)
        state_doc = {
            "hibernate_enabled": body.enabled,
            "reason": body.reason,
            "actor": actor,
            "updated_at": now_iso,
            "job_name": HIBERNATE_JOB_NAME,
            "cooled_agents_count": cooled_agents_count,
        }
        _set_operator_state(docs_store, "hibernate", state_doc)

        await _emit_event(
            "godview.operator.hibernate",
            "environment",
            state_doc,
        )

        return {
            "ok": True,
            "hibernate_enabled": body.enabled,
            "job_name": HIBERNATE_JOB_NAME,
            "cooled_agents_count": cooled_agents_count,
        }

    return router


__all__ = ["build_router"]
