# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Comprehensive unit and integration tests for ADK Court, A2A, and Routers."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from godview_shared.config import (
    DEFAULT_AGENT_FALLBACK_MODEL_ID,
    DEFAULT_AGENT_MODEL_ID,
    DEFAULT_AGENT_MODEL_LOCATION,
)
from godview_shared.contracts import (
    Diagnosis,
)
from godview_shared.fakes import (
    InMemoryAgentEngine,
    InMemoryBigQuery,
    InMemoryFirestore,
    InMemoryPubSub,
)

from godview.agents.adk.compat import (
    Event,
    InvocationContext,
    RemoteA2aAgent,
)
from godview.agents.adk.court import (
    CONSENSUS_THRESHOLD,
    CourtOrchestrator,
    register_in_process_court,
)
from godview.agents.adk.specialist import (
    ARCHETYPE_GENERIC_INSTRUCTIONS,
    SpecialistTools,
    create_specialist_agent,
)
from godview.agents.archetypes import COURT_DOMAINS
from godview.agents.server import AgentExecutor
from godview.harness.cloud_library import InMemorySkillsStorageClient
from godview.harness.rsi import evolve_specialist
from godview.sovereign.court_manager import CourtManager

REPO_ROOT = Path(__file__).resolve().parents[3]

def _load_router(name: str) -> Any:
    p = REPO_ROOT / "godview" / "sovereign" / "control_api" / "routers" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_router_{name}", p)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

build_court_router = _load_router("court").build_router
build_harness_router = _load_router("harness").build_router


class FakeLlmRunner:
    """Deterministic in-memory LLM runner for ADK testing without API calls."""

    def __init__(self, canned_response: str = "Root cause analyzed successfully.") -> None:
        self.canned_response = canned_response
        self.invocations: list[dict[str, Any]] = []

    async def generate(self, model: str, contents: Any, **kwargs: Any) -> str:
        self.invocations.append({"model": model, "contents": contents, "kwargs": kwargs})
        return self.canned_response


class MockSovereignContext:
    """Mock SovereignContext matching plan §1.2."""

    def __init__(self) -> None:
        self.docs = InMemoryFirestore()
        self.ledger = InMemoryBigQuery()
        self.bus = InMemoryPubSub()
        self.agents = InMemoryAgentEngine()
        self.adapters = []
        self.hooks = MagicMock()
        self.hooks.on_incident = []
        self.court_manager = CourtManager(
            firestore=self.docs,
            pubsub=self.bus,
            agent_engine=self.agents,
        )


def test_specialist_agent_generic_archetypes_and_tools() -> None:
    ctx = MockSovereignContext()
    runner = FakeLlmRunner()

    for domain in COURT_DOMAINS:
        agent = create_specialist_agent(
            archetype=domain,
            subject_ref=f"test-{domain}",
            ctx=ctx,
            llm_runner=runner,
        )
        assert agent.archetype == domain
        assert agent.model == DEFAULT_AGENT_MODEL_ID
        assert agent.fallback_model == DEFAULT_AGENT_FALLBACK_MODEL_ID
        assert agent.location == DEFAULT_AGENT_MODEL_LOCATION
        assert agent.name == f"{domain}__test-{domain}"
        assert domain in ARCHETYPE_GENERIC_INSTRUCTIONS

        # Verify specialist tools
        tools = agent.specialist_tools
        assert isinstance(tools, SpecialistTools)

        # 1. get_incident
        inc = tools.get_incident("inc-test-01")
        assert inc["incident_id"] == "inc-test-01"

        # 2. get_business_context
        biz = tools.get_business_context()
        assert isinstance(biz, dict)

        # 3. query_ledger
        rows = tools.query_ledger("SELECT 1")
        assert isinstance(rows, list)

        # 4. invoke_capability (read free, mutating requires approval)
        read_res = tools.invoke_capability("obs.query_logs_scoped", {"filter": "error"})
        assert read_res["status"] == "ok"

        mut_res = tools.invoke_capability("k8s.rollout_restart", {"deployment": "core-api"})
        assert mut_res["status"] == "approval_required"
        assert mut_res["requires_approval"] is True

        # 5. propose_remediation
        prop = tools.propose_remediation(
            capability_id="run.restore_revision_config",
            args={"revision": "v1"},
            rationale="Restore stable revision after error rate surge",
            confidence=0.92,
        )
        assert prop["status"] == "proposed"
        assert prop["action"]["capability_id"] == "run.restore_revision_config"


@pytest.mark.asyncio
async def test_court_orchestrator_parallel_fanout_and_critic_consensus() -> None:
    ctx = MockSovereignContext()
    runner = FakeLlmRunner()
    orchestrator = CourtOrchestrator(ctx=ctx, llm_runner=runner)

    evidence = {
        "scenario": "payload_schema_offset_shift",
        "offset": 120,
        "expected_score_offset": 108,
        "actual_score_offset": 228,
        "service": "orders-gateway",
    }

    diagnosis = await orchestrator.convene(
        incident_id="inc-test-convene",
        evidence_pack=evidence,
        specialists=["application", "data"],
    )

    assert isinstance(diagnosis, Diagnosis)
    assert diagnosis.confidence >= CONSENSUS_THRESHOLD
    assert diagnosis.hypothesis
    assert diagnosis.root_cause_category == "payload_schema_offset_shift"
    assert len(diagnosis.proposed_actions) >= 1
    assert any(
        a.capability_id in ("git.create_pull_request", "run.restore_revision_config")
        for a in diagnosis.proposed_actions
    )


