# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Control API router for Harness² capability trees and RSI evolution (`/harness`).

Spec alignment:
- §3.5: GET /harness/{agent}/tree, POST /harness/{agent}:evolve
- Backward-compatible inspection endpoints: /agents/{id}/harness, /agents/{id}/experience, /sovereign/reflect, /memory/reflect
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from godview_shared.config import DEFAULT_AGENT_MODEL_ID
from pydantic import BaseModel, Field

from godview.agents.archetypes import resolve_domain
from godview.harness.core import HarnessTree, validate
from godview.harness.rsi import EvolutionResult, evolve_specialist
from godview.harness.substrate import GODVIEW_SURFACE, GodViewSubstrate

logger = logging.getLogger("godview.control_api.routers.harness")


class EvolvePayload(BaseModel):
    """Payload to trigger recursive evolution on a specialist's HarnessTree."""

    trigger: str = Field(default="manual", description="Trigger source: incident_closed, approval_decided, false_positive, nightly_reflection, manual")
    task_id: str | None = Field(default=None, description="Task ID or incident identifier")
    evidence_pack: dict[str, Any] = Field(default_factory=dict, description="Archived evidence pack or incident telemetry")
    mode: Literal["parallel", "sequential"] = Field(default="parallel", description="Harness² search mode")
    k: int = Field(default=1, description="Number of candidate branches")


class ReflectPayload(BaseModel):
    """Payload for /sovereign/reflect and /memory/reflect."""

    agent_id: str | None = Field(default=None, description="Target court agent")
    task_id: str | None = Field(default=None, description="Task or incident identifier")
    source: str = Field(default="manual", description="Trigger source")
    mode: Literal["parallel", "sequential"] = Field(default="parallel", description="Search mode")
    k: int = Field(default=1, description="Branch fan-out")


EvolvePayload.model_rebuild()
ReflectPayload.model_rebuild()


def _get_agent_tree(agent_id: str) -> tuple[str, HarnessTree]:
    """Resolve domain and load HarnessTree for an agent."""
    domain = resolve_domain(agent_id)
    substrate = GodViewSubstrate(domain=domain)
    base_dir = substrate.base_tree()
    if not base_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"Base HarnessTree not found for domain '{domain}' (agent '{agent_id}')",
        )
    return domain, HarnessTree(base_dir, GODVIEW_SURFACE)


def _serialize_tree(agent_id: str, domain: str, tree: HarnessTree) -> dict[str, Any]:
    """Serialize HarnessTree inspection payload."""
    report = validate(tree)
    root = tree.root

    c1_path = root / "systemprompt.md"
    c2_path = root / "tool_descriptions" / "tool_guidance.md"
    c3_path = root / "guardrails.md"
    c4_path = root / "LongTermMEMORY.md"

    hooks_dict = {}
    if tree.hooks_path().is_file():
        try:
            hooks_dict = json.loads(tree.hooks_path().read_text(encoding="utf-8"))
        except Exception:
            hooks_dict = {}

    return {
        "agent_id": agent_id,
        "domain": domain,
        "surface": "godview",
        "validation": {
            "ok": report.ok,
            "always_on_tokens": report.always_on_tokens,
            "always_on_max": tree.surface.always_on_max,
            "errors": report.errors,
        },
        "always_on_tokens": report.always_on_tokens,
        "tree_root": str(root),
        "model_id": DEFAULT_AGENT_MODEL_ID,
        "categories": {
            "c1_identity": c1_path.read_text(encoding="utf-8") if c1_path.is_file() else "",
            "c2_tool_descriptions": c2_path.read_text(encoding="utf-8") if c2_path.is_file() else "",
            "c3_guardrails": c3_path.read_text(encoding="utf-8") if c3_path.is_file() else "",
            "c4_long_term_memory": c4_path.read_text(encoding="utf-8") if c4_path.is_file() else "",
            "c5_sub_agents": [p.parent.name for p in tree.sub_agents()],
            "c6_skills": [p.parent.name for p in tree.skills()],
            "c7_scripts": [p.name for p in tree.scripts()],
            "c8_hooks": hooks_dict,
        },
    }


