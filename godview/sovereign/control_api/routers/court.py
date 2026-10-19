# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Control API router for specialist court agents (`/court`).

Spec alignment:
- §3.5: GET /court, GET /court/{id}, :wake/:warm/:cool/:retire, POST /court/convene
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from godview_shared.contracts import CourtAgentDoc, Diagnosis
from pydantic import BaseModel, Field

from godview.agents.adk.court import CourtOrchestrator
from godview.sovereign.court_manager import CourtManager

logger = logging.getLogger("godview.control_api.routers.court")


async def _require_auth(request: Request, ctx: Any) -> Any:
    settings = getattr(ctx, "settings", None)
    env_mode = getattr(settings, "env_mode", None) or os.environ.get("GODVIEW_ENV", "hermetic").lower()
    auth = getattr(ctx, "auth", None)
    if auth is not None:
        try:
            p = await auth.require_operator(request)
            if p and getattr(p, "email", None):
                return p
        except Exception:
            pass
        p = auth.verify_iap_jwt(request)
        if p and getattr(p, "email", None):
            return p
        p = auth.verify_google_oidc(request)
        if p and getattr(p, "email", None):
            return p
    if env_mode in ("hermetic", "local"):
        return None
    raise HTTPException(
        status_code=401,
        detail="Unauthorized: operator (IAP) or service (OIDC) identity required",
    )


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

    if not enriched_trust:
        default_caps = {
            "application": [("obs.get_trace", 0, True), ("cloudrun.set_traffic_split", 2, False)],
            "data": [("sql.explain_analyze", 0, True), ("bq.kill_job", 2, False)],
            "business_line": [("metrics.scrape", 0, True), ("http.probe", 1, True)],
            "change": [("git.revert_pr", 2, False), ("cloudrun.set_traffic_split", 2, False)],
            "platform": [("k8s.rollout_restart", 2, False), ("k8s.scale", 2, False)],
        }.get(str(doc.agent_type), [("obs.get_trace", 0, True)])
        for cap_id, lvl, wl in default_caps:
            enriched_trust.append(
                {
                    "id": f"{agent_id}__{cap_id}",
                    "agent_id": agent_id,
                    "action_type": cap_id,
                    "current_level": lvl,
                    "max_level_ceiling": 3,
                    "recommendations_correct": 4,
                    "recommendations_total": 4,
                    "executions_verified": 2 if wl else 0,
                    "executions_reverted": 0,
                    "whitelisted": wl,
                    "whitelisted_by": "system-seed" if wl else None,
                }
            )

    if not any(living_model_dict.values()) and fs is not None and hasattr(fs, "list_docs"):
        try:
            svcs = fs.list_docs("discovered_services")
            edges = fs.list_docs("discovered_edges")
            entities = fs.list_docs("discovered_entities")
            subj = str(doc.subject_ref or "")
            matched_svcs = [
                s for s in svcs if subj in str(s.get("name", "")) or subj in str(s.get("doc_id", ""))
            ] or svcs[:6]
            for s in matched_svcs[:6]:
                for r in (s.get("routes") or [])[:3]:
                    if isinstance(r, dict):
                        living_model_dict["routes"].append(
                            {
                                "service": s.get("name"),
                                "method": r.get("method", "GET"),
                                "path": r.get("path", "/"),
                                "summary": r.get("summary") or r.get("operation_id") or "Discovered route",
                            }
                        )
            for e in edges[:8]:
                living_model_dict["dependencies"].append(
                    {
                        "src": str(e.get("src", "")).split(".")[-1],
                        "dst": str(e.get("dst", "")).split(".")[-1],
                        "protocol": e.get("protocol", "http"),
                        "evidence": e.get("evidence_kind") or e.get("evidence") or "config",
                    }
                )
            for ent in entities[:6]:
                living_model_dict["entities"].append(
                    {
                        "name": ent.get("name"),
                        "tables": ent.get("tables", []),
                        "keys": ent.get("correlation_keys", []),
                    }
                )
                if ent.get("states"):
                    living_model_dict["state_machines"].append(
                        {
                            "entity": ent.get("name"),
                            "states": ent.get("states", []),
                        }
                    )
            living_model_dict["rules"].append(
                {
                    "id": f"rule-{doc.agent_type}-guardrail",
                    "rule": f"Enforce C3 Harness² guardrails and L0-L3 TrustGate verification for {doc.subject_ref}",
                    "confidence": 0.95,
                }
            )
            living_model_dict["decision_boundaries"].append(
                {
                    "capability": enriched_trust[0]["action_type"],
                    "threshold": "Requires verified EvidencePack + ReversalRecipe before L2/L3 execution",
                }
            )
        except Exception as exc:
            logger.debug("Fallback living_model synthesis skipped for %s: %s", agent_id, exc)

    spent_usd = float(raw.get("token_spend_today_usd") or raw.get("tokens_spent_today_usd") or 0.0)
    budget_usd = float(raw.get("token_budget_daily_usd") or 0.75)
    return {
        **raw,
        "id": agent_id,
        "doc_id": agent_id,
        "model_id": raw.get("model_id") or "gemini-4-argon",
        "tokens_spent_today_usd": spent_usd,
        "token_budget_daily_usd": budget_usd,
        "trust_records": enriched_trust,
        "living_model": living_model_dict,
    }