@pytest.mark.asyncio
async def test_in_process_fallback_hook_registration() -> None:
    ctx = MockSovereignContext()
    orchestrator = register_in_process_court(ctx)
    assert isinstance(orchestrator, CourtOrchestrator)
    assert len(ctx.hooks.on_incident) >= 1

    callback = ctx.hooks.on_incident[0]
    fake_incident = {
        "incident_id": "inc-hook-001",
        "evidence_pack": {
            "scenario": "container_oom_exit_137",
            "container_exit_code": 137,
            "dlq_backlog_depth": 10,
        },
    }
    diag = await callback(fake_incident)
    assert isinstance(diag, Diagnosis)
    assert diag.confidence >= 0.85


@pytest.mark.asyncio
async def test_a2a_remote_agent_and_executor_round_trip() -> None:
    executor = AgentExecutor()
    card = executor.build_agent_card("application")
    assert card.name
    assert card.domain == "application"
    assert card.default_model_id == DEFAULT_AGENT_MODEL_ID

    # Test RemoteA2aAgent interacting with AgentExecutor
    remote_agent = RemoteA2aAgent(
        name="application__specialist",
        agent_card=card.model_dump(),
        client=executor,
    )
    inv_ctx = InvocationContext(session_id="test-a2a-sess")
    event = await remote_agent.invoke(
        {"incident_id": "inc-a2a-roundtrip", "evidence": {"offset": 120}},
        inv_ctx,
    )
    assert isinstance(event, Event)
    assert isinstance(event.content, dict)
    assert event.content.get("status") == "completed"
    assert event.content.get("model_used") == DEFAULT_AGENT_MODEL_ID


def test_routers_court_and_harness_endpoints() -> None:
    ctx = MockSovereignContext()
    app = FastAPI()
    court_router = build_court_router(ctx)
    harness_router = build_harness_router(ctx)
    app.include_router(court_router)
    app.include_router(harness_router)

    client = TestClient(app)

    # 1. GET /court
    r_list = client.get("/court")
    assert r_list.status_code == 200
    assert "agents" in r_list.json()

    # Pre-spawn an agent in court_manager
    asyncio.run(ctx.court_manager.spawn_agent("application", "orders-api"))

    # 2. GET /court/{id}
    r_get = client.get("/court/application__orders-api")
    assert r_get.status_code == 200
    assert r_get.json()["agent_type"] == "application"

    # 3. POST /court/{id}:wake
    r_wake = client.post("/court/application__orders-api:wake")
    assert r_wake.status_code == 200
    assert r_wake.json()["state"] == "awake"

    # 4. POST /court/{id}:warm and :cool
    r_warm = client.post("/court/application__orders-api:warm")
    assert r_warm.status_code == 200
    r_cool = client.post("/court/application__orders-api:cool")
    assert r_cool.status_code == 200

    # 5. POST /court/convene
    r_convene = client.post(
        "/court/convene",
        json={
            "incident_id": "inc-router-convene",
            "evidence_pack": {"container_exit_code": 137},
        },
    )
    assert r_convene.status_code == 200
    assert r_convene.json()["confidence"] >= 0.85

    # 6. GET /harness/{id}/tree
    r_tree = client.get("/harness/application__orders-api/tree")
    assert r_tree.status_code == 200
    tree_data = r_tree.json()
    assert tree_data["surface"] == "godview"
    assert tree_data["validation"]["ok"] is True
    assert tree_data["always_on_tokens"] <= 12_000

    # 7. POST /harness/{id}:evolve
    r_evolve = client.post(
        "/harness/application__orders-api:evolve",
        json={"trigger": "manual", "task_id": "incident_evolve_schema"},
    )
    assert r_evolve.status_code == 200
    assert r_evolve.json()["status"] == "completed"
    assert r_evolve.json()["winner_passed"] is True


def test_rsi_evolution_and_gcs_persistence(tmp_path: Path) -> None:
    ctx = MockSovereignContext()
    fake_gcs = InMemorySkillsStorageClient()

    # Pre-spawn court agent doc in firestore
    ctx.docs.set(
        "court_agents",
        "application__orders-api",
        {
            "agent_id": "application__orders-api",
            "living_model_version": 1,
            "state": "active",
        },
    )

    res = evolve_specialist(
        agent_id="application__orders-api",
        trigger="incident_closed:resolved",
        ctx=ctx,
        task_id="incident_evolve_schema",
        work_dir=tmp_path / "rsi_work",
        mode="parallel",
        k=1,
        storage_client=fake_gcs,
    )

    assert res.passed is True
    assert res.score >= 0.85
    assert res.living_model_version == 2
    assert res.adopted_path is not None
    assert (Path(res.adopted_path) / "EXPERIENCE.md").is_file()

    # Verify GCS blobs were uploaded
    assert len(fake_gcs.list_uris()) >= 1

    # Verify firestore record was updated with living_model_version=2
    updated_doc = ctx.docs.get("court_agents", "application__orders-api")
    assert updated_doc["living_model_version"] == 2
    assert updated_doc["last_evolution_trigger"] == "incident_closed:resolved"

    # Verify skills and harness_trees collections in firestore
    assert len(ctx.docs.list_docs("skills")) >= 1
    assert len(ctx.docs.list_docs("harness_trees")) >= 1
