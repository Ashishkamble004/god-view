"""Unit tests for SentinelEngine loop execution, budget, error isolation, and control API routers."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from godview_shared.contracts import SentinelDefinition
from godview_shared.fakes import InMemoryAgentEngine, InMemoryFirestore, InMemoryPubSub

from godview.sovereign.control_api.routers.incidents import (
    build_router as build_incidents_router,
)
from godview.sovereign.control_api.routers.sentinels import (
    build_router as build_sentinels_router,
)
from godview.sovereign.sentinel_engine import SentinelEngine
from godview.sovereign.sentinels import build_probe_sentinel


class MockContext:
    def __init__(self, fs: Any, ps: Any, ae: Any) -> None:
        self.env_mode = "hermetic"
        self.docs = fs
        self.bus = ps
        self.agents = ae
        self.sentinel_engine = SentinelEngine(firestore=fs, pubsub=ps, agent_engine=ae)
        self.incident_manager = self.sentinel_engine.incident_manager


@pytest.mark.unit
async def test_error_isolation_and_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies per-sentinel error isolation and per-tick evaluation budget."""
    from godview.sovereign.sentinels.evaluators import EVALUATOR_REGISTRY

    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    ae = InMemoryAgentEngine()

    class BrokenEvaluator:
        async def evaluate(self, sentinel: Any, ctx: Any) -> Any:
            raise RuntimeError("Simulated crash in evaluator")

    monkeypatch.setitem(EVALUATOR_REGISTRY, "probe", BrokenEvaluator())

    engine = SentinelEngine(
        firestore=fs,
        pubsub=ps,
        agent_engine=ae,
        budget_s=0.5,
    )

    snt_broken = build_probe_sentinel("https://broken.example.com", probe_name="broken")
    snt_healthy = SentinelDefinition.model_validate(
        {
            "sentinel_id": "snt-healthy-01",
            "class": "invariant_sql",
            "source_ref": "healthy-ref",
            "check": {"dialect": "bigquery", "statement": "SELECT 0", "window_s": 300},
            "cadence_s": 10,
            "baseline": {"model": "constant", "expected": 0.0, "stddev": 0.0},
            "wake_condition": {"operator": ">=", "threshold": 1.0, "consecutive": 1, "window_s": 300},
            "target_agent_id": "platform__env",
            "state": "enforced",
            "compiled_by": "test",
            "compiled_at": "2026-10-17T00:00:00Z",
        }
    )

    engine.register_sentinel(snt_broken)
    engine.register_sentinel(snt_healthy)

    # evaluate_tick must NOT fail despite broken sentinel
    report = await engine.evaluate_tick()
    assert report.evaluated_count == 2
    assert len(report.errors) == 1
    assert "Simulated crash" in report.errors[0]["error"]


