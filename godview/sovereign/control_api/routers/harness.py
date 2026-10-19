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
import os
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
    settings = getattr(ctx, "settings", None)
    env_mode = getattr(settings, "env_mode", None) or os.environ.get("GODVIEW_ENV", "hermetic").lower()
    auth = getattr(ctx, "auth", None)
    if auth is not None:
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

    c1_text = c1_path.read_text(encoding="utf-8") if c1_path.is_file() else ""
    c2_text = c2_path.read_text(encoding="utf-8") if c2_path.is_file() else ""
    c3_text = c3_path.read_text(encoding="utf-8") if c3_path.is_file() else ""
    c4_text = c4_path.read_text(encoding="utf-8") if c4_path.is_file() else ""

    hooks_path = root / "hooks.json"
    hooks_dict: dict[str, Any] = {}
    if hooks_path.is_file():
        try:
            hooks_dict = json.loads(hooks_path.read_text(encoding="utf-8"))
        except Exception:
            hooks_dict = {}

    sub_agents_detail = []
    for p in tree.sub_agents():
        sub_agents_detail.append(
            {
                "name": p.parent.name,
                "path": str(p.relative_to(root)),
                "content": p.read_text(encoding="utf-8") if p.is_file() else "",
            }
        )

    skills_detail = []
    for p in tree.skills():
        skills_detail.append(
            {
                "name": p.parent.name,
                "path": str(p.relative_to(root)),
                "content": p.read_text(encoding="utf-8") if p.is_file() else "",
            }
        )

    scripts_detail = []
    for p in tree.scripts():
        scripts_detail.append(
            {
                "name": p.name,
                "path": str(p.relative_to(root)),
                "content": p.read_text(encoding="utf-8") if p.is_file() else "",
            }
        )

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
            "c1_identity": c1_text,
            "c2_tool_descriptions": c2_text,
            "c3_guardrails": c3_text,
            "c4_long_term_memory": c4_text,
            "c5_sub_agents": [p.parent.name for p in tree.sub_agents()],
            "c6_skills": [p.parent.name for p in tree.skills()],
            "c7_scripts": [p.name for p in tree.scripts()],
            "c8_hooks": hooks_dict,
        },
        "categories_detail": {
            "c5_sub_agents": sub_agents_detail,
            "c6_skills": skills_detail,
            "c7_scripts": scripts_detail,
            "token_breakdown": {
                "c1_identity_tokens": max(1, len(c1_text) // 4),
                "c2_tool_descriptions_tokens": max(1, len(c2_text) // 4),
                "c3_guardrails_tokens": max(1, len(c3_text) // 4),
                "c4_long_term_memory_tokens": max(1, len(c4_text) // 4),
                "always_on_total": report.always_on_tokens,
                "always_on_max": tree.surface.always_on_max,
            },
        },
    }


def _build_harness_overview(ctx: Any | None = None) -> dict[str, Any]:
    archetypes: list[dict[str, Any]] = []
    for dom in ("application", "data", "business_line", "change", "platform"):
        aid = f"{dom}__specialist"
        d_name, tree, ver = _get_agent_tree(aid, ctx=ctx)
        archetypes.append(_serialize_tree(aid, d_name, tree, version=ver))

    return {
        "surface": {
            "name": GODVIEW_SURFACE.name,
            "always_on_max": GODVIEW_SURFACE.always_on_max,
            "categories": list(GODVIEW_SURFACE.categories),
            "forbidden_imports": sorted(GODVIEW_SURFACE.forbidden_script_imports),
            "allowed_hook_events": sorted(GODVIEW_SURFACE.allowed_hook_events),
        },
        "recursion_architecture": {
            "title": "Harness² Two-Level Recursive Self-Improvement (RSI)",
            "reference": "google-research/harness2",
            "inner_loop": {
                "name": "Level 1 — Specialist Court Execution Loop",
                "parameter": "P = 3,000 tokens",
                "budget_tokens": 3000,
                "model": DEFAULT_AGENT_MODEL_ID,
                "description": (
                    "Executes specialist court diagnosis, evidence pack analysis, and TrustGate-governed "
                    "tool invocations under C1-C4 always-on context (<=12,000 tokens) and C3 autotrigger guardrails."
                ),
            },
            "outer_loop": {
                "name": "Level 2 — Harness Proposer & Hill-Climbing Loop",
                "parameter": "R = 4,000 tokens",
                "budget_tokens": 4000,
                "model": DEFAULT_AGENT_MODEL_ID,
                "description": (
                    "Diagnoses EXPERIENCE.md failure modes after incident resolution or scheduled reflection, "
                    "proposes bounded file mutations across C1-C8 (parallel k=4 or sequential hill-climbing), "
                    "validates via AST sandbox + 12k token cap, and promotes winning HarnessTrees to Cloud Storage & Firestore."
                ),
            },
        },
        "archetypes": archetypes,
        "blueprint": {
            "version": "4.0.0",
            "package_name": "god-view",
            "cli_entrypoint": "godview",
            "organs": [
                {
                    "id": "perception",
                    "name": "PerceptionOrgan",
                    "module": "godview.sovereign.perception",
                    "role": "6-Step Self-Awareness Protocol: discovers identity, fingerprints environment, probes 15+ SenseAdapters, builds Topology & BusinessContext, and computes Court Spawn Plan.",
                },
                {
                    "id": "sentinel_engine",
                    "name": "SentinelEngine",
                    "module": "godview.sovereign.sentinel_engine",
                    "role": "Zero-LLM deterministic SQL, metric, and synthetic probe evaluator running on a 10s cadence (60s in hibernate). Tokens at rest = 0.",
                },
                {
                    "id": "nervous_system",
                    "name": "NervousSystemRouter",
                    "module": "godview.sovereign.nervous_system",
                    "role": "CloudEvents 1.0 event bus over Pub/Sub & Cloud Audit Logs with DLQ watchdog and push ingestion.",
                },
                {
                    "id": "court_manager",
                    "name": "CourtManager & CourtOrchestrator",
                    "module": "godview.sovereign.court_manager",
                    "role": "Manages specialist agents across 5 archetypes (application, data, business_line, change, platform) via A2A JSON-RPC 2.0 and TokenGovernor daily ceilings.",
                },
                {
                    "id": "trust_gate",
                    "name": "TrustGate (L0-L4)",
                    "module": "godview.sovereign.trust_ladder",
                    "role": "Deterministic capability gate enforcing L0 Read-Only, L1 Low-Risk, L2 Medium-Risk, L3 High-Risk, and L4 Critical human-in-the-loop approvals with mandatory reversal recipes.",
                },
                {
                    "id": "harness_rsi",
                    "name": "Harness² Recursive Engine",
                    "module": "godview.harness.rsi",
                    "role": "Evolves specialist agent C1-C8 HarnessTrees from incident outcomes and reflection runs with AST-sandboxed C7 scripts and 12k always-on token verification.",
                },
            ],
            "senses": [
                {"id": "INFRASTRUCTURE", "name": "Infrastructure Sense", "adapters": ["cloud-run", "gke", "gce", "load-balancing", "cloud-armor", "serverless-neg", "iam"]},
                {"id": "SCOPED_OBSERVABILITY", "name": "Scoped Observability Sense", "adapters": ["cloud-monitoring", "cloud-logging", "cloud-trace", "otlp-ingest"]},
                {"id": "CODE", "name": "Code & Runtime Sense", "adapters": ["source-repos", "container-images", "vertex-agent-engine"]},
                {"id": "CONTRACTS", "name": "API & Schema Contracts Sense", "adapters": ["openapi-discovery", "grpc-reflection", "pubsub-schemas"]},
                {"id": "DATA_LINEAGE", "name": "Data & Storage Lineage Sense", "adapters": ["cloud-sql", "firestore", "bigquery", "gcs", "pubsub"]},
                {"id": "CHANGE_PLANE", "name": "Change & Deployment Plane Sense", "adapters": ["cloud-audit-logs", "github-webhooks", "terraform-drift"]},
            ],
            "invariants": [
                {"id": "I-01", "title": "Zero Tokens at Rest", "rule": "Sentinel evaluation, health checks, and steady-state polling consume 0 LLM tokens."},
                {"id": "I-02", "title": "Identity-Only Boot & Autonomous Perception", "rule": "Boots with zero hardcoded service lists; discovers the entire estate autonomously via SenseAdapters."},
                {"id": "I-03", "title": "Strict Environment Neutrality", "rule": "Core organs and shared contracts contain zero hardcoded tenant names, IPs, or domain stems."},
                {"id": "I-04", "title": "Single TrustGate Mutation Chokepoint", "rule": "Every mutating capability invocation passes through TrustGate with an explicit ReversalRecipe."},
                {"id": "I-05", "title": "Observer Self-Exclusion", "rule": "God-View classifies its own control-plane resources as observer tier and never mutates itself via remediation."},
                {"id": "I-06", "title": "Bounded Token Governor", "rule": "Daily USD ceilings and 20% Sev-1 escalation reserve are enforced before every LLM call."},
                {"id": "I-07", "title": "Harness² 12k Always-On Cap & AST Sandbox", "rule": "C1-C4 context is capped at 12,000 tokens; C7 scripts forbid network/subprocess/cloud SDK imports."},
            ],
            "portable_deployments": [
                {
                    "target": "cli",
                    "title": "Portable Python Package & CLI Utility",
                    "install": "uv pip install -e .  # or: pip install god-view",
                    "commands": [
                        "godview doctor --env gcp",
                        "godview perceive --project $GOOGLE_CLOUD_PROJECT",
                        "godview harness inspect --agent application__specialist",
                        "godview serve --port 8080",
                    ],
                },
                {
                    "target": "docker",
                    "title": "Local Docker Compose / Container Utility",
                    "install": "docker compose up --build -d",
                    "commands": [
                        "godview deploy --target docker --env local",
                        "docker run -p 8080:8080 -e GODVIEW_ENV=gcp -e GOOGLE_CLOUD_PROJECT=$PROJECT_ID godview-sovereign:latest",
                    ],
                },
                {
                    "target": "cloud_run",
                    "title": "Google Cloud Run + Terraform Module",
                    "install": "terraform -chdir=infra/terraform/platform apply",
                    "commands": [
                        "godview deploy --target cloud-run --project $PROJECT_ID --region us-central1",
                        "gcloud run deploy god-view-sovereign --source . --region us-central1",
                    ],
                },
                {
                    "target": "kubernetes",
                    "title": "Kubernetes / GKE Cluster Utility",
                    "install": "kubectl apply -f infra/k8s/godview-sovereign.yaml",
                    "commands": [
                        "godview deploy --target k8s --namespace godview-system",
                    ],
                },
            ],
        },
    }


def build_router(ctx: Any) -> APIRouter:
    """Build and return FastAPI router for `/harness` and agent reflection."""
    router = APIRouter(tags=["harness"])

    async def auth_dependency(request: Request) -> Any:
        return await _require_auth(request, ctx)

    @router.get("/harness/overview")
    async def get_harness_overview() -> dict[str, Any]:
        return _build_harness_overview(ctx=ctx)

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
        body = payload or {}
        src = body.get("source", "nightly")
        action = body.get("action", "reflect")
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
        return {"status": "ok", "state": "completed", "action": action, "results": results}

    return router
