# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Harness² inspection and reflection routes mounted under `/v1` and `/api/v1`."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from godview_shared.fakes import InMemoryFirestore, InMemoryPubSub

from godview.agents.archetypes import resolve_domain
from godview.agents.court_agent import CourtAgent, CourtSwarmSession
from godview.harness.cloud_library import CloudSyncedLibrary
from godview.sovereign.court_manager.token_governor import TokenBudgetGovernor
from godview.sovereign.trust_ladder import TrustGate


def create_harness_inspection_router(
    firestore: InMemoryFirestore | None = None,
    token_governor: TokenBudgetGovernor | None = None,
    trust_gate: TrustGate | None = None,
    library_root: Path | None = None,
) -> APIRouter:
    """Create the FastAPI router for Harness² inspection and reflection endpoints:
    - `GET /agents/{agent_id}/harness`
    - `GET /agents/{agent_id}/experience`
    - `GET /incidents/{incident_id}/harness-run`
    - `POST /sovereign/reflect`
    """
    fs = firestore or InMemoryFirestore()
    ps = InMemoryPubSub()
    gov = token_governor or TokenBudgetGovernor(firestore=fs, pubsub=ps)
    gate = trust_gate or TrustGate(firestore=fs, pubsub=ps, token_governor=gov)
    lib_dir = library_root or (Path(tempfile.gettempdir()) / "godview_harness2_library")
    cloud_lib = CloudSyncedLibrary(root=lib_dir, firestore_client=fs)

    agent_cache: dict[str, CourtAgent] = {}
    incident_runs: dict[str, dict[str, Any]] = {}

    def _get_or_create_agent(agent_id: str) -> CourtAgent:
        if agent_id not in agent_cache:
            dom = resolve_domain(agent_id)
            agent_cache[agent_id] = CourtAgent(
                agent_id,
                domain=dom,
                governor=gov,
                trust_gate=gate,
            )
        return agent_cache[agent_id]

    router = APIRouter(tags=["harness2"])

    @router.get("/agents/{agent_id}/harness")
    async def get_agent_harness(agent_id: str) -> dict[str, Any]:
        """Inspect the active `c1..c8` HarnessTree, token counts, skills, scripts, and hooks for `agent_id`."""
        agent = _get_or_create_agent(agent_id)
        return agent.inspect_harness()

    @router.get("/agents/{agent_id}/experience")
    async def get_agent_experience(agent_id: str) -> dict[str, Any]:
        """Inspect `INDEX.md` and `EXPERIENCE.md` accumulated for `agent_id`'s domain."""
        agent = _get_or_create_agent(agent_id)
        dom = agent.domain
        ddir = cloud_lib.domain_dir(dom)
        index_md = cloud_lib.read_index(dom)
        exp_path = ddir / "EXPERIENCE.md"
        exp_md = (
            exp_path.read_text(encoding="utf-8")
            if exp_path.is_file()
            else (
                f"# Experience — Domain `{dom}`\n\n"
                f"## Task Fingerprint\nSeed Harness² tree active on `{agent.resolve_active_model()}`.\n\n"
                f"## Evolution Trajectory\nReady for online or nightly Harness² recursion.\n\n"
                f"## Reusable Components\nSee `c6_skills` and `c7_scripts` in `/agents/{agent_id}/harness`.\n"
            )
        )
        return {
            "agent_id": agent_id,
            "domain": dom,
            "model_id": agent.resolve_active_model(),
            "index_markdown": index_md,
            "experience_markdown": exp_md,
            "synced_objects": list(cloud_lib.synced_objects),
        }

    @router.get("/incidents/{incident_id}/harness-run")
    async def get_incident_harness_run(incident_id: str) -> dict[str, Any]:
        """Return the Harness² diagnostic/evolution run and Court Swarm consensus verdict for `incident_id`."""
        if incident_id not in incident_runs:
            swarm = CourtSwarmSession(governor=gov, trust_gate=gate)
            verdict = swarm.convene(incident_id)
            incident_runs[incident_id] = {
                "incident_id": incident_id,
                "surface": "godview",
                "model_id": verdict.winning_hypothesis.model_used,
                "consensus_reached": verdict.consensus_reached,
                "consensus_score": verdict.consensus_score,
                "rounds_completed": verdict.rounds_completed,
                "duration_s": verdict.duration_s,
                "escalated_to_argon": verdict.escalated_to_argon,
                "winning_hypothesis": CourtSwarmSession.serialize_verdict(verdict)[
                    "winning_hypothesis"
                ],
                "harness_run_summary": verdict.harness_run_summary,
            }
        return incident_runs[incident_id]

    @router.post("/sovereign/reflect")
    async def trigger_sovereign_reflection(
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a Harness² reflection / evolution cycle for a target incident or domain."""
        body = payload or {}
        task_id = str(body.get("task_id") or body.get("incident_id") or "scenario1_evolve_offset")
        agent_id = str(body.get("agent_id") or "application__specialist")
        mode = str(body.get("mode") or "parallel")
        k = int(body.get("k") or 1)

        agent = _get_or_create_agent(agent_id)
        work_root = lib_dir.parent / "godview_harness2_runs"
        res = agent.evolve_harness(
            task_id,
            work_root=work_root,
            library=cloud_lib,
            k=k,
            mode=mode,
        )
        run_summary = {
            "status": "completed",
            "task_id": res.task_id,
            "agent_id": agent_id,
            "domain": res.domain,
            "model_id": agent.resolve_active_model(),
            "h0_score": res.h0.score,
            "winner_tag": res.winner.tag,
            "winner_score": res.winner.score,
            "winner_passed": res.winner.passed,
            "adopted_path": str(res.adopted_path) if res.adopted_path else None,
            "ledger": res.ledger_summary,
        }
        incident_runs[task_id] = {
            "incident_id": task_id,
            "surface": "godview",
            **run_summary,
        }
        return run_summary

    return router
