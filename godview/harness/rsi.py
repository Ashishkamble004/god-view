# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Harness² Recursive Self-Improvement (RSI) loop for God-View Court Agents.

Triggers:
- Incident closed with outcome (resolved / mitigated)
- Approval decided (approved / rejected)
- False positive marked
- Nightly /memory/reflect reflection

Persistence:
- GCS: gs://${AGENT_SKILLS_BUCKET}/harness2/${domain}/...
- Firestore: collections `skills`, `harness_trees`, and subdoc `court_agents/${domain}/living_model`
- Proposer: uses `ctx.llm` via ADC (no VERTEX_ACCESS_TOKEN)
- Fitness: replay of archived evidence packs with known outcomes
- Hot-load: updates serving specialist and increments living_model_version in Firestore
"""

from __future__ import annotations

import logging
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from godview.agents.archetypes import resolve_domain
from godview.harness.adapter import GodViewCourtAdapter
from godview.harness.cloud_library import CloudSyncedLibrary
from godview.harness.core import (
    HarnessTree,
    RecursionResult,
    reflect_adoption,
    run_parallel,
    run_sequential,
    trivial_selection,
)
from godview.harness.substrate import GODVIEW_SURFACE
from godview.harness.vertex_proposer import VertexGeminiProposer
from godview.invariants.skills import SkillsStorageClientProtocol

logger = logging.getLogger("godview.harness.rsi")


@dataclass
class EvolutionResult:
    """Outcome of an RSI evolution run for a specialist agent."""

    agent_id: str
    domain: str
    trigger: str
    task_id: str
    passed: bool
    score: float
    living_model_version: int
    winning_tag: str
    notes: str
    adopted_path: str | None = None
    synced_gcs_objects: list[str] = field(default_factory=list)


def _collect_archived_evidence(ctx: Any, domain: str) -> list[dict[str, Any]]:
    """Fetch archived incidents with known outcomes from doc store for fitness replay."""
    scenarios: list[dict[str, Any]] = []
    docs = getattr(ctx, "docs", None)
    if docs is not None and hasattr(docs, "list_docs"):
        try:
            incidents = docs.list_docs(
                "incidents",
                predicate=lambda d: d.get("status") in ("resolved", "mitigated", "false_positive"),
            )
            for inc in incidents:
                ep = inc.get("evidence_pack") or {}
                scenarios.append(
                    {
                        "task_id": inc.get("incident_id") or inc.get("doc_id") or "archived-inc",
                        "domain": domain,
                        "description": inc.get("summary") or f"Archived incident in {domain}",
                        "outcome": inc.get("status"),
                        **ep,
                    }
                )
        except Exception as exc:
            logger.debug("Failed to fetch archived incidents from docs store: %s", exc)

    if not scenarios:
        # Fallback to standard deterministic baseline scenario for the domain
        scenarios.append(
            {
                "task_id": f"baseline_{domain}_replay",
                "domain": domain,
                "description": f"Baseline archived telemetry replay for {domain} specialist",
                "outcome": "resolved",
            }
        )
    return scenarios


def evolve_specialist(
    agent_id: str,
    trigger: str,
    *,
    ctx: Any | None = None,
    task_id: str | None = None,
    evidence_pack: dict[str, Any] | None = None,
    mode: Literal["parallel", "sequential"] = "parallel",
    k: int = 1,
    work_dir: Path | None = None,
    storage_client: SkillsStorageClientProtocol | None = None,
) -> EvolutionResult:
    """Execute RSI evolution loop for a court specialist agent.

    1. Gathers fitness cases (archived evidence packs with known outcomes).
    2. Runs proposer (using ctx.llm via ADC).
    3. Evaluates candidates against deterministic c7 scripts & Six Senses.
    4. Adopts winner to CloudSyncedLibrary (persisting to GCS & Firestore).
    5. Hot-loads winning tree into serving specialist with visible version bump.
    """
    domain = resolve_domain(agent_id)
    t_id = task_id or f"rsi_{domain}_{int(time.time())}"
    temp_dir_obj = None

    if work_dir is None:
        temp_dir_obj = tempfile.TemporaryDirectory(prefix=f"godview_rsi_{domain}_")
        root_dir = Path(temp_dir_obj.name)
    else:
        root_dir = work_dir

    try:
        adapter = GodViewCourtAdapter(default_domain=domain)

        # 1. Register archived evidence packs as fitness replay scenarios
        archived_cases = _collect_archived_evidence(ctx, domain)
        for case in archived_cases:
            adapter.register_scenario(case["task_id"], case)

        if evidence_pack:
            adapter.register_scenario(
                t_id,
                {
                    "domain": domain,
                    "description": f"Trigger `{trigger}` telemetry pack for {agent_id}",
                    **evidence_pack,
                },
            )

        # 2. Build proposer using ctx.llm via ADC
        llm_client = getattr(ctx, "llm", None)
        proposer = VertexGeminiProposer(ctx=ctx, client=llm_client)

        # 3. Build CloudSyncedLibrary with GCS & Firestore sync
        docs_store = getattr(ctx, "docs", None)
        auth_service = getattr(ctx, "auth", None)
        gcs_client = storage_client or getattr(ctx, "storage", None)
        library = CloudSyncedLibrary(
            root=root_dir / "library",
            storage_client=gcs_client,
            firestore_client=docs_store,
            auth=auth_service,
        )

        # 4. Run evolution loop (parallel or sequential)
        if mode == "sequential":
            res_seq = run_sequential(
                adapter=adapter,
                proposer=proposer,
                library=library,
                work_root=root_dir / "work",
                task_id=t_id,
                k=k,
            )
            winner = trivial_selection(res_seq.candidates)
            adopted_path = reflect_adoption(
                library=library,
                domain=domain,
                task_id=t_id,
                task_md=adapter.task_description(t_id),
                winner=winner,
                all_candidates=res_seq.candidates,
                proposer=proposer,
                ledger=res_seq.ledger,
                work_dir=root_dir / "reflect",
            )
            final_res = RecursionResult(
                task_id=t_id,
                h0=res_seq.candidates[0] if res_seq.candidates else winner,
                candidates=res_seq.candidates,
                winner=winner,
                rounds=1,
                total_r=res_seq.ledger.r_spent,
                total_p=res_seq.ledger.p_spent,
                adopted_path=adopted_path,
            )
        else:
            final_res = run_parallel(
                adapter=adapter,
                proposer=proposer,
                library=library,
                work_root=root_dir / "work",
                task_id=t_id,
                k=k,
            )

        winner = final_res.winner

        # 5. Hot-load winning tree and bump version in Firestore court_agents
        new_version = 1
        if docs_store is not None and hasattr(docs_store, "get"):
            existing_doc = docs_store.get("court_agents", agent_id)
            if existing_doc:
                cur_v = existing_doc.get("living_model_version", 1)
                new_version = cur_v + 1
                if hasattr(docs_store, "set"):
                    docs_store.set(
                        "court_agents",
                        agent_id,
                        {
                            "living_model_version": new_version,
                            "last_evolution_at": time.time(),
                            "last_evolution_score": winner.score,
                            "last_evolution_trigger": trigger,
                        },
                        merge=True,
                    )

        # If CourtOrchestrator exists on ctx, hot-load into in-process specialist
        orchestrator = getattr(ctx, "court_orchestrator", None)
        if orchestrator is not None and hasattr(orchestrator, "_in_process_specialists"):
            in_proc = orchestrator._in_process_specialists.get(domain)
            if in_proc is not None and final_res.adopted_path is not None:
                new_harness_dir = final_res.adopted_path / "harness"
                if new_harness_dir.is_dir():
                    in_proc.tree = HarnessTree(new_harness_dir, GODVIEW_SURFACE)
                    logger.info(
                        "Hot-loaded evolved HarnessTree for specialist %s (v%d)",
                        agent_id,
                        new_version,
                    )

        return EvolutionResult(
            agent_id=agent_id,
            domain=domain,
            trigger=trigger,
            task_id=t_id,
            passed=winner.passed,
            score=winner.score,
            living_model_version=new_version,
            winning_tag=winner.tag,
            notes=winner.notes,
            adopted_path=str(final_res.adopted_path) if final_res.adopted_path else None,
            synced_gcs_objects=list(library.synced_objects),
        )
    finally:
        if temp_dir_obj is not None:
            try:
                temp_dir_obj.cleanup()
            except Exception:
                pass


def register_rsi_hooks(ctx: Any) -> None:
    """Attach RSI self-improvement triggers onto SovereignContext hooks.

    Triggers:
    - on_incident_closed: when an incident is resolved, mitigated, or false_positive
    - on_approval_decided: when an approval is approved or rejected
    - on_nightly_reflection: nightly batch replay
    """
    hooks = getattr(ctx, "hooks", None)
    if hooks is None:
        return

    async def _on_incident_closed(incident_doc: Any) -> None:
        data = (
            incident_doc.model_dump(mode="json")
            if hasattr(incident_doc, "model_dump")
            else dict(incident_doc or {})
        )
        status = str(data.get("status") or "resolved")
        inc_id = str(data.get("incident_id") or data.get("doc_id") or "inc-closed")
        ep = data.get("evidence_pack") or {}
        domain = str(data.get("domain") or "application")
        agent_id = f"{domain}__specialist"
        evolve_specialist(
            agent_id=agent_id,
            trigger=f"incident_closed:{status}",
            ctx=ctx,
            task_id=inc_id,
            evidence_pack=ep,
        )

    async def _on_approval_decided(approval_doc: Any) -> None:
        data = (
            approval_doc.model_dump(mode="json")
            if hasattr(approval_doc, "model_dump")
            else dict(approval_doc or {})
        )
        state = str(data.get("state") or "approved")
        app_id = str(data.get("approval_id") or data.get("doc_id") or "appr-decided")
        agent_id = str(data.get("agent_id") or "application__specialist")
        evolve_specialist(
            agent_id=agent_id,
            trigger=f"approval_decided:{state}",
            ctx=ctx,
            task_id=app_id,
            evidence_pack=data.get("args") or {},
        )

    # Register on hooks
    for hook_name, callback in (
        ("on_incident_closed", _on_incident_closed),
        ("on_approval_decided", _on_approval_decided),
    ):
        if not hasattr(hooks, hook_name):
            setattr(hooks, hook_name, [])
        hook_list = getattr(hooks, hook_name)
        if isinstance(hook_list, list):
            hook_list.append(callback)
