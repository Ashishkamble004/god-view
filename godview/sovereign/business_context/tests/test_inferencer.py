"""Unit tests for BusinessContextInferencer (Plan §1.3, §3.2)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from godview_shared.contracts import DiscoveredEdgeDoc, DiscoveredServiceDoc

from godview.sovereign.business_context.inferencer import (
    INFERENCE_SYSTEM_PROMPT,
    BusinessContextInferencer,
    compute_evidence_hash,
)
from shared.tools.lint_neutrality import BANNED_TERMS


@pytest.mark.unit
def test_business_context_prompt_neutrality() -> None:
    """Verify inference system prompt and template contain zero banned domain terms."""
    prompt_lower = INFERENCE_SYSTEM_PROMPT.lower()
    for term in BANNED_TERMS:
        assert term not in prompt_lower, f"Banned term '{term}' found in INFERENCE_SYSTEM_PROMPT"


@pytest.mark.unit
def test_evidence_bundle_strips_secret_values() -> None:
    """Verify evidence bundle extracts env var names only and never values."""
    inferencer = BusinessContextInferencer()
    now = datetime.now(tz=UTC).isoformat()
    svc = DiscoveredServiceDoc(
        env_id="test-env",
        name="svc-processor",
        kind="cloud_run",
        adapter_family="gcp",
        locator="https://svc-processor.run.app",
        labels={
            "env_var_DB_HOST": "postgres.internal.net",
            "env_var_SECRET_KEY": "super_secret_password_12345",
            "env_var_PORT": "8080",
            "tier": "backend",
        },
        routes=[
            {
                "method": "POST",
                "path": "/v1/records",
                "summary": "Process record batch",
            }
        ],
        first_seen=now,
        last_seen=now,
    )
    bundle = inferencer.build_evidence_bundle(svc, edges=[])
    bundle_str = str(bundle)

    # Names must be present, values stripped
    assert "postgres.internal.net" not in bundle_str
    assert "super_secret_password_12345" not in bundle_str
    assert "DB_HOST" in bundle["env_var_names"]
    assert "SECRET_KEY" in bundle["env_var_names"]
    assert "PORT" in bundle["env_var_names"]


@pytest.mark.unit
async def test_evidence_hash_caching() -> None:
    """Verify inference caches results by evidence hash (<= 1 call per node per evidence change)."""
    inferencer = BusinessContextInferencer()
    now = datetime.now(tz=UTC).isoformat()
    svc = DiscoveredServiceDoc(
        env_id="test-env",
        name="svc-records",
        kind="cloud_run",
        adapter_family="gcp",
        locator="https://svc-records.run.app",
        labels={"tier": "backend"},
        routes=[{"method": "GET", "path": "/v1/records", "summary": "List records"}],
        first_seen=now,
        last_seen=now,
    )
    edges: list[DiscoveredEdgeDoc] = []

    # First call
    ctx1 = await inferencer.infer_service_context(svc, edges)
    assert ctx1.domain
    assert ctx1.confidence > 0.0

    # Second call with identical evidence -> returned from cache
    ctx2 = await inferencer.infer_service_context(svc, edges)
    assert ctx1 == ctx2

    # Modified evidence -> cache key changes
    svc_modified = svc.model_copy(
        update={
            "routes": [
                {"method": "GET", "path": "/v1/records", "summary": "List records"},
                {"method": "POST", "path": "/v1/records", "summary": "Create record"},
            ]
        }
    )
    hash_orig = compute_evidence_hash(inferencer.build_evidence_bundle(svc, edges))
    hash_mod = compute_evidence_hash(inferencer.build_evidence_bundle(svc_modified, edges))
    assert hash_orig != hash_mod


@pytest.mark.unit
def test_journey_inference_from_trace_dags() -> None:
    """Verify multi-service journeys are inferred from topology call graphs."""
    inferencer = BusinessContextInferencer()
    now = datetime.now(tz=UTC).isoformat()
    services = [
        DiscoveredServiceDoc(
            env_id="test-env",
            name="gateway",
            kind="api_gateway",
            adapter_family="gcp",
            locator="https://gw.dev",
            routes=[{"method": "POST", "path": "/v1/submit", "operation_id": "submitOrder"}],
            first_seen=now,
            last_seen=now,
        ),
        DiscoveredServiceDoc(
            env_id="test-env",
            name="svc-order",
            kind="cloud_run",
            adapter_family="gcp",
            locator="https://svc-order.run.app",
            routes=[{"method": "POST", "path": "/v1/orders", "operation_id": "createOrder"}],
            first_seen=now,
            last_seen=now,
        ),
        DiscoveredServiceDoc(
            env_id="test-env",
            name="svc-billing",
            kind="cloud_run",
            adapter_family="gcp",
            locator="https://svc-billing.run.app",
            routes=[{"method": "POST", "path": "/v1/charge", "operation_id": "chargeCustomer"}],
            first_seen=now,
            last_seen=now,
        ),
    ]

    edges = [
        DiscoveredEdgeDoc(
            src="test-env__api_gateway.gateway",
            dst="test-env__cloud_run.svc-order",
            protocol="http",
            evidence="trace",
            first_seen=now,
            last_seen=now,
        ),
        DiscoveredEdgeDoc(
            src="test-env__cloud_run.svc-order",
            dst="test-env__cloud_run.svc-billing",
            protocol="http",
            evidence="trace",
            first_seen=now,
            last_seen=now,
        ),
    ]

    journeys = inferencer.infer_journeys_from_topology(services, edges)
    assert len(journeys) >= 1
    j = journeys[0]
    assert j.intended_order_source == "trace_dag"
    assert len(j.steps) >= 2
