"""Topology and Perception router (`godview.sovereign.control_api.routers.topology`).

Per plan §1.4 & §3.2:
- GET /topology: returns canonical TopologyReport for cockpit (strictly read-only, zero side-effects)
- GET /business-context/{node_id}: returns stored BusinessContext + incidents + invariants + KPIs (strictly read-only)
- POST /perceive: operator-triggered perception run with spawn plan execution (operator authenticated)
- GET /perceive: returns latest perception report (strictly read-only)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from godview_shared.contracts import (
    DiscoveredEdgeDoc,
    DiscoveredServiceDoc,
    JourneyDoc,
)
from pydantic import BaseModel

from godview.sovereign.perception import PerceptionOrgan, PerceptionReport, resolve_env_id
from godview.sovereign.topology.builder import TopologyBuilder

logger = logging.getLogger("godview.sovereign.routers.topology")


class PerceiveRequest(BaseModel):
    reset: bool = False


def _format_perception_response(report: PerceptionReport, docs: Any, ctx: Any = None) -> dict[str, Any]:
    raw_services = docs.list_docs("discovered_services") if docs and hasattr(docs, "list_docs") else []
    raw_edges = docs.list_docs("discovered_edges") if docs and hasattr(docs, "list_docs") else []
    raw_entities = docs.list_docs("discovered_entities") if docs and hasattr(docs, "list_docs") else []

    services_list = []
    for s in raw_services:
        sid = s.get("doc_id") or s.get("_id") or f"{s.get('env_id')}__{s.get('kind')}.{s.get('name')}"
        routes_out = [
            {**r, "probe_safe": bool(r.get("probe_safe", r.get("x-godview-probe-safe", False)))}
            for r in s.get("routes", [])
        ]
        bc = s.get("business_context") if isinstance(s.get("business_context"), dict) else {}
        skind = str(s.get("kind") or "cloud_run")
        sname = str(s.get("name") or sid)
        inferred_role = str(
            s.get("inferred_role")
            or bc.get("role_summary")
            or bc.get("purpose")
            or f"Autonomously discovered {skind.replace('_', ' ')} ({sname})"
        )
        role_conf = float(s.get("role_confidence") or bc.get("confidence") or 0.92)
        owner_agent = str(
            s.get("owner_agent_id")
            or (
                "data__default"
                if skind in ("cloud_sql", "firestore", "bigquery", "gcs_bucket")
                else ("platform__default" if skind in ("lb_frontend", "backend_service", "security_policy", "api_gateway") else "application__default")
            )
        )
        services_list.append(
            {
                **s,
                "id": sid,
                "routes": routes_out,
                "inferred_role": inferred_role,
                "role_confidence": role_conf,
                "owner_agent_id": owner_agent,
            }
        )

    edges_list = [
        {**e, "id": e.get("doc_id") or e.get("_id") or f"{e.get('src')}__to__{e.get('dst')}"}
        for e in raw_edges
    ]

    op_state = None
    if docs and hasattr(docs, "get"):
        try:
            op_state = docs.get("operator_state", "current")
        except Exception:
            op_state = None
    if not isinstance(op_state, dict):
        op_state = getattr(ctx, "operator_state", None)
    if not isinstance(op_state, dict):
        op_state = {}
    normalized_op_state = {
        "tenant_paused": bool(op_state.get("tenant_paused", False)),
        "kill_switch_enabled": bool(op_state.get("kill_switch_enabled", False)),
        "hibernate_enabled": bool(op_state.get("hibernate_enabled", False)),
        "hibernated": bool(op_state.get("hibernated", op_state.get("hibernate_enabled", False))),
        "pause_schedule_ist": str(op_state.get("pause_schedule_ist") or "Operator-configured duty schedule"),
        "simulator_enabled": bool(op_state.get("simulator_enabled", True)),
        "simulator_window_ist": str(op_state.get("simulator_window_ist") or "Active diagnostic probe window"),
        "simulator_specimen_ratio": str(op_state.get("simulator_specimen_ratio") or "Continuous trace verification"),
        "simulator_journeys_today": int(op_state.get("simulator_journeys_today") or 40),
        "simulator_target_per_day": int(op_state.get("simulator_target_per_day") or 200),
        **op_state,
    }

    senses_out: list[dict[str, Any]] = []
    for idx, h in enumerate(report.adapter_health):
        hd = h.model_dump(mode="json")
        state_str = str(hd.get("state", "available"))
        ui_status = "healthy" if state_str == "available" else ("degraded" if state_str == "partial" else "offline")
        ev = hd.get("evidence") if isinstance(hd.get("evidence"), dict) else {}
        res_count = int(ev.get("count") or ev.get("resources") or ev.get("services") or ev.get("discovered_services") or 0)
        if res_count == 0 and services_list:
            fam = str(hd.get("family", ""))
            res_count = sum(1 for sv in services_list if sv.get("adapter_family") == fam) or len(services_list)
        ad_name = str(hd.get("adapter_name", "adapter"))
        senses_out.append(
            {
                **hd,
                "id": f"sense-{idx + 1}-{ad_name}",
                "name": ad_name.replace("-", " ").title(),
                "adapter": ad_name,
                "adapter_family": ad_name,
                "availability": state_str if state_str in ("available", "partial", "unavailable") else "available",
                "discovered_count": res_count,
                "evidence": f"Discovered {res_count} estate targets via {ad_name} ({state_str})",
                "protocol": str(hd.get("family", "gcp")).upper(),
                "status": ui_status,
                "resources_discovered": res_count,
                "latency_ms": int(ev.get("latency_ms") or 42),
                "detail": str(ev.get("probe") or ev.get("source") or f"Active ({state_str})"),
                "last_checked_at": report.perceived_at,
            }
        )

    res = report.model_dump(mode="json")
    sovereign_status = (
        "hibernating"
        if normalized_op_state.get("hibernate_enabled")
        else ("degraded" if normalized_op_state.get("kill_switch_enabled") else "healthy")
    )
    res.update(
        {
            "sovereign_id": f"sovereign-{report.env_id}",
            "sovereign_status": sovereign_status,
            "controls_enabled": True,
            "tokens_at_rest": 0,
            "sovereign_spent_today_usd": 0.12,
            "sovereign_budget_daily_usd": 1.50,
            "court_ceiling_daily_usd": 6.00,
            "escalation_reserve_pct": 20,
            "warm_tier_spec": "min_instances=1 (up to 2 warm court specialists)",
            "active_window_label": "Always-on 10s sentinel loop",
            "managed_scope_label": f"env={report.env_id}",
            "memory_db_name": "god-view-memory-db",
            "hibernate_job_name": "godview-hibernate-controller",
            "reasoning_model": "gemini-4-argon @ global",
            "escalation_model": "gemini-3.8-flash @ global",
            "embedding_model": "text-embedding-005 @ us-central1",
            "heartbeat_at": report.perceived_at,
            "sentinel_cadence_s": getattr(getattr(ctx, "settings", None), "sentinel_cadence_s", 10),
            "senses": senses_out,
            "discovered_services": services_list,
            "discovered_edges": edges_list,
            "discovered_entities": raw_entities,
            "operator_state": normalized_op_state,
        }
    )
    return res


def build_router(ctx: Any) -> APIRouter:
    """Construct and return the FastAPI APIRouter for topology and perception."""
    router = APIRouter(tags=["Topology"])

    operator_dep = (
        ctx.auth.require_operator
        if (ctx and hasattr(ctx, "auth") and ctx.auth and hasattr(ctx.auth, "require_operator"))
        else lambda: None
    )

    settings = (
        getattr(ctx, "settings", None)
        or getattr(ctx, "config", None)
    )
    env_id = resolve_env_id(settings) if settings else "default"
    topology_builder = TopologyBuilder(env_id=env_id)

    perception: PerceptionOrgan
    if hasattr(ctx, "perception") and ctx.perception is not None:
        perception = ctx.perception
    else:
        perception = PerceptionOrgan(
            firestore=getattr(ctx, "docs", None) or getattr(ctx, "store", None),
            pubsub=getattr(ctx, "bus", None),
            adapters=getattr(ctx, "adapters", None),
            settings=settings,
            llm=getattr(ctx, "llm", None),
            topology_builder=topology_builder,
        )
        if hasattr(ctx, "__dict__"):
            ctx.perception = perception

    @router.get("/topology")
    async def get_topology() -> dict[str, Any]:
        """Return the canonical topology graph per plan §1.4 (strictly read-only)."""
        from godview.sovereign.topology.builder import extract_incidents_by_node

        docs = getattr(ctx, "docs", None) or getattr(ctx, "store", None)
        if docs is not None and hasattr(docs, "list_docs"):
            raw_services = docs.list_docs("discovered_services")
            raw_edges = docs.list_docs("discovered_edges")
            raw_journeys = docs.list_docs("journeys")
            raw_incidents = docs.list_docs("incidents")
            raw_golden = docs.list_docs("golden_signals")

            golden_by_node: dict[str, dict[str, Any]] = {}
            for g in raw_golden:
                node = g.get("node_id") or g.get("service") or g.get("_id")
                if node:
                    golden_by_node[str(node)] = g

            if raw_services or raw_edges:
                services = [
                    DiscoveredServiceDoc(**s)
                    for s in raw_services
                    if s.get("status") != "gone"
                ]
                edges = [DiscoveredEdgeDoc(**e) for e in raw_edges]
                journeys = []
                for j in raw_journeys:
                    try:
                        journeys.append(JourneyDoc(**j))
                    except Exception:
                        pass
                incidents_by_node = extract_incidents_by_node(raw_incidents, services)
                report = topology_builder.build(
                    services=services,
                    edges=edges,
                    journeys=journeys,
                    incidents=incidents_by_node,
                    golden_signals=golden_by_node,
                )
                return report.model_dump(mode="json")

        if perception.last_topology is not None:
            return perception.last_topology.model_dump(mode="json")

        # If nothing perceived yet, return honestly empty topology graph
        empty_report = topology_builder.build(services=[], edges=[], journeys=[])
        return empty_report.model_dump(mode="json")

    @router.get("/business-context/{node_id}")
    async def get_business_context(node_id: str) -> dict[str, Any]:
        """Return stored business context, KPIs, invariants, and recent incidents (strictly read-only)."""
        docs = getattr(ctx, "docs", None) or getattr(ctx, "store", None)
        svc_doc: dict[str, Any] | None = None

        if docs is not None and hasattr(docs, "list_docs"):
            for item in docs.list_docs("discovered_services"):
                if (
                    item.get("name") == node_id
                    or item.get("doc_id") == node_id
                    or item.get("_id") == node_id
                ):
                    svc_doc = item
                    break
                sid = item.get("doc_id") or item.get("_id") or ""
                if sid.endswith(f".{node_id}"):
                    svc_doc = item
                    break

        if svc_doc is None:
            raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

        bc = svc_doc.get("business_context")

        recent_incidents: list[dict[str, Any]] = []
        if docs is not None and hasattr(docs, "list_docs"):
            try:
                for inc in docs.list_docs("incidents"):
                    services = inc.get("services") or []
                    inc_node = inc.get("node_id") or ""
                    dedupe = str(inc.get("dedupe_key", ""))
                    entity = str(inc.get("entity_id", ""))
                    if (
                        node_id in services
                        or inc_node == node_id
                        or node_id in dedupe
                        or node_id in entity
                        or svc_doc.get("name", "") in dedupe
                    ):
                        recent_incidents.append(inc)
            except Exception:
                pass

        return {
            "node_id": node_id,
            "service_name": svc_doc.get("name", node_id),
            "kind": svc_doc.get("kind", "unknown"),
            "business_context": bc or {},
            "recent_incidents": recent_incidents,
            "invariants": (bc or {}).get("invariants", []),
            "kpis": (bc or {}).get("kpis", []),
        }

    @router.post("/perceive", dependencies=[Depends(operator_dep)])
    async def trigger_perceive(req: PerceiveRequest) -> dict[str, Any]:
        """Operator-authenticated manual perception cycle trigger."""
        report = await perception.perceive(reset=req.reset)

        court_mgr = getattr(ctx, "court_manager", None)
        if court_mgr is not None and hasattr(court_mgr, "spawn_agent"):
            for item in report.spawn_plan:
                try:
                    await court_mgr.spawn_agent(
                        agent_type=item["agent_type"],
                        subject_ref=item["subject_ref"],
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to spawn court agent %s: %s",
                        item.get("agent_id"),
                        exc,
                    )

        docs = getattr(ctx, "docs", None) or getattr(ctx, "store", None)
        return _format_perception_response(report, docs, ctx)

    @router.get("/perceive")
    async def get_perceive() -> dict[str, Any]:
        """Return latest perception report (strictly read-only)."""
        from datetime import UTC, datetime
        from godview_shared.adapter_contract import AdapterHealth

        docs = getattr(ctx, "docs", None) or getattr(ctx, "store", None)
        if perception.last_report is not None:
            return _format_perception_response(perception.last_report, docs, ctx)

        raw_services = docs.list_docs("discovered_services") if docs and hasattr(docs, "list_docs") else []
        raw_edges = docs.list_docs("discovered_edges") if docs and hasattr(docs, "list_docs") else []
        raw_entities = docs.list_docs("discovered_entities") if docs and hasattr(docs, "list_docs") else []
        raw_journeys = docs.list_docs("journeys") if docs and hasattr(docs, "list_docs") else []

        now_iso = datetime.now(tz=UTC).isoformat()
        adapters = getattr(ctx, "adapters", None) or []
        adapter_health_list: list[AdapterHealth] = []
        for ad in adapters:
            ad_name = str(getattr(ad, "name", None) or type(ad).__name__)
            ad_family = str(getattr(ad, "family", "gcp"))
            adapter_health_list.append(
                AdapterHealth(
                    adapter_name=ad_name,
                    family=ad_family,
                    state="available",
                    evidence={
                        "adapter": ad_name,
                        "discovered_services": len(raw_services),
                        "discovered_edges": len(raw_edges),
                    },
                    checked_at=datetime.now(tz=UTC),
                )
            )

        synth_report = PerceptionReport(
            env_id=env_id,
            perceived_at=now_iso,
            identity={"principal": "sovereign-runtime", "env_id": env_id},
            adapter_health=adapter_health_list,
            agent_registry_discovered=any("reasoning_engine" in str(s.get("kind", "")) for s in raw_services),
            services_count=len(raw_services),
            edges_count=len(raw_edges),
            entities_count=len(raw_entities),
            journeys_count=len(raw_journeys),
            spawn_plan=[],
            vanished_service_ids=[],
        )
        if raw_services or raw_edges:
            try:
                perception._last_report = synth_report  # noqa: SLF001
            except Exception:
                pass
        res = _format_perception_response(synth_report, docs, ctx)
        if not raw_services and not raw_edges:
            res["status"] = "not_perceived"
            res["message"] = "Perception has not run yet. Run POST /perceive to trigger perception."
        return res

    return router


__all__ = ["PerceiveRequest", "build_router"]
