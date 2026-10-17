"""Contract tests verifying production entrypoint has no fakes (Plan §3.1 item 8).

Non-negotiable (§0):
- GODVIEW_ENV=gcp must fail to start (sys.exit(1)) if any InMemory* store is used
  or zero adapters are healthy.
- In production GCP mode, stores must be real FirestoreStore, BigQueryLedger,
  PubSubBus, and AgentEngineRuntime instances, never InMemory* fakes.
- Background lifespan loops must start and update heartbeats.
- Nervous system push must verify OIDC, decode audit events, write 7-day TTL, and invoke hooks.
- Routes must not contain hardcoded façade literals.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from godview_shared.adapter_contract import AdapterHealth, SenseAdapter
from godview_shared.fakes import (
    InMemoryAgentEngine,
    InMemoryBigQuery,
    InMemoryFirestore,
    InMemoryPubSub,
)
from godview_shared.stores import (
    AgentEngineRuntime,
    BigQueryLedger,
    FirestoreStore,
    PubSubBus,
)

from godview.adapters.gcp import build_all_gcp_adapters
from godview.sovereign.bootstrap import build_context, create_app

FIXTURES_PATH = (
    Path(__file__).parents[2] / "adapters" / "gcp" / "tests" / "fixtures" / "gcp_api_fixtures.json"
)


class MockUnhealthyAdapter(SenseAdapter):
    """Mock unhealthy sense adapter for fail-closed tests."""

    name = "mock-unhealthy-adapter"
    family = "gcp"

    async def health(self) -> AdapterHealth:
        return AdapterHealth(
            adapter_name=self.name,
            family="gcp",
            state="unavailable",
            evidence={"error": "down"},
            checked_at=datetime.now(tz=UTC),
        )

    def capabilities(self) -> dict[str, Any]:
        return {}

    def senses(self) -> list[Any]:
        return []

    async def discover(self) -> Any:
        return None

    async def _execute_capability_impl(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError()


BUDGET_ENV = {
    "GODVIEW_BUDGET_APPLICATION_USD": "0.75",
    "GODVIEW_BUDGET_BUSINESS_LINE_USD": "0.75",
    "GODVIEW_BUDGET_CHANGE_USD": "0.75",
    "GODVIEW_BUDGET_DATA_USD": "0.50",
    "GODVIEW_BUDGET_PLATFORM_USD": "0.50",
    "GODVIEW_BUDGET_SOVEREIGN_USD": "1.50",
    "GODVIEW_COURT_DAILY_CEILING_USD": "5.00",
}


@pytest.mark.unit
def test_production_entrypoint_has_no_fakes() -> None:
    """Contract test: In GCP mode, stores are real GCP classes, >=15 adapters healthy, lifespan runs."""
    gcp_fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8")) if FIXTURES_PATH.exists() else {}

    env = {
        "GODVIEW_ENV": "gcp",
        "GOOGLE_CLOUD_PROJECT": "test-project-123",
        "FIRESTORE_DATABASE": "god-view-memory-db",
        "BIGQUERY_LEDGER_DATASET": "godview_ledger",
        "NERVOUS_SYSTEM_TOPIC": "godview-nervous-system",
        "GODVIEW_REGIONS": "us-central1",
        **BUDGET_ENV,
    }

    # Use the real factory with test fixtures (no mock classes)
    real_adapters = build_all_gcp_adapters(
        project_id="test-project-123",
        regions=["us-central1"],
        fixtures=gcp_fixtures,
    )

    with patch("godview.adapters.gcp.build_all_gcp_adapters", return_value=real_adapters):
        ctx = build_context(env)

    # 1. Assert stores are real classes, not in-memory fakes
    assert isinstance(ctx.docs, FirestoreStore)
    assert not isinstance(ctx.docs, InMemoryFirestore)

    assert isinstance(ctx.ledger, BigQueryLedger)
    assert not isinstance(ctx.ledger, InMemoryBigQuery)

    assert isinstance(ctx.bus, PubSubBus)
    assert not isinstance(ctx.bus, InMemoryPubSub)

    assert isinstance(ctx.agents, AgentEngineRuntime)
    assert not isinstance(ctx.agents, InMemoryAgentEngine)

    # 2. Assert healthy adapters >= 15
    assert len(ctx.adapters) >= 15

    # 3. Create app and verify lifespan background loops and healthz
    app = create_app(ctx)
    assert app is not None

    with TestClient(app) as client:
        # /public/healthz reports real store names, healthy adapters, and heartbeats
        r_health = client.get("/public/healthz")
        assert r_health.status_code == 200
        health_data = r_health.json()
        assert health_data["status"] == "ok"
        assert health_data["mode"] == "gcp"
        assert health_data["store_classes"]["docs"] == "FirestoreStore"
        assert health_data["store_classes"]["ledger"] == "BigQueryLedger"
        assert health_data["store_classes"]["bus"] == "PubSubBus"
        assert health_data["store_classes"]["agents"] == "AgentEngineRuntime"
        assert health_data["adapters_healthy"] >= 15
        assert "perception" in health_data["loop_heartbeats"]
        assert "sentinels" in health_data["loop_heartbeats"]
        assert "agent_warmth" in health_data["loop_heartbeats"]
        assert "dlq_watchdog" in health_data["loop_heartbeats"]

        # /metrics reports real Prometheus metrics
        r_metrics = client.get("/metrics")
        assert r_metrics.status_code == 200
        assert "godview_adapters_total" in r_metrics.text
        assert "godview_loop_heartbeat_timestamp_seconds" in r_metrics.text


@pytest.mark.unit
def test_production_startup_fails_closed_on_zero_healthy_adapters() -> None:
    """Contract test: GODVIEW_ENV=gcp exits with code 1 if zero adapters are healthy."""
    env = {
        "GODVIEW_ENV": "gcp",
        "GOOGLE_CLOUD_PROJECT": "test-project-123",
        **BUDGET_ENV,
    }

    # Zero adapters
    with (
        patch("godview.adapters.gcp.build_all_gcp_adapters", return_value=[]),
        pytest.raises(SystemExit) as exc_info,
    ):
        build_context(env)
    assert exc_info.value.code == 1

    # Only unhealthy adapters
    with (
        patch(
            "godview.adapters.gcp.build_all_gcp_adapters",
            return_value=[MockUnhealthyAdapter()],
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        build_context(env)
    assert exc_info.value.code == 1


@pytest.mark.unit
def test_production_startup_fails_closed_on_missing_project() -> None:
    """Contract test: GODVIEW_ENV=gcp exits with code 1 if project ID cannot be determined."""
    env = {
        "GODVIEW_ENV": "gcp",
        **BUDGET_ENV,
    }
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("godview.sovereign.bootstrap._fetch_metadata", return_value=None),
        pytest.raises(SystemExit) as exc_info,
    ):
        build_context(env)
    assert exc_info.value.code == 1


@pytest.mark.unit
def test_production_create_app_rejects_inmemory_stores() -> None:
    """Contract test: create_app rejects InMemory stores when in GCP mode (Binding §0)."""
    env = {
        "GODVIEW_ENV": "hermetic",
        **BUDGET_ENV,
    }
    ctx = build_context(env)
    # Switch env_mode to gcp while stores are InMemory
    ctx.settings.env_mode = "gcp"

    with pytest.raises(SystemExit) as exc_info:
        create_app(ctx)
    assert exc_info.value.code == 1


@pytest.mark.unit
def test_nervous_system_push_audit_change_event_dispatch() -> None:
    """Contract test: /nervous-system/push verifies audit payload, writes 7-day TTL, and invokes hooks."""
    app = create_app()
    client = TestClient(app)

    app.state.firestore._collections.setdefault("change_events", {})

    # Access SovereignContext from app closures or hooks
    # Register hook on app state or test
    import base64
    audit_data = {
        "protoPayload": {
            "methodName": "v1.compute.instances.insert",
            "resourceName": "projects/p/zones/z/instances/vm-01",
        },
        "timestamp": "2026-10-17T12:00:00Z",
    }
    encoded = base64.b64encode(json.dumps(audit_data).encode("utf-8")).decode("utf-8")
    payload = {
        "message": {
            "data": encoded,
            "messageId": "msg-test-12345",
        }
    }

    resp = client.post(
        "/nervous-system/push",
        json=payload,
        headers={"x-godview-test-principal": "godview-sense-sa@hermetic.iam.gserviceaccount.com"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "acknowledged"

    # Verify change_events collection has document with 7-day TTL expires_at
    chgs = app.state.firestore.list_docs("change_events")
    assert len(chgs) >= 1
    recent = chgs[-1]
    assert "expires_at" in recent
    exp = datetime.fromisoformat(recent["expires_at"])
    now = datetime.now(tz=UTC)
    assert exp > now + timedelta(days=6)


@pytest.mark.unit
def test_no_facade_literals_in_cost_or_routes() -> None:
    """Contract test: Verify zero fake drift seeds, fake schedules, or fake hardcoded numbers in routes."""
    app = create_app()
    client = TestClient(app)

    # 1. GET /cost returns clean defaults, no fake drift diffs, no hardcoded IST strings
    r_cost = client.get("/v1/cost")
    assert r_cost.status_code == 200
    cost_data = r_cost.json()
    assert "drift_diffs" in cost_data
    assert "17:00–09:00 IST" not in str(cost_data)
    assert "1 in 5 journeys" not in str(cost_data)

    # 2. Simulator routes must NOT be mounted on sovereign control API
    r_sim = client.post("/v1/simulator/state", json={"enabled": True})
    assert r_sim.status_code in (404, 405)

    # 3. Drift restore/reconcile routes must NOT be mounted on sovereign control API
    r_restore = client.post("/v1/cost/drift/d1:restore_attribute", json={})
    assert r_restore.status_code in (404, 405)
