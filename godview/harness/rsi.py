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

import asyncio
import inspect
import logging
import os
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
                predicate=lambda d: (d.get("outcome") or d.get("status")) in ("resolved", "mitigated", "false_positive"),
            )
            for inc in incidents:
                ep = dict(inc.get("evidence_pack") or {})
                root_cause = inc.get("root_cause_category") or inc.get("category")
                remediation = inc.get("remediation_capability") or inc.get("capability_id")
                resolution_summary = inc.get("resolution_summary") or inc.get("summary")
                scenarios.append(
                    {
                        "task_id": inc.get("incident_id") or inc.get("doc_id") or "archived-inc",
                        "domain": domain,
                        "description": resolution_summary or f"Archived incident in {domain}",
                        "outcome": inc.get("outcome") or inc.get("status"),
                        "root_cause_category": root_cause,
                        "remediation_capability": remediation,
                        "resolution_summary": resolution_summary,
                        **ep,
                    }
                )
        except Exception as exc:
            logger.debug("Failed to fetch archived incidents from docs store: %s", exc)

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
        adapter = GodViewCourtAdapter(default_domain=domain, ctx=ctx)

        # 1. Register archived evidence packs as fitness replay scenarios
        archived_cases = _collect_archived_evidence(ctx, domain)
        for case in archived_cases:
            adapter.register_scenario(case["task_id"], case)

        if evidence_pack:
            scenario_entry = {
                "task_id": t_id,
                "domain": domain,
                "description": f"Trigger `{trigger}` telemetry pack for {agent_id}",
                "outcome": "resolved",
                **evidence_pack,
            }
            adapter.register_scenario(t_id, scenario_entry)
            archived_cases.append(scenario_entry)

        # If no archived cases with known outcomes exist, skip evolution
        if not archived_cases:
            logger.info("No archived incidents with known outcomes found for domain %s; skipping evolution", domain)
            current_version = 1
            if ctx is not None and hasattr(ctx, "docs") and ctx.docs is not None:
                doc = ctx.docs.get("court_agents", agent_id)
                if doc and "living_model_version" in doc:
                    current_version = int(doc["living_model_version"])
            return EvolutionResult(
                agent_id=agent_id,
                domain=domain,
                trigger=trigger,
                task_id=t_id,
                passed=False,
                score=0.0,
                living_model_version=current_version,
                winning_tag="none",
                notes="Skipped evolution: no archived cases with known outcomes available.",
            )

        # 2. Build proposer using ctx.llm via ADC
        llm_client = getattr(ctx, "llm", None)
        proposer = VertexGeminiProposer(ctx=ctx, client=llm_client)

        # 3. Build CloudSyncedLibrary with GCS & Firestore sync
        docs_store = getattr(ctx, "docs", None)
        auth_service = getattr(ctx, "auth", None)
        gcs_client = storage_client
        if gcs_client is None:
            bucket_name = (
                getattr(getattr(ctx, "settings", None), "agent_skills_bucket", None)
                or os.environ.get("AGENT_SKILLS_BUCKET")
            )
            if bucket_name:
                try:
                    from godview.invariants.skills import GcsSkillsStorageClient

                    gcs_client = GcsSkillsStorageClient(bucket=bucket_name, auth=auth_service)
                except Exception as gcs_exc:
                    logger.debug("Could not initialize GcsSkillsStorageClient: %s", gcs_exc)

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
            else:
                new_version = 2

        # Record evolution run across Firestore collections
        run_record = {
            "agent_id": agent_id,
            "domain": domain,
            "task_id": t_id,
            "trigger": trigger,
            "winner_tag": winner.tag,
            "score": winner.score,
            "passed": winner.passed,
            "notes": winner.notes,
            "living_model_version": new_version,
            "adopted_path": str(final_res.adopted_path) if final_res.adopted_path else None,
            "created_at_epoch": time.time(),
            "updated_at_epoch": time.time(),
        }
        if docs_store is not None and hasattr(docs_store, "set"):
            try:
                run_id = f"hrun-{domain}-{int(time.time())}"
                docs_store.set("harness_runs", run_id, run_record, merge=True)
                docs_store.set("harness_trees", f"{domain}__{t_id}", run_record, merge=True)
                docs_store.set("skills", f"{domain}__{t_id}", run_record, merge=True)
                docs_store.set("skill_versions", f"{domain}__v{new_version}", run_record, merge=True)
            except Exception as e:
                logger.warning("Failed to record evolution in firestore: %s", e)

        # Bump living_model_version on all matching court_agents docs for this domain
        env_project = (
            os.environ.get("GCP_PROJECT_ID", "").strip()
            or os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
        )
        candidate_ca_ids = {
            agent_id,
            domain,
            f"{domain}__specialist",
            f"{domain}__env",
            f"{domain}__default",
        }
        if env_project:
            candidate_ca_ids.add(f"{domain}__{env_project}")
        if docs_store is not None and hasattr(docs_store, "list_docs"):
            try:
                for ca in docs_store.list_docs("court_agents"):
                    if isinstance(ca, dict):
                        ca_id = ca.get("_id") or ca.get("doc_id") or ca.get("id")
                        ca_type = ca.get("agent_type")
                        if ca_id and (ca_id.startswith(f"{domain}__") or ca_type == domain):
                            candidate_ca_ids.add(ca_id)
            except Exception:
                pass

        for ca_id in candidate_ca_ids:
            if docs_store is not None and hasattr(docs_store, "set"):
                try:
                    cur_doc = docs_store.get("court_agents", ca_id) if hasattr(docs_store, "get") else {}
                    cur_v = (cur_doc.get("living_model_version") if isinstance(cur_doc, dict) else 1) or 1
                    bump_v = max(cur_v + 1, new_version, 2)
                    docs_store.set(
                        "court_agents",
                        ca_id,
                        {
                            "living_model_version": bump_v,
                            "last_evolution_at": time.time(),
                            "last_evolution_score": winner.score,
                            "last_evolution_trigger": trigger,
                        },
                        merge=True,
                    )
                except Exception as e:
                    logger.warning("Failed to bump court_agent %s version: %s", ca_id, e)

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


