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
        services_list.append({**s, "id": sid, "routes": routes_out})

    edges_list = [
        {**e, "id": e.get("doc_id") or e.get("_id") or f"{e.get('src')}__to__{e.get('dst')}"}
        for e in raw_edges
    ]

    res = report.model_dump(mode="json")
    res.update(
        {
            "sovereign_id": f"sovereign-{report.env_id}",
            "heartbeat_at": report.perceived_at,
            "senses": [h.model_dump(mode="json") for h in report.adapter_health],
            "discovered_services": services_list,
            "discovered_edges": edges_list,
            "discovered_entities": raw_entities,
        }
    )
    if ctx is not None and hasattr(ctx, "operator_state"):
        res["operator_state"] = ctx.operator_state
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
        if perception.last_topology is not None:
            return perception.last_topology.model_dump(mode="json")

        docs = getattr(ctx, "docs", None) or getattr(ctx, "store", None)
        if docs is not None and hasattr(docs, "list_docs"):
            raw_services = docs.list_docs("discovered_services")
            raw_edges = docs.list_docs("discovered_edges")
            raw_journeys = docs.list_docs("journeys")
            if raw_services or raw_edges:
                services = [
                    DiscoveredServiceDoc(**s)
                    for s in raw_services
                    if s.get("status") != "gone"
                ]
                edges = [DiscoveredEdgeDoc(**e) for e in raw_edges]
                journeys = [JourneyDoc(**j) for j in raw_journeys]
                report = topology_builder.build(
                    services=services, edges=edges, journeys=journeys
                )
                return report.model_dump(mode="json")

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
        docs = getattr(ctx, "docs", None) or getattr(ctx, "store", None)
        if perception.last_report is None:
            return {
                "status": "not_perceived",
                "env_id": env_id,
                "message": "Perception has not run yet. Run POST /perceive to trigger perception.",
            }
        return _format_perception_response(perception.last_report, docs, ctx)

    return router


__all__ = ["PerceiveRequest", "build_router"]
