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
    ProposedAction,
)
from godview_shared.fakes import (
    InMemoryAgentEngine,
    InMemoryBigQuery,
    InMemoryFirestore,
    InMemoryPubSub,
)
from google.adk.agents import LlmAgent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent

from godview.agents.adk.court import (
    CONSENSUS_THRESHOLD,
    CourtOrchestrator,
    install_court_and_rsi,
)
from godview.agents.adk.specialist import (
    ARCHETYPE_GENERIC_INSTRUCTIONS,
    SpecialistTools,
    create_specialist_agent,
)
from godview.agents.archetypes import COURT_DOMAINS
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


class AsyncFakeLlm:
    """Async fake LLM matching shared/godview_shared/llm.py `async def generate(...)`."""

    def __init__(self, canned_diag: Diagnosis | None = None) -> None:
        self.awaited = 0
        self.invocations: list[dict[str, Any]] = []
        self._canned = canned_diag or Diagnosis(
            hypothesis="LLM-PRODUCED: TLS certificate expired on observed edge service",
            confidence=0.92,
            root_cause_category="certificate_expired",
            citations=["evidence:cert_status_failed"],
            proposed_actions=[
                ProposedAction(
                    capability_id="cert.renew_certificate",
                    args={"domain": "edge.domain.test"},
                    rationale="Renew expired TLS cert to restore traffic",
                    confidence=0.92,
                )
            ],
        )

    async def generate(
        self,
        role: str = "reasoning",
        contents: Any = "",
        tools: list[Any] | None = None,
        response_schema: Any | None = None,
        agent_id: str = "sovereign",
        **kwargs: Any,
    ) -> Diagnosis:
        self.awaited += 1
        self.invocations.append({
            "role": role,
            "contents": contents,
            "tools": tools,
            "response_schema": response_schema,
            "agent_id": agent_id,
            "kwargs": kwargs,
        })
        return self._canned


class MockSovereignContext:
    """Mock SovereignContext matching plan §1.2."""

    def __init__(self, llm: Any | None = None) -> None:
        self.docs = InMemoryFirestore()
        self.ledger = InMemoryBigQuery()
        self.bus = InMemoryPubSub()
        self.agents = InMemoryAgentEngine()
        self.adapters = []
        self.hooks = MagicMock()
        self.hooks.on_incident = []
        self.hooks.on_incident_closed = []
        self.hooks.on_approval_decided = []
        self.llm = llm
        self.court_orchestrator = None
        self.court_manager = CourtManager(
            firestore=self.docs,
            pubsub=self.bus,
            agent_engine=self.agents,
        )


def test_specialist_agent_generic_archetypes_and_tools() -> None:
    ctx = MockSovereignContext()
    runner = AsyncFakeLlm()

    for domain in COURT_DOMAINS:
        agent = create_specialist_agent(
            archetype=domain,
            subject_ref=f"test-{domain}",
            ctx=ctx,
            llm_runner=runner,
        )
        assert agent.archetype == domain
        assert agent.primary_model_id == DEFAULT_AGENT_MODEL_ID
        assert agent.fallback_model_id == DEFAULT_AGENT_FALLBACK_MODEL_ID
        assert agent.model_location == DEFAULT_AGENT_MODEL_LOCATION
        assert agent.name == f"{domain}__test-{domain}"
        assert domain in ARCHETYPE_GENERIC_INSTRUCTIONS

        # Real Google ADK LlmAgent object
        assert isinstance(agent.adk_agent, LlmAgent)

        # Verify specialist tools
        tools = agent.tools_helper
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
async def test_specialist_agent_async_llm_diagnose() -> None:
    llm = AsyncFakeLlm()
    ctx = MockSovereignContext(llm=llm)

    agent = create_specialist_agent(
        archetype="platform",
        subject_ref="edge-gateway",
        ctx=ctx,
    )

    unseen_evidence = {
        "summary": "TLS handshake failures after managed certificate expired",
        "telemetry": {"5xx_rate": 0.35, "ssl_handshake_errors": 420},
    }

    diag = await agent.diagnose("inc-unseen-tls", unseen_evidence)
    assert isinstance(diag, Diagnosis)
    assert llm.awaited >= 1
    # Confirms hypothesis originated from LLM and not keywords
    assert "LLM-PRODUCED" in diag.hypothesis
    assert diag.confidence == 0.92
    assert diag.root_cause_category == "certificate_expired"
    assert len(diag.proposed_actions) >= 1
    assert diag.proposed_actions[0].capability_id == "cert.renew_certificate"


