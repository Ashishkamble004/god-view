"""Unit tests for BusinessContextInferencer (Plan §1.3, §3.2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from godview_shared.contracts import (
    BusinessContext,
    BusinessContextEvidence,
    BusinessContextInvariant,
    BusinessContextJourney,
    BusinessContextKpi,
    DiscoveredEdgeDoc,
    DiscoveredServiceDoc,
)

from godview.sovereign.business_context.inferencer import (
    INFERENCE_SYSTEM_PROMPT,
    BusinessContextInferencer,
    compute_evidence_hash,
)
from shared.tools.lint_neutrality import BANNED_TERMS


class _FakeLlm:
    def __init__(self, model_name: str = "gemini-1.5-pro", fail: bool = False) -> None:
        self.model_name = model_name
        self.fail = fail
        self.calls = 0

    async def generate(self, role: str, contents: str, response_schema: Any = None) -> Any:
        self.calls += 1
        if self.fail:
            raise RuntimeError("429 ResourceExhausted: rate limit exceeded")
        if response_schema == BusinessContext or (
            isinstance(response_schema, type) and "BusinessContext" in response_schema.__name__
        ):
            return BusinessContext(
                role_summary="Order processor and validator",
                domain="orders",
                capabilities=["listRecords", "createRecord"],
                upstream=[],
                downstream=[],
                journeys=[],
                kpis=[],
                invariants=[],
                confidence=0.88,
                evidence=[
                    BusinessContextEvidence(
                        kind="openapi",
                        ref="openapi:GET /v1/records",
                        excerpt="List records",
                    )
                ],
                inferred_by=self.model_name,
            )
        if "journeys" in contents.lower():
            return [
                {
                    "name": "Order Placement Flow",
                    "steps": [
                        {"node": "gateway", "operation": "submitOrder"},
                        {"node": "svc-order", "operation": "createOrder"},
                        {"node": "svc-billing", "operation": "chargeCustomer"},
                    ],
                }
            ]
        return {}


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
async def test_evidence_hash_caching_and_provenance() -> None:
    """Verify inference caches results by evidence hash and records honest provenance."""
    fake_llm = _FakeLlm()
    inferencer = BusinessContextInferencer(llm=fake_llm)
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
    assert ctx1.domain == "orders"
    assert ctx1.confidence == 0.88
    assert ctx1.inferred_by == "gemini-1.5-pro"
    assert fake_llm.calls == 1

    # Second call with identical evidence -> returned from cache (no extra LLM call)
    ctx2 = await inferencer.infer_service_context(svc, edges)
    assert ctx1 == ctx2
    assert fake_llm.calls == 1

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
async def test_llm_failure_records_zero_confidence_and_no_fabrications() -> None:
    """Verify that when LLM fails or is unavailable, confidence is 0.0 and no KPIs/invariants are invented."""
    failing_llm = _FakeLlm(fail=True)
    inferencer = BusinessContextInferencer(llm=failing_llm)
    now = datetime.now(tz=UTC).isoformat()
    svc = DiscoveredServiceDoc(
        env_id="test-env",
        name="svc-records",
        kind="cloud_run",
        adapter_family="gcp",
        locator="https://svc-records.run.app",
        routes=[{"method": "GET", "path": "/v1/records", "summary": "List records"}],
        first_seen=now,
        last_seen=now,
    )
    res = await inferencer.infer_service_context(svc, edges=[])
    assert res.confidence == 0.0
    assert res.inferred_by == "none"
    assert res.domain == "unknown"
    assert res.kpis == []
    assert res.invariants == []


@pytest.mark.unit
async def test_journey_inference_with_and_without_llm() -> None:
    """Verify journeys are only inferred with real evidence and LLM, never fabricated."""
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
    ]

    edges = [
        DiscoveredEdgeDoc(
            src="test-env__api_gateway.gateway",
            dst="test-env__cloud_run.svc-order",
            protocol="http",
            evidence="trace",
            evidence_kind="trace",
            first_seen=now,
            last_seen=now,
        ),
    ]

    # Without LLM -> empty journeys honestly
    inferencer_no_llm = BusinessContextInferencer(llm=None)
    journeys_no_llm = await inferencer_no_llm.infer_journeys_from_topology(services, edges)
    assert journeys_no_llm == []

    # With LLM -> journeys inferred from traces
    inferencer_llm = BusinessContextInferencer(llm=_FakeLlm())
    journeys_llm = await inferencer_llm.infer_journeys_from_topology(services, edges)
    assert len(journeys_llm) >= 1
    assert journeys_llm[0].name == "Order Placement Flow"


@pytest.mark.unit
async def test_uncited_model_output_marks_status_uncited_and_caps_confidence() -> None:
    """Verify that a model returning 0 refs is marked uncited, has confidence capped <0.5, and is scheduled for retry."""
    class _UncitedLlm:
        async def generate(self, role: str, contents: str, response_schema: Any = None) -> Any:
            return BusinessContext(
                role_summary="Processor with no cited evidence",
                domain="processing",
                capabilities=["processBatch"],
                confidence=0.95,
                evidence=[],  # 0 refs emitted
            )

    inferencer = BusinessContextInferencer(llm=_UncitedLlm())
    now = datetime.now(tz=UTC).isoformat()
    svc = DiscoveredServiceDoc(
        env_id="test-env",
        name="svc-processor",
        kind="cloud_run",
        adapter_family="gcp",
        locator="https://svc-processor.run.app",
        routes=[{"method": "POST", "path": "/v1/batch", "summary": "Process batch"}],
        first_seen=now,
        last_seen=now,
    )
    res = await inferencer.infer_service_context(svc, edges=[])
    assert res.evidence == []
    assert res.confidence <= 0.3
    assert getattr(res, "inference_status", None) == "uncited"


@pytest.mark.unit
async def test_model_ref_absent_from_bundle_is_dropped() -> None:
    """Verify that evidence refs absent from the bundle are dropped, and if all are absent, marks uncited."""
    class _PartiallyValidLlm:
        async def generate(self, role: str, contents: str, response_schema: Any = None) -> Any:
            return BusinessContext(
                role_summary="Processor with one valid and one fake ref",
                domain="processing",
                capabilities=["processBatch"],
                confidence=0.85,
                evidence=[
                    BusinessContextEvidence(
                        kind="openapi",
                        ref="openapi:POST /v1/batch",
                        excerpt="Process batch",
                    ),
                    BusinessContextEvidence(
                        kind="openapi",
                        ref="openapi:DELETE /nonexistent/route",
                        excerpt="Fabricated route citation",
                    ),
                ],
            )

    inferencer = BusinessContextInferencer(llm=_PartiallyValidLlm())
    now = datetime.now(tz=UTC).isoformat()
    svc = DiscoveredServiceDoc(
        env_id="test-env",
        name="svc-processor",
        kind="cloud_run",
        adapter_family="gcp",
        locator="https://svc-processor.run.app",
        routes=[{"method": "POST", "path": "/v1/batch", "summary": "Process batch"}],
        first_seen=now,
        last_seen=now,
    )
    res = await inferencer.infer_service_context(svc, edges=[])
    # The valid ref is kept, the absent one is dropped
    assert len(res.evidence) == 1
    assert res.evidence[0].ref == "openapi:POST /v1/batch"
    assert res.confidence == 0.85
    assert getattr(res, "inference_status", None) == "inferred"

    # Now verify when ALL refs are absent from bundle
    class _AllAbsentLlm:
        async def generate(self, role: str, contents: str, response_schema: Any = None) -> Any:
            return BusinessContext(
                role_summary="Processor with only fabricated refs",
                domain="processing",
                confidence=0.90,
                evidence=[
                    BusinessContextEvidence(
                        kind="openapi",
                        ref="openapi:GET /totally/fake",
                        excerpt="Fake route",
                    ),
                    BusinessContextEvidence(
                        kind="env",
                        ref="env:NONEXISTENT_VAR",
                        excerpt="Fake env",
                    ),
                ],
            )

    inferencer_absent = BusinessContextInferencer(llm=_AllAbsentLlm())
    res_absent = await inferencer_absent.infer_service_context(svc, edges=[])
    assert res_absent.evidence == []
    assert res_absent.confidence <= 0.3
    assert getattr(res_absent, "inference_status", None) == "uncited"


@pytest.mark.unit
async def test_kpis_invariants_journeys_uncited_or_absent_refs_dropped() -> None:
    """Verify KPIs, invariants, and journeys are dropped if their evidence does not resolve to bundle items."""
    class _ClaimsLlm:
        async def generate(self, role: str, contents: str, response_schema: Any = None) -> Any:
            return BusinessContext(
                role_summary="Service with valid and invalid claim citations",
                domain="processing",
                confidence=0.80,
                evidence=[
                    BusinessContextEvidence(
                        kind="openapi",
                        ref="openapi:POST /v1/batch",
                        excerpt="Process batch",
                    )
                ],
                kpis=[
                    BusinessContextKpi(
                        name="batch_throughput",
                        definition="sum(records) / time",
                        unit="records/sec",
                        evidence="openapi:POST /v1/batch",  # in bundle -> kept
                    ),
                    BusinessContextKpi(
                        name="fake_kpi",
                        definition="fake",
                        unit="none",
                        evidence="openapi:GET /fake_endpoint",  # absent -> dropped
                    ),
                    BusinessContextKpi(
                        name="uncited_kpi",
                        definition="uncited",
                        unit="none",
                        evidence="",  # empty -> dropped
                    ),
                ],
                invariants=[
                    BusinessContextInvariant(
                        statement="Payload must be valid",
                        check="len(body) > 0",
                        evidence="openapi:POST /v1/batch",  # in bundle -> kept
                    ),
                    BusinessContextInvariant(
                        statement="Fabricated assertion",
                        check="unknown()",
                        evidence="openapi:PUT /nonexistent",  # absent -> dropped
                    ),
                ],
                journeys=[
                    BusinessContextJourney(
                        name="Batch Intake Journey",
                        steps=[{"node": "svc-processor", "operation": "processBatch"}],
                        confidence=0.9,
                        evidence="openapi:POST /v1/batch",  # in bundle -> kept
                    ),
                    BusinessContextJourney(
                        name="Fabricated Journey",
                        steps=["step1"],
                        confidence=0.2,
                        evidence="invented_flow",  # absent -> dropped
                    ),
                ],
            )

    inferencer = BusinessContextInferencer(llm=_ClaimsLlm())
    now = datetime.now(tz=UTC).isoformat()
    svc = DiscoveredServiceDoc(
        env_id="test-env",
        name="svc-processor",
        kind="cloud_run",
        adapter_family="gcp",
        locator="https://svc-processor.run.app",
        routes=[{"method": "POST", "path": "/v1/batch", "summary": "Process batch"}],
        first_seen=now,
        last_seen=now,
    )
    res = await inferencer.infer_service_context(svc, edges=[])
    # Surviving KPIs: only batch_throughput
    assert len(res.kpis) == 1
    assert res.kpis[0].name == "batch_throughput"
    # Surviving Invariants: only the valid assertion
    assert len(res.invariants) == 1
    assert res.invariants[0].statement == "Payload must be valid"
    # Surviving Journeys: only Batch Intake Journey
    assert len(res.journeys) == 1
    assert res.journeys[0].name == "Batch Intake Journey"