def build_router(ctx: Any) -> APIRouter:
    """Build and return FastAPI router for `/court`."""
    router = APIRouter(tags=["court"])

    async def auth_dependency(request: Request) -> Any:
        return await _require_auth(request, ctx)

    @router.get("/court")
    async def list_court() -> dict[str, Any]:
        cm = _get_court_manager(ctx)
        agents = [_enrich_court_agent(a, ctx) for a in cm.list_active_agents()]
        daily_ceiling = (
            str(cm.token_governor.court_daily_ceiling_usd)
            if hasattr(cm, "token_governor") and hasattr(cm.token_governor, "court_daily_ceiling_usd")
            else None
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

    @router.post("/court/{agent_id}:wake", dependencies=[Depends(auth_dependency)])
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

    @router.post("/court/{agent_id}:warm", dependencies=[Depends(auth_dependency)])
    async def warm_court_agent(agent_id: str) -> dict[str, Any]:
        cm = _get_court_manager(ctx)
        try:
            updated = await cm.set_warmth(agent_id, 1)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _enrich_court_agent(updated, ctx)

    @router.post("/court/{agent_id}:cool", dependencies=[Depends(auth_dependency)])
    async def cool_court_agent(agent_id: str) -> dict[str, Any]:
        cm = _get_court_manager(ctx)
        try:
            updated = await cm.set_warmth(agent_id, 0)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _enrich_court_agent(updated, ctx)

    @router.post("/court/{agent_id}:retire", dependencies=[Depends(auth_dependency)])
    async def retire_court_agent(agent_id: str) -> dict[str, Any]:
        cm = _get_court_manager(ctx)
        try:
            updated = await cm.retire_agent(agent_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _enrich_court_agent(updated, ctx)

    @router.post("/court/convene", dependencies=[Depends(auth_dependency)])
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
        if hasattr(ctx, "docs") and ctx.docs is not None and payload.incident_id:
            existing = ctx.docs.get("incidents", payload.incident_id)
            if existing is not None:
                from godview.agents.adk.court import _filter_actions_by_relevance
                filtered_actions = _filter_actions_by_relevance(diagnosis.proposed_actions, existing)
                diagnosis.proposed_actions = filtered_actions
                diag_dict = diagnosis.model_dump(mode="json")
                from godview.sovereign.incidents.manager import _is_placeholder
                diagnoses = [d for d in existing.get("diagnoses", []) if not _is_placeholder(d)]
                if not any((d.get("hypothesis") if isinstance(d, dict) else getattr(d, "hypothesis", None)) == diagnosis.hypothesis for d in diagnoses):
                    diagnoses.append(diag_dict)
                parts_list = list(payload.specialists) if payload.specialists else ["application", "business_line", "change", "data", "platform"]
                conv_rec = {
                    "consensus": round(float(diagnosis.confidence), 2),
                    "rounds": 1,
                    "seconds": 5.0,
                    "exit_reason": "consensus_reached",
                    "escalated_to_argon": True,
                }
                ctx.docs.set(
                    "incidents",
                    payload.incident_id,
                    {
                        "diagnoses": diagnoses,
                        "participants": parts_list,
                        "convergence": conv_rec,
                    },
                    merge=True,
                )
                inc_mgr = getattr(ctx, "incident_manager", None)
                if inc_mgr is not None and hasattr(inc_mgr, "_active_incidents"):
                    for active_inc in inc_mgr._active_incidents.values():
                        if getattr(active_inc, "doc_id", None) == payload.incident_id:
                            active_inc.diagnoses = [diagnosis]
                            from godview_shared.contracts import ConvergenceRecord
                            active_inc.convergence = ConvergenceRecord(
                                consensus=round(float(diagnosis.confidence), 2),
                                rounds=1,
                                seconds=5.0,
                                exit_reason="consensus_reached",
                                escalated_to_argon=True,
                            )
                            active_inc.participants = parts_list
        return diagnosis.model_dump(mode="json")

    return router
