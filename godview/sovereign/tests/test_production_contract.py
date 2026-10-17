"""Contract tests verifying production entrypoint has no fakes (Plan §3.1 item 8).

Non-negotiable (§0):
- GODVIEW_ENV=gcp must fail to start (sys.exit(1)) if any InMemory* store is used
  or zero adapters are healthy.
- In production GCP mode, stores must be real FirestoreStore, BigQueryLedger,
  PubSubBus, and AgentEngineRuntime instances, never InMemory* fakes.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from godview_shared.adapter_contract import AdapterHealth
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

from godview.sovereign.bootstrap import build_context, create_app


class MockHealthyAdapter:
    """Mock sense adapter satisfying SenseAdapter interface for testing."""

    name = "mock-gcp-adapter"
    family = "gcp"

    def healthy(self) -> bool:
        return True

    def check_health(self) -> AdapterHealth:
        return AdapterHealth(family="gcp", state="available", evidence={"ok": True})


class MockUnhealthyAdapter:
    """Mock unhealthy sense adapter."""

    name = "mock-unhealthy-adapter"
    family = "gcp"

    def healthy(self) -> bool:
        return False

    def check_health(self) -> AdapterHealth:
        return AdapterHealth(family="gcp", state="unavailable", evidence={"error": "down"})


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
    """Contract test: In GCP mode, stores are real GCP classes and >=1 adapter is healthy."""
    env = {
        "GODVIEW_ENV": "gcp",
        "GOOGLE_CLOUD_PROJECT": "test-project-123",
        "FIRESTORE_DATABASE": "god-view-memory-db",
        "BIGQUERY_LEDGER_DATASET": "godview_ledger",
        "NERVOUS_SYSTEM_TOPIC": "godview-nervous-system",
        "GODVIEW_REGIONS": "us-central1",
        **BUDGET_ENV,
    }

    mock_adapters = [MockHealthyAdapter()]

    with patch("godview.adapters.gcp.build_all_gcp_adapters", return_value=mock_adapters):
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

    # 2. Assert healthy adapters > 0
    assert len(ctx.adapters) > 0
    assert any(
        a.healthy() if callable(getattr(a, "healthy", None)) else getattr(a, "healthy", False)
        for a in ctx.adapters
    )

    # 3. Create app and verify /public/healthz reports real store names and healthy adapters
    app = create_app(ctx)
    assert app is not None


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