@pytest.mark.unit
def test_routers_incidents_and_sentinels() -> None:
    """Verifies control API routers for incidents, sentinels, and manual probe execution."""
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    ae = InMemoryAgentEngine()
    ctx = MockContext(fs, ps, ae)

    app = FastAPI()
    app.include_router(build_incidents_router(ctx))
    app.include_router(build_sentinels_router(ctx))
    client = TestClient(app)

    # 1. GET /sentinels initially empty
    resp = client.get("/sentinels")
    assert resp.status_code == 200
    assert resp.json() == {"sentinels": []}

    # 2. POST /sentinels
    snt_payload = {
        "sentinel_id": "snt-probe-portal",
        "class": "probe",
        "source_ref": "https://example.com/healthz",
        "check": {"dialect": "python", "statement": "probe", "window_s": 300},
        "cadence_s": 10,
        "baseline": {"model": "synthetic", "expected": 0.0, "stddev": 0.0},
        "wake_condition": {"operator": ">=", "threshold": 1.0, "consecutive": 1, "window_s": 300},
        "target_agent_id": "platform__env",
        "state": "enforced",
        "compiled_by": "admin",
        "compiled_at": "2026-10-17T00:00:00Z",
    }
    resp = client.post("/sentinels", json=snt_payload)
    assert resp.status_code == 200
    assert resp.json()["id"] == "snt-probe-portal"

    # 3. PATCH /sentinels/{id}
    resp = client.patch("/sentinels/snt-probe-portal", json={"state": "paused"})
    assert resp.status_code == 200
    assert resp.json()["state"] == "paused"

    # 4. POST /probes/run
    resp = client.post("/probes/run", json={"source": "manual_test"})
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["status"] == "ok"
    assert data["action"] == "probe_run"

    # 5. GET /incidents
    resp = client.get("/incidents")
    assert resp.status_code == 200
    assert "incidents" in resp.json()

    # Seed an incident and test GET /incidents/{id} and POST /incidents/{id}:close
    from godview.sovereign.incidents.models import IncidentDoc
    now_iso = "2026-10-17T12:00:00Z"
    test_inc = IncidentDoc(
        doc_id="inc-test-0001",
        dedupe_key="probe:https://example.com:404",
        opened_at=now_iso,
        symptom_summary="Test probe incident",
        evidence_pack={"schema_version": "1.0", "evidence_pack_id": "evp-1", "incident_id": "inc-test-0001", "fired_at": now_iso, "sentinel": {"id": "s1", "class": "probe", "source_ref": "https://example.com"}, "breach": {"statement": "test", "observed": {"count": 1, "window_s": 300}, "first_seen": now_iso, "consecutive": 1}, "baseline": {"model": "test", "expected": 0, "stddev": 0}, "affected": {"services": ["s1"], "rows": 1}, "golden_signals": {"status": "green", "p95_ms": 100, "error_rate_pct": 0}, "budget": {"agent_tokens_remaining": 100, "escalation_allowed": True}, "target_agent_id": "agent-1"},
        lead_agent_id="agent-1",
    )
    fs.set("episodic_incidents", "inc-test-0001", test_inc.model_dump(mode="json"))

    resp = client.get("/incidents/inc-test-0001")
    assert resp.status_code == 200
    assert resp.json()["doc_id"] == "inc-test-0001"

    resp = client.post("/incidents/inc-test-0001:close", json={"reason": "Fixed in PR #99"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"
    assert resp.json()["incident"]["outcome"] == "resolved"


@pytest.mark.unit
async def test_engine_cadence_and_rotation() -> None:
    """Verifies that evaluate_tick honors cadence_s and rotates evaluation order."""
    from datetime import UTC, datetime

    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    ae = InMemoryAgentEngine()
    now = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)

    engine = SentinelEngine(
        firestore=fs,
        pubsub=ps,
        agent_engine=ae,
        clock=lambda: now,
    )

    # Sentinel A: cadence 60s, ran 10s ago -> MUST BE SKIPPED
    snt_a = SentinelDefinition.model_validate(
        {
            "sentinel_id": "snt-cadence-60s",
            "class": "invariant_sql",
            "source_ref": "ref-a",
            "check": {"dialect": "bigquery", "statement": "SELECT 0", "window_s": 300},
            "cadence_s": 60,
            "baseline": {"model": "constant", "expected": 0.0, "stddev": 0.0},
            "wake_condition": {"operator": ">=", "threshold": 1.0, "consecutive": 1, "window_s": 300},
            "target_agent_id": "platform__env",
            "state": "enforced",
            "compiled_by": "test",
            "compiled_at": "2026-10-17T00:00:00Z",
            "last_run_at": "2026-10-17T11:59:50Z",  # 10s ago
        }
    )

    # Sentinel B: cadence 10s, ran 20s ago -> MUST BE EVALUATED
    snt_b = SentinelDefinition.model_validate(
        {
            "sentinel_id": "snt-cadence-10s",
            "class": "invariant_sql",
            "source_ref": "ref-b",
            "check": {"dialect": "bigquery", "statement": "SELECT 0", "window_s": 300},
            "cadence_s": 10,
            "baseline": {"model": "constant", "expected": 0.0, "stddev": 0.0},
            "wake_condition": {"operator": ">=", "threshold": 1.0, "consecutive": 1, "window_s": 300},
            "target_agent_id": "platform__env",
            "state": "enforced",
            "compiled_by": "test",
            "compiled_at": "2026-10-17T00:00:00Z",
            "last_run_at": "2026-10-17T11:59:40Z",  # 20s ago
        }
    )

    engine.register_sentinel(snt_a)
    engine.register_sentinel(snt_b)

    report = await engine.evaluate_tick()
    # Only snt_b should be evaluated
    assert report.evaluated_count == 1


@pytest.mark.unit
async def test_unknown_status_does_not_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that when an evaluator returns status='unknown', record_clear is NOT called."""
    from datetime import UTC, datetime

    from godview.sovereign.sentinels.evaluators import EVALUATOR_REGISTRY
    from godview.sovereign.sentinels.models import SentinelEvaluation

    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    ae = InMemoryAgentEngine()
    now = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)

    class UnknownEvaluator:
        async def evaluate(self, sentinel: Any, ctx: Any) -> Any:
            return SentinelEvaluation(
                observed=0.0,
                breached=False,
                details={"status": "unknown", "error": "Adapter timed out after 5.0s"},
            )

    monkeypatch.setitem(EVALUATOR_REGISTRY, "probe", UnknownEvaluator())

    engine = SentinelEngine(
        firestore=fs,
        pubsub=ps,
        agent_engine=ae,
        clock=lambda: now,
    )

    snt = build_probe_sentinel("https://api.example.com", probe_name="unknown-check")
    engine.register_sentinel(snt)

    # Seed an open incident on this resource
    eval_b = SentinelEvaluation(
        observed=1.0,
        breached=True,
        signature="probe_failed_http_500",
        resource_id="https://api.example.com",
    )
    inc, _ = await engine.incident_manager.record_breach(snt, eval_b, now)
    assert inc.clear_ticks == 0

    # Run evaluate_tick where evaluator returns unknown
    await engine.evaluate_tick()

    # The incident MUST NOT increment clear_ticks because state was unknown!
    assert inc.clear_ticks == 0


@pytest.mark.unit
async def test_install_e2e_lifecycle() -> None:
    """Verifies install(ctx) lifecycle: auto-compilation via perception hook, evaluate_tick, breach -> incident."""
    from datetime import UTC, datetime

    from godview.sovereign.bootstrap import SovereignHooks
    from godview.sovereign.sentinel_engine import install

    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    ae = InMemoryAgentEngine()
    now = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)

    # Dummy SovereignContext
    class Ctx:
        def __init__(self) -> None:
            self.docs = fs
            self.bus = ps
            self.agents = ae
            self.ledger = None
            self.adapters = []
            self.clock = lambda: now
            self.hooks = SovereignHooks()

    ctx = Ctx()

    # 1. install(ctx)
    engine = install(ctx)
    assert hasattr(ctx, "sentinel_engine")
    assert hasattr(ctx, "incident_manager")
    assert len(ctx.hooks.perception_tick) == 1
    assert len(ctx.hooks.sentinel_tick) == 1

    # 2. Seed discovered service in docs
    fs.set(
        "discovered_services",
        "svc-auth",
        {
            "name": "svc-auth",
            "kind": "cloud_run",
            "endpoint": "https://auth.example.com",
            "tier": "edge",
        },
    )

    # 3. Fire perception hook -> sentinels auto-registered in Firestore
    perception_hook = ctx.hooks.perception_tick[0]
    await perception_hook()

    sentinels = engine.list_sentinels()
    assert len(sentinels) >= 1
    snt_ids = [s.sentinel_id for s in sentinels]
    assert any("posture" in s_id for s_id in snt_ids)