def build_router(ctx: Any) -> APIRouter:
    """Build and return FastAPI router for `/harness` and agent reflection."""
    router = APIRouter(tags=["harness"])

    @router.get("/harness/{agent_id}/tree")
    async def get_harness_tree(agent_id: str) -> dict[str, Any]:
        domain, tree = _get_agent_tree(agent_id)
        return _serialize_tree(agent_id, domain, tree)

    @router.post("/harness/{agent_id}:evolve")
    async def evolve_agent_tree(agent_id: str, payload: EvolvePayload) -> dict[str, Any]:
        res: EvolutionResult = evolve_specialist(
            agent_id=agent_id,
            trigger=payload.trigger,
            ctx=ctx,
            task_id=payload.task_id,
            evidence_pack=payload.evidence_pack,
            mode=payload.mode,
            k=payload.k,
        )
        return {
            "agent_id": res.agent_id,
            "domain": res.domain,
            "trigger": res.trigger,
            "task_id": res.task_id,
            "status": "completed",
            "winner_passed": res.passed,
            "winner_score": res.score,
            "winning_tag": res.winning_tag,
            "living_model_version": res.living_model_version,
            "adopted_path": res.adopted_path,
            "synced_gcs_objects": res.synced_gcs_objects,
            "notes": res.notes,
        }

    # Backward-compatible routes matching existing test assertions and UI
    @router.get("/agents/{agent_id}/harness")
    async def get_agent_harness(agent_id: str) -> dict[str, Any]:
        domain, tree = _get_agent_tree(agent_id)
        return _serialize_tree(agent_id, domain, tree)

    @router.get("/agents/{agent_id}/experience")
    async def get_agent_experience(agent_id: str) -> dict[str, Any]:
        domain = resolve_domain(agent_id)
        substrate = GodViewSubstrate(domain=domain)
        base_dir = substrate.base_tree()
        exp_file = base_dir / "LongTermMEMORY.md"
        content = exp_file.read_text(encoding="utf-8") if exp_file.is_file() else "# Experience Log\n"
        return {
            "agent_id": agent_id,
            "domain": domain,
            "experience_markdown": content,
        }

    @router.post("/sovereign/reflect")
    async def sovereign_reflect(payload: ReflectPayload) -> dict[str, Any]:
        aid = payload.agent_id or "application__specialist"
        res: EvolutionResult = evolve_specialist(
            agent_id=aid,
            trigger=payload.source,
            ctx=ctx,
            task_id=payload.task_id,
            mode=payload.mode,
            k=payload.k,
        )
        return {
            "agent_id": res.agent_id,
            "status": "completed",
            "winner_passed": res.passed,
            "winner_score": res.score,
            "winning_tag": res.winning_tag,
            "living_model_version": res.living_model_version,
            "adopted_path": res.adopted_path,
        }

    @router.post("/memory/reflect")
    async def memory_reflect(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        src = (payload or {}).get("source", "nightly")
        results = []
        for dom in ("application", "data", "business_line", "change", "platform"):
            aid = f"{dom}__specialist"
            try:
                res = evolve_specialist(
                    agent_id=aid,
                    trigger=f"memory_reflect:{src}",
                    ctx=ctx,
                    k=1,
                    mode="parallel",
                )
                results.append(
                    {
                        "agent_id": aid,
                        "domain": dom,
                        "score": res.score,
                        "passed": res.passed,
                        "version": res.living_model_version,
                    }
                )
            except Exception as exc:
                logger.warning("RSI reflection skipped for %s: %s", aid, exc)
                results.append({"agent_id": aid, "domain": dom, "error": str(exc)})
        return {"status": "completed", "results": results}

    return router
