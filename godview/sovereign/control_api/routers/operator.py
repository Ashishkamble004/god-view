"""Operator controls router (Plan §3.4 & §1.2).

Exposes:
- POST /operator/kill-switch: toggle preview on Cloud Armor rule attached to observed public backends
  (never observer's god-view-control-policy); 502 on execution failure; publishes NervousSystemEvent.
- POST /operator/pause & /operator/resume: invokes discovered tenant admin action; 409 if no action
  discovered; 502 on execution failure; persists operator_state in docs; publishes NervousSystemEvent.
- POST /operator/hibernate: triggers Cloud Scheduler hibernate job via act SA and scales agents to 0;
  restores agents to 1 when disabled; 502 on failure; persists operator_state in docs; publishes NervousSystemEvent.
- GET /operator/state: inspect active operator controls state.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from godview_shared.contracts import NervousSystemEvent
from pydantic import BaseModel

from godview.sovereign.remediation.executors.agent import AgentScalingExecutor
from godview.sovereign.remediation.executors.armor import CloudArmorExecutor
from godview.sovereign.remediation.executors.tenant_admin import TenantAdminExecutor
from godview.sovereign.remediation.impersonation import get_act_sa_access_token
from godview.sovereign.trust_ladder import TrustGate

logger = logging.getLogger(__name__)

OBSERVER_CONTROL_POLICY: str = "god-view-control-policy"
SCHEDULER_API_BASE: str = "https://cloudscheduler.googleapis.com/v1"


class KillSwitchRequest(BaseModel):
    """Payload to toggle the Cloud Armor kill switch."""

    enabled: bool
    security_policy: str | None = None
    priority: int | None = None
    reason: str | None = None


class PauseRequest(BaseModel):
    """Payload to pause/resume the tenant via discovered admin action."""

    reason: str | None = None
    source: str | None = None


class HibernateRequest(BaseModel):
    """Payload to hibernate the platform environment."""

    enabled: bool
    job_name: str | None = None
    reason: str | None = None


def _get_operator_email(operator: Any) -> str:
    if operator is None:
        raise HTTPException(status_code=401, detail="Unauthorized: valid operator identity required")
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
    raise HTTPException(status_code=401, detail="Unauthorized: valid operator identity required")


def _get_env_mode(ctx: Any) -> str:
    return (
        getattr(getattr(ctx, "settings", None), "env_mode", None)
        or getattr(ctx, "env_mode", None)
        or os.environ.get("GODVIEW_ENV", "gcp")
    )


def _discover_observed_security_policy(ctx: Any) -> str:
    """Find the security policy attached to observed public backends (never observer control policy)."""
    # 1. Check ctx.discovered_services attribute or ctx.docs
    candidates = []
    if ctx and hasattr(ctx, "discovered_services") and ctx.discovered_services:
        if isinstance(ctx.discovered_services, list):
            candidates.extend(ctx.discovered_services)
        elif isinstance(ctx.discovered_services, dict):
            candidates.extend(ctx.discovered_services.values())

    if ctx and hasattr(ctx, "docs") and ctx.docs is not None:
        try:
            candidates.extend(ctx.docs.list_docs("discovered_services"))
        except Exception:
            pass

    for svc in candidates:
        if not isinstance(svc, dict):
            continue
        kind = svc.get("kind", "")
        name = svc.get("name", "")
        labels = svc.get("labels", {})
        if labels.get("godview_role") == "observer":
            continue

        if kind == "security_policy" and name != OBSERVER_CONTROL_POLICY:
            return name
        if kind == "backend_service":
            sec_policy = svc.get("posture", {}).get("security_policy") or svc.get("security_policy")
            if sec_policy and sec_policy != OBSERVER_CONTROL_POLICY:
                return sec_policy.split("/")[-1]

    # 2. Check environment variable override
    env_policy = os.environ.get("TENANT_EDGE_POLICY")
    if env_policy:
        if env_policy == OBSERVER_CONTROL_POLICY:
            raise ValueError("Kill switch is strictly forbidden from targeting god-view-control-policy (Plan §3.4 item 4)")
        return env_policy

    # In hermetic mode without discovered services, use hermetic test policy name
    env_mode = _get_env_mode(ctx)
    if env_mode == "hermetic":
        return "hermetic-edge-policy"

    raise HTTPException(status_code=409, detail="No observed public security policy discovered for kill switch")


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

    async def operator_dep(
        request: Request,
    ) -> Any:
        if ctx and hasattr(ctx, "auth") and ctx.auth is not None:
            require_op = getattr(ctx.auth, "require_operator_identity", None) or getattr(
                ctx.auth, "require_operator", None
            )
            if callable(require_op):
                try:
                    op = await require_op(request)
                    if op is not None:
                        return op.email if hasattr(op, "email") else str(op)
                except HTTPException:
                    if _get_env_mode(ctx) != "hermetic":
                        raise

        # Check Bearer ID token directly
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            raw_tok = auth_header.split(" ", 1)[1].strip()
            try:
                from google.auth.transport import requests as google_requests
                from google.oauth2 import id_token as google_id_token

                claims = google_id_token.verify_oauth2_token(
                    raw_tok, google_requests.Request()
                )
                if claims and claims.get("iss") in (
                    "https://accounts.google.com",
                    "accounts.google.com",
                ):
                    email = str(claims.get("email") or "").strip().lower()
                    if email.endswith(".gserviceaccount.com"):
                        raise HTTPException(
                            status_code=403,
                            detail="Forbidden: Service accounts cannot act as human operators",
                        )
                    if not claims.get("email_verified", True):
                        raise HTTPException(
                            status_code=401,
                            detail="Unauthorized: Email must be verified",
                        )
                    allowed_ops = {
                        s.strip().lower()
                        for s in (
                            os.environ.get("GODVIEW_OPERATOR_EMAILS", "")
                            or os.environ.get("GODVIEW_OPERATOR_EMAIL", "")
                        ).split(",")
                        if s.strip()
                    }
                    if allowed_ops and email not in allowed_ops:
                        raise HTTPException(
                            status_code=403,
                            detail=f"Forbidden: Operator '{email}' is not in the operator allowlist",
                        )
                    if email:
                        return str(email)
            except HTTPException:
                raise
            except Exception:
                pass

        env_mode = _get_env_mode(ctx)
        if env_mode == "hermetic":
            header_email = (
                request.headers.get("x-goog-authenticated-user-email")
                or request.headers.get("x-godview-test-principal")
                or "operator@local.hermetic"
            )
            return header_email.split(":")[-1].strip()
        raise HTTPException(status_code=401, detail="Unauthorized: valid operator identity required")

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
    agent_executor = (
        getattr(ctx, "agent_executor", None)
        or AgentScalingExecutor()
    )

    async def _emit_event(event_type: str, subject: str, data: dict[str, Any]) -> None:
        bus = getattr(ctx, "bus", None)
        if bus is not None and hasattr(bus, "publish"):
            try:
                now = datetime.now(tz=UTC)
                event_obj = NervousSystemEvent(
                    specversion="1.0",
                    type=event_type,
                    source="godview/sovereign/operator",
                    id=f"evt-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}",
                    time=now.isoformat(),
                    subject=subject,
                    data=data,
                )
                topic = os.environ.get("NERVOUS_SYSTEM_TOPIC", "godview-nervous-system")
                pub_res = bus.publish(topic=topic, event=event_obj.model_dump(mode="json"))
                if hasattr(pub_res, "__await__"):
                    await pub_res
            except Exception as e:
                logger.warning("Could not publish nervous system operator event: %s", e)

    @router.get("/operator/state")
    async def get_operator_state(
        operator: Any = Depends(operator_dep),  # noqa: B008
    ) -> dict[str, Any]:
        """Fetch current operator controls state (kill switch, pause, hibernate)."""
        docs_store = getattr(ctx, "docs", None)
        doc = _get_operator_state_doc(docs_store, "current") if docs_store is not None else None
        if doc:
            return doc
        return {
            "kill_switch_enabled": getattr(trust_gate, "global_kill_switch", False),
            "tenant_paused": False,
            "hibernate_enabled": False,
            "updated_at": datetime.now(tz=UTC).isoformat(),
            "updated_by": "system:initial",
        }

    @router.post("/operator/kill-switch")
    @router.post("/tenant/kill-switch")
    async def toggle_kill_switch(
        body: KillSwitchRequest,
        operator: Any = Depends(operator_dep),  # noqa: B008
    ) -> dict[str, Any]:
        """Toggle kill switch by adjusting Cloud Armor rule preview on observed public backend."""
        actor = _get_operator_email(operator)

        # Strictly discover observed policy; forbidden to target observer control policy
        target_policy = body.security_policy or _discover_observed_security_policy(ctx)
        if target_policy == OBSERVER_CONTROL_POLICY:
            raise HTTPException(
                status_code=403,
                detail=f"Targeting '{OBSERVER_CONTROL_POLICY}' with kill switch is strictly forbidden",
            )

        # When kill switch is enabled, preview is FALSE (rule enforces 403 hard deny)
        preview_mode = not body.enabled
        exec_res = await armor_executor.set_rule_preview(
            ctx=ctx,
            args={
                "preview": preview_mode,
                "priority": body.priority or 0,
            },
            target_resource=target_policy,
            agent_id="operator",
        )

        if not exec_res.ok:
            raise HTTPException(
                status_code=502,
                detail=f"Cloud Armor kill switch execution failed: {exec_res.error}",
            )

        # Update TrustGate in-memory kill switch flag
        trust_gate.global_kill_switch = body.enabled

        now_iso = datetime.now(tz=UTC).isoformat()
        docs_store = getattr(ctx, "docs", None)
        state_doc = _get_operator_state_doc(docs_store, "current") or {}
        state_doc.update(
            {
                "kill_switch_enabled": body.enabled,
                "kill_switch_target_policy": target_policy,
                "kill_switch_updated_at": now_iso,
                "kill_switch_updated_by": actor,
                "kill_switch_reason": body.reason,
            }
        )
        _set_operator_state(docs_store, "current", state_doc)

        await _emit_event(
            event_type="godview.operator.kill_switch",
            subject=target_policy,
            data={
                "enabled": body.enabled,
                "target_policy": target_policy,
                "operator": actor,
                "reason": body.reason,
            },
        )

        return {
            "ok": True,
            "kill_switch_enabled": body.enabled,
            "target_policy": target_policy,
            "execution": exec_res.model_dump(mode="json"),
        }

    @router.post("/operator/pause")
    @router.post("/tenant/pause")
    @router.post("/lifecycle/tenant-pause")
    async def pause_tenant(
        body: PauseRequest | None = None,
        operator: Any = Depends(operator_dep),  # noqa: B008
    ) -> dict[str, Any]:
        """Pause tenant operations via discovered OpenAPI admin action."""
        actor = _get_operator_email(operator)
        reason = body.reason if body else "operator pause"
        env_mode = _get_env_mode(ctx)

        # Try to invoke discovered tenant admin action for pause
        admin_result = None
        try:
            admin_result = await tenant_admin_executor.execute(
                ctx=ctx,
                args={"action_name": "tenant.pause", "operation_id": "pause", "body": {"reason": reason}},
                target_resource="tenant.pause",
                agent_id="operator",
            )
        except Exception as e:
            if env_mode != "hermetic":
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot pause tenant: no discovered admin action 'tenant.pause' available: {e}",
                ) from None

        if admin_result is not None and not admin_result.ok:
            raise HTTPException(
                status_code=502,
                detail=f"Tenant administrative pause operation failed: {admin_result.error}",
            )

        now_iso = datetime.now(tz=UTC).isoformat()
        docs_store = getattr(ctx, "docs", None)
        state_doc = _get_operator_state_doc(docs_store, "current") or {}
        state_doc.update(
            {
                "tenant_paused": True,
                "simulator_enabled": False,
                "tenant_paused_at": now_iso,
                "tenant_paused_by": actor,
                "pause_reason": reason,
            }
        )
        _set_operator_state(docs_store, "current", state_doc)

        if hasattr(ctx, "operator_state") and isinstance(ctx.operator_state, dict):
            ctx.operator_state["tenant_paused"] = True
            ctx.operator_state["simulator_enabled"] = False

        sentinel_eng = getattr(ctx, "sentinel_engine", None)
        if sentinel_eng is not None and hasattr(sentinel_eng, "set_planned_operator_action"):
            try:
                sentinel_eng.set_planned_operator_action(active=True, reason=reason)
            except Exception as e:
                logger.warning("Could not set planned operator action in sentinel engine: %s", e)

        court_mgr = getattr(ctx, "court_manager", None)
        if court_mgr is not None and hasattr(court_mgr, "cool_all_agents"):
            try:
                res = court_mgr.cool_all_agents()
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.warning("Could not cool agents during tenant pause: %s", e)

        await _emit_event(
            event_type="godview.schedule.pause",
            subject="tenant",
            data={"action": "pause", "operator": actor, "reason": reason, "tenant_paused": True, "warm_agents_count": 0},
        )

        return {
            "ok": True,
            "tenant_paused": True,
            "warm_agents_count": 0,
            "admin_action_invoked": admin_result is not None,
            "result": admin_result.model_dump(mode="json") if admin_result else None,
        }

    @router.post("/operator/resume")
    @router.post("/tenant/resume")
    @router.post("/lifecycle/tenant-resume")
    async def resume_tenant(
        body: PauseRequest | None = None,
        operator: Any = Depends(operator_dep),  # noqa: B008
    ) -> dict[str, Any]:
        """Resume tenant operations via discovered OpenAPI admin action."""
        actor = _get_operator_email(operator)
        reason = body.reason if body else "operator resume"
        env_mode = _get_env_mode(ctx)

        admin_result = None
        try:
            admin_result = await tenant_admin_executor.execute(
                ctx=ctx,
                args={"action_name": "tenant.resume", "operation_id": "resume", "body": {"reason": reason}},
                target_resource="tenant.resume",
                agent_id="operator",
            )
        except Exception as e:
            if env_mode != "hermetic":
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot resume tenant: no discovered admin action 'tenant.resume' available: {e}",
                ) from None

        if admin_result is not None and not admin_result.ok:
            raise HTTPException(
                status_code=502,
                detail=f"Tenant administrative resume operation failed: {admin_result.error}",
            )

        now_iso = datetime.now(tz=UTC).isoformat()
        docs_store = getattr(ctx, "docs", None)
        state_doc = _get_operator_state_doc(docs_store, "current") or {}
        state_doc.update(
            {
                "tenant_paused": False,
                "simulator_enabled": True,
                "tenant_resumed_at": now_iso,
                "tenant_resumed_by": actor,
            }
        )
        _set_operator_state(docs_store, "current", state_doc)

        if hasattr(ctx, "operator_state") and isinstance(ctx.operator_state, dict):
            ctx.operator_state["tenant_paused"] = False
            ctx.operator_state["simulator_enabled"] = True

        sentinel_eng = getattr(ctx, "sentinel_engine", None)
        if sentinel_eng is not None and hasattr(sentinel_eng, "set_planned_operator_action"):
            try:
                sentinel_eng.set_planned_operator_action(active=False, reason=None)
            except Exception as e:
                logger.warning("Could not reset planned operator action in sentinel engine: %s", e)

        await _emit_event(
            event_type="godview.schedule.resume",
            subject="tenant",
            data={"action": "resume", "operator": actor, "reason": reason, "tenant_paused": False},
        )

        return {
            "ok": True,
            "tenant_paused": False,
            "admin_action_invoked": admin_result is not None,
            "result": admin_result.model_dump(mode="json") if admin_result else None,
        }

    @router.post("/operator/hibernate")
    @router.post("/hibernate")
    async def toggle_hibernate(
        body: HibernateRequest,
        operator: Any = Depends(operator_dep),  # noqa: B008
    ) -> dict[str, Any]:
        """Hibernate or wake the platform: run Cloud Run hibernate job via act SA and scale agents."""
        actor = _get_operator_email(operator)
        env_mode = _get_env_mode(ctx)

        project_id = (
            os.environ.get("GOOGLE_CLOUD_PROJECT", "")
            or getattr(ctx, "project_id", "")
            or (getattr(ctx, "settings", None) and getattr(ctx.settings, "gcp_project_id", ""))
        )
        location = (
            (getattr(ctx, "settings", None) and getattr(ctx.settings, "region", ""))
            or os.environ.get("GODVIEW_REGIONS", "us-central1").split(",")[0].strip()
            or "us-central1"
        )

        if env_mode == "hermetic":
            project_id = project_id or "hermetic"
            location = location or "us-central1"
        elif not project_id:
            raise HTTPException(
                status_code=409,
                detail="Cannot trigger hibernate: GOOGLE_CLOUD_PROJECT is required outside hermetic mode",
            )

        discovered_job = None
        disc = getattr(ctx, "discovered_services", {}) or {}
        for s in disc.values():
            if isinstance(s, dict):
                s_name = s.get("name") or s.get("id") or ""
                labels = s.get("labels") or {}
                if "hibernate" in str(s_name) or labels.get("godview_role") == "hibernate":
                    discovered_job = str(s_name)
                    break

        raw_job = body.job_name or os.environ.get("HIBERNATE_JOB_NAME") or discovered_job or "god-view-hibernate-job"
        if raw_job.startswith("projects/"):
            job_name = raw_job
        else:
            job_name = f"projects/{project_id}/locations/{location}/jobs/{raw_job}"

        if body.enabled:
            # 1. Trigger Cloud Run v2 hibernate job via act SA
            if env_mode != "hermetic":
                try:
                    token = await get_act_sa_access_token(ctx, project_id=project_id)
                    headers = {"Authorization": f"Bearer {token}"}
                    client = getattr(ctx, "http_client", None) or httpx.AsyncClient()
                    run_url = f"https://run.googleapis.com/v2/{job_name}:run"
                    resp = await client.post(run_url, headers=headers, timeout=15.0)
                    if resp.status_code not in (200, 204):
                        raise HTTPException(
                            status_code=502,
                            detail=f"Cloud Run hibernate job run failed with status {resp.status_code}: {resp.text}",
                        )
                except httpx.HTTPError as exc:
                    raise HTTPException(
                        status_code=502,
                        detail=f"Failed to trigger Cloud Run hibernate job: {exc}",
                    ) from exc

            # 2. Scale agents to 0
            if hasattr(ctx, "agents") and ctx.agents is not None:
                try:
                    agents_list = ctx.agents.list_agents() if hasattr(ctx.agents, "list_agents") else []
                    for ag in agents_list:
                        aid = ag.get("agent_id") or ag.get("id") or ag.get("name")
                        if aid:
                            try:
                                await agent_executor.execute(ctx, {"min_instances": 0}, str(aid), agent_id="operator")
                            except Exception as e:
                                logger.warning("Failed to scale agent %s to 0 during hibernate: %s", aid, e)
                except Exception as exc:
                    logger.warning("Failed to list agents during hibernate: %s", exc)
        else:
            # Restore agents to 1
            if hasattr(ctx, "agents") and ctx.agents is not None:
                try:
                    agents_list = ctx.agents.list_agents() if hasattr(ctx.agents, "list_agents") else []
                    for ag in agents_list:
                        aid = ag.get("agent_id") or ag.get("id") or ag.get("name")
                        if aid:
                            try:
                                await agent_executor.execute(ctx, {"min_instances": 1}, str(aid), agent_id="operator")
                            except Exception as e:
                                logger.warning("Failed to restore agent %s to 1: %s", aid, e)
                except Exception as exc:
                    logger.warning("Failed to list agents during hibernate wake: %s", exc)

        now_iso = datetime.now(tz=UTC).isoformat()
        docs_store = getattr(ctx, "docs", None)
        state_doc = _get_operator_state_doc(docs_store, "current") or {}
        state_doc.update(
            {
                "hibernate_enabled": body.enabled,
                "hibernate_job_name": job_name,
                "hibernate_updated_at": now_iso,
                "hibernate_updated_by": actor,
                "hibernate_reason": body.reason,
            }
        )
        _set_operator_state(docs_store, "current", state_doc)

        await _emit_event(
            event_type="godview.operator.hibernate",
            subject="platform",
            data={"enabled": body.enabled, "job_name": job_name, "operator": actor, "reason": body.reason},
        )

        return {
            "ok": True,
            "hibernate_enabled": body.enabled,
            "job_name": job_name,
        }

    @router.get("/chaos/drills")
    async def list_chaos_drills() -> dict[str, Any]:
        """Return registered chaos drills (empty in production unless drills are configured)."""
        return {"drills": []}

    return router


__all__ = ["build_router"]
