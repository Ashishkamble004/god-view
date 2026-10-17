# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Control API router for specialist court agents (`/court`).

Spec alignment:
- §3.5: GET /court, GET /court/{id}, :wake/:warm/:cool/:retire, POST /court/convene
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from godview_shared.contracts import CourtAgentDoc, Diagnosis
from pydantic import BaseModel, Field

from godview.agents.adk.court import CourtOrchestrator
from godview.sovereign.court_manager import CourtManager

logger = logging.getLogger("godview.control_api.routers.court")


class CourtConveneRequest(BaseModel):
    """Payload to convene the multi-agent court for incident diagnosis."""

    incident_id: str = Field(..., description="Unique incident identifier")
    evidence_pack: dict[str, Any] = Field(default_factory=dict, description="Telemetry, logs, traces, topology evidence")
    specialists: list[str] | None = Field(default=None, description="Optional subset of archetypes to convene")
    timeout_s: float = Field(default=90.0, description="Swarm timeout in seconds")
    consensus_target: float = Field(default=0.85, description="Required swarm consensus threshold")


CourtConveneRequest.model_rebuild()


def _get_court_manager(ctx: Any) -> CourtManager:
    """Retrieve or build CourtManager from SovereignContext."""
    if hasattr(ctx, "court_manager") and ctx.court_manager is not None:
        return ctx.court_manager

    docs = getattr(ctx, "docs", None)
    bus = getattr(ctx, "bus", None)
    agents = getattr(ctx, "agents", None)
    return CourtManager(firestore=docs, pubsub=bus, agent_engine=agents)


def _enrich_court_agent(doc: CourtAgentDoc, ctx: Any) -> dict[str, Any]:
    """Enrich CourtAgentDoc with trust records and living model if available in doc store."""
    raw = doc.model_dump(mode="json")
    agent_id = doc.doc_id or doc.namespace
    fs = getattr(ctx, "docs", None)

    enriched_trust: list[dict[str, Any]] = []
    living_model_dict: dict[str, list[dict[str, Any]]] = {
        "entities": [],
        "rules": [],
        "state_machines": [],
        "routes": [],
        "dependencies": [],
        "decision_boundaries": [],
        "feature_flags": [],
        "commit_diffs": [],
    }

    if fs is not None and hasattr(fs, "list_docs"):
        try:
            trust_docs = fs.list_docs(
                "trust_records", predicate=lambda d: d.get("agent_id") == agent_id
            )
            for td in trust_docs:
                rec_id = td.get("doc_id") or td.get("_id") or f"{agent_id}__{td.get('action_type', '')}"
                enriched_trust.append({**td, "id": rec_id})
        except Exception as exc:
            logger.debug("Failed to list trust records for %s: %s", agent_id, exc)

        if hasattr(fs, "list_subdocs"):
            try:
                subdocs = fs.list_subdocs("court_agents", agent_id, "living_model")
                for sd in subdocs:
                    sec = str(sd.get("section") or sd.get("_id") or "rules")
                    if sec in living_model_dict and isinstance(sd.get("items"), list):
                        living_model_dict[sec] = list(sd["items"])
            except Exception as exc:
                logger.debug("Failed to list subdocs for %s: %s", agent_id, exc)

    return {
        **raw,
        "trust_records": enriched_trust,
        "living_model": living_model_dict,
    }


def build_router(ctx: Any) -> APIRouter:
    """Build and return FastAPI router for `/court`."""
    router = APIRouter(tags=["court"])

    @router.get("/court")
    async def list_court() -> dict[str, Any]:
        cm = _get_court_manager(ctx)
        agents = [_enrich_court_agent(a, ctx) for a in cm.list_active_agents()]
        daily_ceiling = (
            str(cm.token_governor.court_daily_ceiling_usd)
            if hasattr(cm, "token_governor") and hasattr(cm.token_governor, "court_daily_ceiling_usd")
            else "50.00"
        )
        return {
            "agents": agents,
            "max_agents": cm.max_agents,
            "court_daily_ceiling_usd": daily_ceiling,
        }

    @router.get("/court/{agent_id}")
    async def get_court_agent(agent_id: str) -> dict[str, Any]:
        cm = _get_court_manager(ctx)
        doc = cm.get_agent(agent_id)
        if doc is None:
            raise HTTPException(status_code=404, detail=f"Unknown court agent '{agent_id}'")
        enriched = _enrich_court_agent(doc, ctx)
        return {
            **enriched,
            "agent": enriched,
        }

    @router.post("/court/{agent_id}:wake")
    async def wake_court_agent(agent_id: str) -> dict[str, Any]:
        cm = _get_court_manager(ctx)
        doc = cm.get_agent(agent_id)
        if doc is None:
            raise HTTPException(status_code=404, detail=f"Unknown court agent '{agent_id}'")
        now_iso = datetime.now(tz=UTC).isoformat()
        updated = doc.model_copy(
            update={
                "wakes_7d": doc.wakes_7d + 1,
                "last_wake_at": now_iso,
                "state": "awake",
            }
        )
        fs = getattr(ctx, "docs", None)
        if fs is not None and hasattr(fs, "set"):
            fs.set("court_agents", agent_id, updated.model_dump(mode="json"))

        bus = getattr(ctx, "bus", None)
        if bus is not None and hasattr(bus, "publish"):
            now_str = datetime.now(tz=UTC).isoformat()
            pub_res = bus.publish(
                "godview.court.warm",
                {
                    "id": f"evt-{agent_id}-{int(datetime.now(tz=UTC).timestamp())}",
                    "type": "godview.court.warm",
                    "source": "//sovereign/control-api",
                    "subject": agent_id,
                    "time": now_str,
                    "data": {"agent_id": agent_id, "manual_wake": True},
                },
            )
            if asyncio.iscoroutine(pub_res):
                await pub_res
        return _enrich_court_agent(updated, ctx)

    @router.post("/court/{agent_id}:warm")
    async def warm_court_agent(agent_id: str) -> dict[str, Any]:
        cm = _get_court_manager(ctx)
        try:
            updated = await cm.set_warmth(agent_id, 1)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _enrich_court_agent(updated, ctx)

    @router.post("/court/{agent_id}:cool")
    async def cool_court_agent(agent_id: str) -> dict[str, Any]:
        cm = _get_court_manager(ctx)
        try:
            updated = await cm.set_warmth(agent_id, 0)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _enrich_court_agent(updated, ctx)

    @router.post("/court/{agent_id}:retire")
    async def retire_court_agent(agent_id: str) -> dict[str, Any]:
        cm = _get_court_manager(ctx)
        try:
            updated = await cm.retire_agent(agent_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _enrich_court_agent(updated, ctx)

    @router.post("/court/convene")
    async def convene_court(payload: CourtConveneRequest) -> dict[str, Any]:
        orchestrator = getattr(ctx, "court_orchestrator", None)
        if orchestrator is None:
            orchestrator = CourtOrchestrator(ctx=ctx)
        diagnosis: Diagnosis = await orchestrator.convene(
            incident_id=payload.incident_id,
            evidence_pack=payload.evidence_pack,
            specialists=payload.specialists,
            timeout_s=payload.timeout_s,
            consensus_target=payload.consensus_target,
        )
        return diagnosis.model_dump(mode="json")

    return router