class HookDispatcher:
    """Callable wrapper that allows hooks to be called as functions or iterated as lists."""

    def __init__(self, existing: Any = None) -> None:
        self._callbacks: list[Any] = []
        if callable(existing):
            self._callbacks.append(existing)
        elif isinstance(existing, (list, tuple)):
            self._callbacks.extend(existing)

    def append(self, cb: Any) -> None:
        if cb not in self._callbacks:
            self._callbacks.append(cb)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        results = []
        for cb in list(self._callbacks):
            try:
                res = cb(*args, **kwargs)
                if inspect.isawaitable(res):
                    try:
                        loop = asyncio.get_running_loop()
                        res = loop.create_task(res)
                    except RuntimeError:
                        pass
                results.append(res)
            except Exception as exc:
                logger.warning("Hook callback failed: %s", exc)
        return results

    def __iter__(self) -> Any:
        return iter(self._callbacks)

    def __len__(self) -> int:
        return len(self._callbacks)


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

    def _on_incident_closed(incident_doc: Any) -> None:
        data = (
            incident_doc.model_dump(mode="json")
            if hasattr(incident_doc, "model_dump")
            else dict(incident_doc or {})
        )
        status = str(data.get("outcome") or data.get("status") or "resolved")
        inc_id = str(data.get("incident_id") or data.get("doc_id") or "inc-closed")
        ep = dict(data.get("evidence_pack") or {})

        # Resolve domain from data, lead_agent_id, or dedupe_key
        domain = str(data.get("domain") or "")
        if not domain or domain == "application":
            lead_agent = str(data.get("lead_agent_id") or "")
            if "__" in lead_agent:
                domain = lead_agent.split("__")[0]
            else:
                dedupe_k = str(data.get("dedupe_key") or "").lower()
                target_r = str(data.get("target_resource") or "").lower()
                if any(k in dedupe_k or k in target_r for k in ("posture", "lb_requests", "armor", "policy", "probe", "gke", "cloud_run")):
                    domain = "platform"
                elif any(k in dedupe_k or k in target_r for k in ("postgres", "sql", "db", "dataset")):
                    domain = "data"
                elif any(k in dedupe_k or k in target_r for k in ("commit", "deploy", "build", "change")):
                    domain = "change"
                else:
                    domain = "platform"
        agent_id = f"{domain}__specialist"

        if "root_cause_category" in data and "root_cause_category" not in ep:
            ep["root_cause_category"] = data["root_cause_category"]
        if "remediation_capability" in data and "remediation_capability" not in ep:
            ep["remediation_capability"] = data["remediation_capability"]
        if "resolution_summary" in data and "resolution_summary" not in ep:
            ep["resolution_summary"] = data["resolution_summary"]

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                asyncio.to_thread(
                    evolve_specialist,
                    agent_id=agent_id,
                    trigger=f"incident_closed:{status}",
                    ctx=ctx,
                    task_id=inc_id,
                    evidence_pack=ep,
                )
            )
        except RuntimeError:
            evolve_specialist(
                agent_id=agent_id,
                trigger=f"incident_closed:{status}",
                ctx=ctx,
                task_id=inc_id,
                evidence_pack=ep,
            )

    def _on_approval_decided(approval_doc: Any) -> None:
        data = (
            approval_doc.model_dump(mode="json")
            if hasattr(approval_doc, "model_dump")
            else dict(approval_doc or {})
        )
        state = str(data.get("state") or "approved")
        app_id = str(data.get("approval_id") or data.get("doc_id") or "appr-decided")
        lead_ag = str(data.get("agent_id") or "")
        dom = lead_ag.split("__")[0] if "__" in lead_ag else "platform"
        agent_id = f"{dom}__specialist"
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                asyncio.to_thread(
                    evolve_specialist,
                    agent_id=agent_id,
                    trigger=f"approval_decided:{state}",
                    ctx=ctx,
                    task_id=app_id,
                    evidence_pack=data.get("args") or {},
                )
            )
        except RuntimeError:
            evolve_specialist(
                agent_id=agent_id,
                trigger=f"approval_decided:{state}",
                ctx=ctx,
                task_id=app_id,
                evidence_pack=data.get("args") or {},
            )

    # Register on hooks using HookDispatcher
    for hook_name, callback in (
        ("on_incident_closed", _on_incident_closed),
        ("on_approval_decided", _on_approval_decided),
    ):
        existing = getattr(hooks, hook_name, None)
        if isinstance(existing, HookDispatcher):
            existing.append(callback)
        else:
            dispatcher = HookDispatcher(existing)
            dispatcher.append(callback)
            setattr(hooks, hook_name, dispatcher)

    # In GCP mode, schedule a one-time startup task to replay any previously resolved incidents
    # that have not yet had their Harness2 RSI evolution recorded.
    if os.environ.get("GODVIEW_ENV", "hermetic").lower() == "gcp":

        async def _replay_closed_incidents_startup() -> None:
            await asyncio.sleep(4.0)
            docs = getattr(ctx, "docs", None)
            if docs is not None and hasattr(docs, "list_docs"):
                try:
                    resolved = docs.list_docs(
                        "incidents",
                        predicate=lambda d: (d.get("outcome") or d.get("status"))
                        in ("resolved", "mitigated", "false_positive"),
                    )
                    existing_trees = set()
                    for t in docs.list_docs("harness_trees"):
                        tid = t.get("task_id")
                        if tid:
                            existing_trees.add(tid)
                        oid = t.get("_id")
                        if oid:
                            existing_trees.add(oid)

                    for inc in resolved:
                        inc_id = str(inc.get("incident_id") or inc.get("doc_id") or "inc")
                        if inc_id not in existing_trees and not any(
                            inc_id in str(e) for e in existing_trees
                        ):
                            logger.info(
                                "Auto-replaying closed incident %s for Harness2 RSI evolution",
                                inc_id,
                            )
                            _on_incident_closed(inc)
                except Exception as e:
                    logger.warning("Auto-replay of closed incidents failed: %s", e)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_replay_closed_incidents_startup())
        except RuntimeError:
            pass