@pytest.mark.asyncio
async def test_specialist_agent_model_unavailable_fallback() -> None:
    # No LLM provided in ctx
    ctx = MockSovereignContext(llm=None)
    agent = create_specialist_agent(
        archetype="application",
        subject_ref="orders-svc",
        ctx=ctx,
    )

    diag = await agent.diagnose("inc-nomodel", {"error": "any"})
    assert isinstance(diag, Diagnosis)
    assert diag.confidence == 0.0
    assert diag.hypothesis == "not diagnosed: model unavailable"
    assert diag.proposed_actions == []


@pytest.mark.asyncio
async def test_court_orchestrator_parallel_fanout_and_critic_consensus() -> None:
    llm = AsyncFakeLlm()
    ctx = MockSovereignContext(llm=llm)
    orchestrator = CourtOrchestrator(ctx=ctx)

    unseen_evidence = {
        "summary": "Sudden latency spike and handshake aborts on edge ingress",
        "network": {"reset_count": 89},
    }

    diagnosis = await orchestrator.convene(
        incident_id="inc-test-convene",
        evidence_pack=unseen_evidence,
        specialists=["application", "platform"],
    )

    assert isinstance(diagnosis, Diagnosis)
    # Critic and specialists both awaited
    assert llm.awaited >= 2
    assert diagnosis.confidence >= CONSENSUS_THRESHOLD
    assert "LLM-PRODUCED" in diagnosis.hypothesis
    assert diagnosis.root_cause_category == "certificate_expired"
    assert len(diagnosis.proposed_actions) >= 1
    assert diagnosis.proposed_actions[0].capability_id == "cert.renew_certificate"


@pytest.mark.asyncio
async def test_court_orchestrator_model_unavailable() -> None:
    ctx = MockSovereignContext(llm=None)
    orchestrator = CourtOrchestrator(ctx=ctx)

    diagnosis = await orchestrator.convene(
        incident_id="inc-nomodel",
        evidence_pack={"some": "telemetry"},
    )
    assert isinstance(diagnosis, Diagnosis)
    assert diagnosis.confidence == 0.0
    assert diagnosis.hypothesis == "not diagnosed: model unavailable"


@pytest.mark.asyncio
async def test_install_court_and_rsi_wiring() -> None:
    llm = AsyncFakeLlm()
    ctx = MockSovereignContext(llm=llm)

    install_court_and_rsi(ctx)
    assert ctx.court_orchestrator is not None
    assert isinstance(ctx.court_orchestrator, CourtOrchestrator)

    # Verify incident hook dispatcher
    assert len(ctx.hooks.on_incident) >= 1
    hook_fn = ctx.hooks.on_incident[0]
    diag = await hook_fn({"incident_id": "inc-hook-test", "evidence_pack": {"metric": "val"}})
    assert isinstance(diag, Diagnosis)
    assert llm.awaited >= 1


@pytest.mark.asyncio
async def test_remote_a2a_agent_registration() -> None:
    ctx = MockSovereignContext()
    orchestrator = CourtOrchestrator(ctx=ctx)

    card_url = "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/test/locations/us-central1/reasoningEngines/app-1:query"
    remote = orchestrator.register_remote_agent("application__orders-svc", card_url)
    assert isinstance(remote, RemoteA2aAgent)
    assert "application__orders-svc" in orchestrator._remote_specialists


def test_routers_court_and_harness_endpoints() -> None:
    llm = AsyncFakeLlm()
    ctx = MockSovereignContext(llm=llm)
    install_court_and_rsi(ctx)

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
            "evidence_pack": {"tls_issue": True},
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
    assert tree_data["living_model_version"] >= 1

    # 7. POST /harness/{id}:evolve
    r_evolve = client.post(
        "/harness/application__orders-api:evolve",
        json={"trigger": "manual", "task_id": "incident_evolve_schema"},
    )
    assert r_evolve.status_code == 200
    assert r_evolve.json()["status"] == "completed"


def test_rsi_evolution_and_gcs_persistence(tmp_path: Path) -> None:
    llm = AsyncFakeLlm()
    ctx = MockSovereignContext(llm=llm)
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
        evidence_pack={
            "summary": "archived case replay",
            "category": "certificate_expired",
            "require_adaptation_on_h0": True,
        },
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
