# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Comprehensive unit and integration tests for ADK Court, A2A, and Routers."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
from collections.abc import AsyncGenerator
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
from google.adk.models import BaseLlm, LlmRequest
from google.adk.models import LlmResponse as AdkLlmResponse
from google.genai import types

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
        self.executors = {"obs.query_logs_scoped": lambda **kwargs: {"status": "ok", "logs": []}}
        self.court_manager = CourtManager(
            firestore=self.docs,
            pubsub=self.bus,
            agent_engine=self.agents,
        )


def test_specialist_agent_generic_archetypes_and_tools() -> None:
    llm = AsyncFakeLlm()
    ctx = MockSovereignContext(llm=llm)
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

        # Zero Echo: unhandled capability raises NoExecutorError
        from godview.sovereign.remediation.types import NoExecutorError

        with pytest.raises(NoExecutorError):
            tools.invoke_capability("unregistered.read_capability")

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


class FunctionCallingFakeLlm(BaseLlm):
    model: str = "fake-tool-llm"
    calls: int = 0

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[AdkLlmResponse]:
        self.calls += 1
        if self.calls == 1:
            part = types.Part.from_function_call(name="get_incident", args={"incident_id": "inc-42"})
            yield AdkLlmResponse(content=types.Content(parts=[part], role="model"))
        else:
            part = types.Part.from_text(
                text=json.dumps({
                    "hypothesis": "Function calling verified incident inc-42 DB lock contention",
                    "confidence": 0.94,
                    "root_cause_category": "database_lock",
                    "citations": ["incident:inc-42"],
                })
            )
            yield AdkLlmResponse(content=types.Content(parts=[part], role="model"))


@pytest.mark.asyncio
async def test_specialist_adk_native_function_tool_calling() -> None:
    fake_llm = FunctionCallingFakeLlm()
    llm = AsyncFakeLlm()
    ctx = MockSovereignContext(llm=llm)
    ctx.docs.set("incidents", "inc-42", {"incident_id": "inc-42", "summary": "DB lock timeout"})

    agent = create_specialist_agent(
        archetype="data",
        subject_ref="postgres-primary",
        ctx=ctx,
        llm_runner=fake_llm,
    )

    diag = await agent.diagnose("inc-42")
    assert isinstance(diag, Diagnosis)
    assert fake_llm.calls == 2
    assert "Function calling verified" in diag.hypothesis
    assert diag.confidence == 0.94
    assert diag.root_cause_category == "database_lock"


@pytest.mark.asyncio
async def test_zero_invented_confidence_on_parse_error() -> None:
    class MalformedFakeLlm(BaseLlm):
        model: str = "malformed-llm"

        async def generate_content_async(
            self, llm_request: LlmRequest, stream: bool = False
        ) -> AsyncGenerator[AdkLlmResponse]:
            part = types.Part.from_text(text="NOT_VALID_JSON_CANNOT_PARSE")
            yield AdkLlmResponse(content=types.Content(parts=[part], role="model"))

    malformed_llm = MalformedFakeLlm()
    llm = AsyncFakeLlm()
    ctx = MockSovereignContext(llm=llm)
    agent = create_specialist_agent(
        archetype="application",
        subject_ref="payments-svc",
        ctx=ctx,
        llm_runner=malformed_llm,
    )

    diag = await agent.diagnose("inc-corrupt")
    assert isinstance(diag, Diagnosis)
    assert diag.confidence == 0.0
    assert diag.hypothesis == "not diagnosed: parse error"
    assert diag.root_cause_category == "parse_error"


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
async def test_court_critic_multi_round_debate_challenges_revision() -> None:
    class DebateFakeCriticLlm(BaseLlm):
        model: str = "debate-critic-llm"
        round_calls: int = 0

        async def generate_content_async(
            self, llm_request: LlmRequest, stream: bool = False
        ) -> AsyncGenerator[AdkLlmResponse]:
            self.round_calls += 1
            if self.round_calls == 1:
                part = types.Part.from_text(
                    text=json.dumps({
                        "consensus_score": 0.65,
                        "hypothesis": "Initial hypothesis challenged",
                        "confidence": 0.65,
                        "root_cause_category": "pending_revision",
                        "challenges": ["Provide network trace evidence for latency surge"],
                    })
                )
            else:
                part = types.Part.from_text(
                    text=json.dumps({
                        "consensus_score": 0.93,
                        "hypothesis": "Swarm debate consensus reached: TLS cert expiration",
                        "confidence": 0.93,
                        "root_cause_category": "certificate_expired",
                        "citations": ["evidence:tls_handshake_abort"],
                        "proposed_actions": [
                            {"capability_id": "cert.renew_certificate", "confidence": 0.93}
                        ],
                    })
                )
            yield AdkLlmResponse(content=types.Content(parts=[part], role="model"))

    critic_llm = DebateFakeCriticLlm()
    spec_llm = AsyncFakeLlm()
    ctx = MockSovereignContext(llm=spec_llm)
    orchestrator = CourtOrchestrator(ctx=ctx)
    orchestrator.critic.llm_runner = critic_llm
    orchestrator.critic.adk_agent.model = critic_llm

    diag = await orchestrator.convene(
        incident_id="inc-debate-test",
        evidence_pack={"trace_data": "sample"},
        specialists=["application", "platform"],
    )
    assert isinstance(diag, Diagnosis)
    assert critic_llm.round_calls == 2
    assert diag.confidence >= 0.85
    assert "Swarm debate consensus reached" in diag.hypothesis


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
    llm = AsyncFakeLlm()
    ctx = MockSovereignContext(llm=llm)
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


