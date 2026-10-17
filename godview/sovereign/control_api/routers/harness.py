# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Harness² inspection, reflection, and evolution routes for Sovereign Control API.

Exposes:
- GET /harness/{agent_id}/tree: returns full HarnessTree components and living_model_version
- POST /harness/{agent_id}:evolve: triggers RSI evolution loop non-blocking
- GET /agents/{agent_id}/harness & /experience (backward compatibility)
- POST /sovereign/reflect & /memory/reflect: triggers batch reflection
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from godview_shared.config import DEFAULT_AGENT_MODEL_ID
from pydantic import BaseModel, Field

from godview.agents.archetypes import resolve_domain
from godview.harness.core import HarnessTree, validate
from godview.harness.rsi import EvolutionResult, evolve_specialist
from godview.harness.substrate import GODVIEW_SURFACE, GodViewSubstrate

logger = logging.getLogger("godview.control_api.routers.harness")


class EvolvePayload(BaseModel):
    """Payload to trigger an RSI evolution run."""

    trigger: str = Field(default="manual")
    task_id: str | None = None
    evidence_pack: dict[str, Any] | None = None
    mode: Literal["parallel", "sequential"] = "parallel"
    k: int = Field(default=1, ge=1, le=4)


class ReflectPayload(BaseModel):
    """Payload for sovereign reflection."""

    agent_id: str | None = None
    source: str = "operator_manual"
    task_id: str | None = None
    mode: Literal["parallel", "sequential"] = "parallel"
    k: int = Field(default=1, ge=1, le=4)


EvolvePayload.model_rebuild()
ReflectPayload.model_rebuild()


async def _require_auth(request: Request, ctx: Any) -> Any:
    auth = getattr(ctx, "auth", None)
    if auth is None:
        return None
    p = auth.verify_iap_jwt(request)
    if p:
        return p
    p = auth.verify_google_oidc(request)
    if p:
        return p
    raise HTTPException(
        status_code=401,
        detail="Unauthorized: operator (IAP) or service (OIDC) identity required",
    )


def _get_agent_tree(agent_id: str, ctx: Any | None = None) -> tuple[str, HarnessTree, int]:
    domain = resolve_domain(agent_id)
    version = 1
    adopted_tree_path: Path | None = None

    if ctx is not None and hasattr(ctx, "docs") and ctx.docs is not None:
        doc = ctx.docs.get("court_agents", agent_id) or ctx.docs.get("court_agents", domain)
        if doc:
            if "living_model_version" in doc:
                version = int(doc["living_model_version"])
            if "adopted_path" in doc and doc["adopted_path"]:
                cand = Path(doc["adopted_path"])
                if cand.is_dir():
                    adopted_tree_path = cand

    if adopted_tree_path is not None:
        return domain, HarnessTree(root=adopted_tree_path, surface=GODVIEW_SURFACE), version

    substrate = GodViewSubstrate(domain=domain)
    base_dir = substrate.base_tree()
    return domain, HarnessTree(root=base_dir, surface=GODVIEW_SURFACE), version


def _serialize_tree(agent_id: str, domain: str, tree: HarnessTree, version: int = 1) -> dict[str, Any]:
    report = validate(tree)
    root = tree.root

    c1_path = root / "systemprompt.md"
    c2_path = root / "tool_descriptions" / "tool_guidance.md"
    c3_path = root / "guardrails.md"
    c4_path = root / "LongTermMEMORY.md"

    hooks_path = root / "hooks.json"
    hooks_dict: dict[str, Any] = {}
    if hooks_path.is_file():
        try:
            hooks_dict = json.loads(hooks_path.read_text(encoding="utf-8"))
        except Exception:
            hooks_dict = {}

    return {
        "agent_id": agent_id,
        "domain": domain,
        "surface": "godview",
        "living_model_version": version,
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

    async def auth_dependency(request: Request) -> Any:
        return await _require_auth(request, ctx)

    @router.get("/harness/{agent_id}/tree")
    async def get_harness_tree(agent_id: str) -> dict[str, Any]:
        domain, tree, version = _get_agent_tree(agent_id, ctx=ctx)
        return _serialize_tree(agent_id, domain, tree, version=version)

    @router.post("/harness/{agent_id}:evolve", dependencies=[Depends(auth_dependency)])
    async def evolve_agent_tree(agent_id: str, payload: EvolvePayload) -> dict[str, Any]:
        res: EvolutionResult = await asyncio.to_thread(
            evolve_specialist,
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
        domain, tree, version = _get_agent_tree(agent_id, ctx=ctx)
        return _serialize_tree(agent_id, domain, tree, version=version)

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

    @router.post("/sovereign/reflect", dependencies=[Depends(auth_dependency)])
    async def sovereign_reflect(payload: ReflectPayload) -> dict[str, Any]:
        aid = payload.agent_id or "application__specialist"
        res: EvolutionResult = await asyncio.to_thread(
            evolve_specialist,
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

    @router.post("/memory/reflect", dependencies=[Depends(auth_dependency)])
    async def memory_reflect(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        src = (payload or {}).get("source", "nightly")
        results = []
        for dom in ("application", "data", "business_line", "change", "platform"):
            aid = f"{dom}__specialist"
            try:
                res = await asyncio.to_thread(
                    evolve_specialist,
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
