"""Topology and Perception router (`godview.sovereign.control_api.routers.topology`).

Per plan §1.4 & §3.2:
- GET /topology: returns canonical TopologyReport for cockpit
- GET /business-context/{node_id}: returns full BusinessContext + incidents + invariants + KPIs
- POST /perceive: operator-triggered perception run with spawn plan execution
- GET /perceive: returns latest perception report
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

from godview.sovereign.business_context.inferencer import BusinessContextInferencer
from godview.sovereign.perception import PerceptionOrgan, PerceptionReport
from godview.sovereign.topology.builder import TopologyBuilder

logger = logging.getLogger("godview.sovereign.routers.topology")


class PerceiveRequest(BaseModel):
    reset: bool = False


def _format_perception_response(report: PerceptionReport, docs: Any) -> dict[str, Any]:
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
            "sovereign_status": "active",
            "heartbeat_at": report.perceived_at,
            "sentinel_cadence_s": 10,
            "senses": [h.model_dump(mode="json") for h in report.adapter_health],
            "discovered_services": services_list,
            "discovered_edges": edges_list,
            "discovered_entities": raw_entities,
            "operator_state": {"paused": False, "kill_switch_active": False},
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

    env_mode = (
        getattr(ctx.settings, "env_mode", "default")
        if hasattr(ctx, "settings") and ctx.settings
        else "default"
    )

    topology_builder = TopologyBuilder(env_id=env_mode)

    perception: PerceptionOrgan
    if hasattr(ctx, "perception") and ctx.perception is not None:
        perception = ctx.perception
    else:
        perception = PerceptionOrgan(
            firestore=getattr(ctx, "docs", None),
            pubsub=getattr(ctx, "bus", None),
            adapters=getattr(ctx, "adapters", None),
            settings=getattr(ctx, "settings", None),
            llm=getattr(ctx, "llm", None),
            topology_builder=topology_builder,
        )
        if hasattr(ctx, "__dict__"):
            ctx.perception = perception

    @router.get("/topology")
    async def get_topology() -> dict[str, Any]:
        """Return the canonical topology graph per plan §1.4."""
        if perception.last_topology is not None:
            return perception.last_topology.model_dump(mode="json")

        docs = getattr(ctx, "docs", None)
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

        # Run perception if nothing has run yet
        await perception.perceive(reset=False)
        if perception.last_topology is not None:
            return perception.last_topology.model_dump(mode="json")

        empty_report = topology_builder.build([], [])
        return empty_report.model_dump(mode="json")

    @router.get("/business-context/{node_id}")
    async def get_business_context(node_id: str) -> dict[str, Any]:
        """Return full BusinessContext + recent incidents + invariants + KPIs."""
        docs = getattr(ctx, "docs", None)
        if docs is None or not hasattr(docs, "get"):
            raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

        # Direct lookup by doc_id
        svc_doc = docs.get("discovered_services", node_id)
        if svc_doc is None:
            # Fallback scan for matching name or suffix
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
        if bc is None:
            # Infer on-demand from technical evidence
            svc_model = DiscoveredServiceDoc(**svc_doc)
            edges_raw = docs.list_docs("discovered_edges") if hasattr(docs, "list_docs") else []
            edges = [DiscoveredEdgeDoc(**e) for e in edges_raw]
            inferencer = getattr(perception, "_inferencer", None) or BusinessContextInferencer(
                llm=getattr(ctx, "llm", None), firestore=docs
            )
            bc_model = await inferencer.infer_service_context(svc_model, edges)
            bc = bc_model.model_dump(mode="json")

        recent_incidents: list[dict[str, Any]] = []
        if hasattr(docs, "list_docs"):
            try:
                for inc in docs.list_docs("incidents"):
                    target = inc.get("target_resource") or inc.get("resource_id") or ""
                    if node_id in target or svc_doc.get("name", "") in target:
                        recent_incidents.append(inc)
            except Exception:
                pass

        return {
            "node_id": node_id,
            "service_name": svc_doc.get("name", node_id),
            "kind": svc_doc.get("kind", "unknown"),
            "business_context": bc,
            "domain": bc.get("domain", "") if isinstance(bc, dict) else getattr(bc, "domain", ""),
            "role_summary": (
                bc.get("role_summary", "") if isinstance(bc, dict) else getattr(bc, "role_summary", "")
            ),
            "confidence": (
                bc.get("confidence", 0.0) if isinstance(bc, dict) else getattr(bc, "confidence", 0.0)
            ),
            "capabilities": (
                bc.get("capabilities", []) if isinstance(bc, dict) else getattr(bc, "capabilities", [])
            ),
            "invariants": (
                bc.get("invariants", []) if isinstance(bc, dict) else getattr(bc, "invariants", [])
            ),
            "kpis": bc.get("kpis", []) if isinstance(bc, dict) else getattr(bc, "kpis", []),
            "evidence": bc.get("evidence", []) if isinstance(bc, dict) else getattr(bc, "evidence", []),
            "recent_incidents": recent_incidents[:10],
        }

    @router.post("/perceive")
    async def run_perceive(
        body: PerceiveRequest | None = None,
        operator: Any = Depends(operator_dep),  # noqa: B008
    ) -> dict[str, Any]:
        """Trigger perception pass and return formatted report."""
        reset_flag = body.reset if body is not None else False
        report = await perception.perceive(reset=reset_flag)

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

        docs = getattr(ctx, "docs", None)
        return _format_perception_response(report, docs)

    @router.get("/perceive")
    async def get_perceive() -> dict[str, Any]:
        """Return latest perception report."""
        if perception.last_report is None:
            report = await perception.perceive(reset=False)
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
        else:
            report = perception.last_report

        docs = getattr(ctx, "docs", None)
        return _format_perception_response(report, docs)

    return router


__all__ = ["PerceiveRequest", "build_router"]