@pytest.mark.asyncio
async def test_court_orchestrator_convene_multiple_incidents_without_reparenting_error() -> None:
    llm = AsyncFakeLlm()
    ctx = MockSovereignContext(llm=llm)
    orchestrator = CourtOrchestrator(ctx=ctx)

    # First convene call
    diag1 = await orchestrator.convene(
        incident_id="inc-first-run",
        evidence_pack={"metric": "cpu_high"},
        specialists=["application", "platform"],
    )
    assert isinstance(diag1, Diagnosis)
    assert diag1.confidence >= 0.85

    # Second convene call on the SAME orchestrator singleton (must not raise ValidationError: already has a parent)
    diag2 = await orchestrator.convene(
        incident_id="inc-second-run",
        evidence_pack={"metric": "memory_leak"},
        specialists=["application", "platform"],
    )
    assert isinstance(diag2, Diagnosis)
    assert diag2.confidence >= 0.85


@pytest.mark.asyncio
async def test_specialist_ctxllm_forwards_system_instruction_and_tool_declarations() -> None:
    recorded_calls: list[dict[str, Any]] = []

    class InspectingLlm:
        async def generate(self, **kwargs: Any) -> Any:
            recorded_calls.append(kwargs)
            return {
                "hypothesis": "Inspected system instruction and tools",
                "confidence": 0.90,
                "root_cause_category": "inspected",
                "citations": ["evidence:inspected"],
                "proposed_actions": [],
            }

    llm = InspectingLlm()
    ctx = MockSovereignContext(llm=llm)
    specialist = create_specialist_agent("application", "checkout-svc", ctx=ctx)
    specialist.instruction = "SYSTEM_INSTRUCTION_UNIQUE_TOKEN_123"
    specialist.adk_agent.instruction = "SYSTEM_INSTRUCTION_UNIQUE_TOKEN_123"

    diag = await specialist.diagnose("inc-inspect", {"key": "val"})
    assert isinstance(diag, Diagnosis)
    assert len(recorded_calls) >= 1

    last_call = recorded_calls[-1]
    # Verify system_instruction was passed to ctx.llm.generate
    assert "system_instruction" in last_call
    assert last_call["system_instruction"] is not None
    assert "SYSTEM_INSTRUCTION_UNIQUE_TOKEN_123" in str(last_call["system_instruction"])

    # Verify tools (GenAI Tool/FunctionDeclaration) were passed
    assert "tools" in last_call
    assert last_call["tools"] is not None
    tools = last_call["tools"]
    assert len(tools) >= 1
    tool_names: list[str] = []
    for t in tools:
        if hasattr(t, "function_declarations"):
            tool_names.extend([fd.name for fd in t.function_declarations])
        elif hasattr(t, "name"):
            tool_names.append(t.name)
    assert "get_incident" in tool_names or "invoke_capability" in tool_names


@pytest.mark.asyncio
async def test_remote_specialist_parses_reasoning_engine_query_output_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    llm = AsyncFakeLlm()
    ctx = MockSovereignContext(llm=llm)
    orchestrator = CourtOrchestrator(ctx=ctx)
    card_url = "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/p/locations/l/reasoningEngines/re-123:query"
    orchestrator.register_remote_agent("application__orders-svc", card_url)

    # Recorded Vertex AI Agent Engine :query response payload
    query_response_payload = {
        "output": {
            "agent_id": "application__orders-svc",
            "agent_type": "application",
            "status": "ok",
            "diagnosis": {
                "hypothesis": "High memory leak in worker pool",
                "confidence": 0.92,
                "citations": ["metric:container/memory/used"],
                "proposed_actions": [
                    {"capability_id": "run.restore_revision_config", "confidence": 0.92, "rationale": "rollback"}
                ],
                "root_cause_category": "memory_leak",
            },
        }
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = query_response_payload

    class MockAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> MockAsyncClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def post(self, *args: Any, **kwargs: Any) -> Any:
            return mock_resp

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    class EchoCriticLlm(BaseLlm):
        model: str = "echo-critic"

        async def generate_content_async(
            self, llm_request: LlmRequest, stream: bool = False
        ) -> AsyncGenerator[AdkLlmResponse]:
            text = ""
            for c in llm_request.contents:
                if hasattr(c, "parts"):
                    for p in c.parts:
                        if hasattr(p, "text") and p.text:
                            text += p.text
            match = re.search(r'"hypothesis":\s*"([^"]+)"', text)
            hyp = match.group(1) if match else "Synthesized hypothesis"
            part = types.Part.from_text(
                text=json.dumps({
                    "consensus_score": 0.95,
                    "hypothesis": f"Critic verified: {hyp}",
                    "confidence": 0.92,
                    "root_cause_category": "memory_leak",
                    "citations": ["evidence:trace"],
                    "proposed_actions": [],
                })
            )
            yield AdkLlmResponse(content=types.Content(parts=[part], role="model"))

    critic_llm = EchoCriticLlm()
    orchestrator.critic.llm_runner = critic_llm
    orchestrator.critic.adk_agent.model = critic_llm

    diag = await orchestrator.convene(
        incident_id="inc-remote-query-shape",
        evidence_pack={"trace": "data"},
        specialists=["application"],
    )
    assert isinstance(diag, Diagnosis)
    assert diag.confidence == 0.92
    assert "High memory leak in worker pool" in diag.hypothesis
    assert diag.root_cause_category == "memory_leak"


@pytest.mark.asyncio
async def test_specialist_tools_remote_dispatch_with_id_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    import httpx

    captured_sync_requests: list[tuple[str, str, dict[str, Any]]] = []
    captured_async_requests: list[tuple[str, str, dict[str, Any]]] = []

    class MockHttpClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> MockHttpClient:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def get(self, url: str, headers: dict[str, str]) -> Any:
            captured_sync_requests.append(("GET", url, headers))
            resp = MagicMock()
            resp.status_code = 200
            if "/incidents/" in url:
                resp.json.return_value = {"incident_id": "inc-456", "title": "DB Latency Spike"}
            elif "/business-context/" in url:
                resp.json.return_value = {"service_id": "order-api", "domain": "checkout"}
            return resp

        def post(self, url: str, json: dict[str, Any], headers: dict[str, str]) -> Any:
            captured_sync_requests.append(("POST", url, headers))
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"status": "ok", "result": {"rows": 42}}
            return resp

    class MockAsyncHttpClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> MockAsyncHttpClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def get(self, url: str, headers: dict[str, str]) -> Any:
            captured_async_requests.append(("GET", url, headers))
            resp = MagicMock()
            resp.status_code = 200
            if "/incidents/" in url:
                resp.json.return_value = {"incident_id": "inc-456", "title": "DB Latency Spike"}
            elif "/business-context/" in url:
                resp.json.return_value = {"service_id": "order-api", "domain": "checkout"}
            return resp

        async def post(self, url: str, json: dict[str, Any], headers: dict[str, str]) -> Any:
            captured_async_requests.append(("POST", url, headers))
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"status": "ok", "result": {"rows": 42}}
            return resp

    monkeypatch.setattr(httpx, "Client", MockHttpClient)
    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncHttpClient)

    class FakeAuth:
        def id_token_for(self, audience: str) -> str:
            return f"mock-oidc-token-for-{audience}"

    ctx = SimpleNamespace(auth=FakeAuth(), docs=None, executors=None, ledger=None)
    tools = SpecialistTools(ctx=ctx, sovereign_url="https://godview-sovereign.a.run.app")

    # 1. Synchronous dispatch with exact /api/v1 routes
    inc = tools.get_incident("inc-456")
    assert inc["incident_id"] == "inc-456"
    method, url, headers = captured_sync_requests[-1]
    assert method == "GET"
    assert url == "https://godview-sovereign.a.run.app/api/v1/incidents/inc-456"
    assert headers["Authorization"] == "Bearer mock-oidc-token-for-https://godview-sovereign.a.run.app"

    bctx = tools.get_business_context("order-api")
    assert bctx["domain"] == "checkout"
    method, url, headers = captured_sync_requests[-1]
    assert method == "GET"
    assert url == "https://godview-sovereign.a.run.app/api/v1/business-context/order-api"
    assert headers["Authorization"] == "Bearer mock-oidc-token-for-https://godview-sovereign.a.run.app"

    res = tools.invoke_capability("sql.list_tables", {"database": "prod"})
    assert res["status"] == "ok"
    method, url, headers = captured_sync_requests[-1]
    assert method == "POST"
    assert url == "https://godview-sovereign.a.run.app/api/v1/capabilities/sql.list_tables:invoke"
    assert headers["Authorization"] == "Bearer mock-oidc-token-for-https://godview-sovereign.a.run.app"

    # 2. Asynchronous dispatch with AsyncClient and exact /api/v1 routes
    inc_async = await tools.get_incident_async("inc-456")
    assert inc_async["incident_id"] == "inc-456"
    method, url, headers = captured_async_requests[-1]
    assert method == "GET"
    assert url == "https://godview-sovereign.a.run.app/api/v1/incidents/inc-456"
    assert headers["Authorization"] == "Bearer mock-oidc-token-for-https://godview-sovereign.a.run.app"

    bctx_async = await tools.get_business_context_async("order-api")
    assert bctx_async["domain"] == "checkout"
    method, url, headers = captured_async_requests[-1]
    assert method == "GET"
    assert url == "https://godview-sovereign.a.run.app/api/v1/business-context/order-api"
    assert headers["Authorization"] == "Bearer mock-oidc-token-for-https://godview-sovereign.a.run.app"

    res_async = await tools.invoke_capability_async("sql.list_tables", {"database": "prod"})
    assert res_async["status"] == "ok"
    method, url, headers = captured_async_requests[-1]
    assert method == "POST"
    assert url == "https://godview-sovereign.a.run.app/api/v1/capabilities/sql.list_tables:invoke"
    assert headers["Authorization"] == "Bearer mock-oidc-token-for-https://godview-sovereign.a.run.app"

    # 3. ADK FunctionTool wrappers invoking async methods
    adk_tools = {t.name: t for t in tools.build_adk_tools()}
    assert "get_incident" in adk_tools
    assert "get_business_context" in adk_tools
    assert "invoke_capability" in adk_tools

    t_inc = adk_tools["get_incident"]
    await t_inc.func(incident_id="inc-789")
    method, url, headers = captured_async_requests[-1]
    assert method == "GET"
    assert url == "https://godview-sovereign.a.run.app/api/v1/incidents/inc-789"

    t_bctx = adk_tools["get_business_context"]
    await t_bctx.func(service_id="payment-svc")
    method, url, headers = captured_async_requests[-1]
    assert method == "GET"
    assert url == "https://godview-sovereign.a.run.app/api/v1/business-context/payment-svc"

    t_inv = adk_tools["invoke_capability"]
    await t_inv.func(capability_id="obs.query_logs_scoped", args={"service": "payment-svc"})
    method, url, headers = captured_async_requests[-1]
    assert method == "POST"
    assert url == "https://godview-sovereign.a.run.app/api/v1/capabilities/obs.query_logs_scoped:invoke"

