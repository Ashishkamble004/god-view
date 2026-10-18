"""Unit tests for God-View v5 Topology Builder, Business Context Inference, and Perception Organ.

Covers plan §1.4, §3.2 requirements:
- TopologyBuilder producing exact §1.4 JSON schema with nodes, edges, journeys, layout_hint
- Tier classification (edge, compute, data, messaging, platform, observer, external)
- Status determination from open incidents and golden signals
- BusinessContextInferencer evidence bundle assembly (zero env var values leaked)
- System prompt verification: zero banned domain words
- Caching by evidence hash (<= 1 call per node per evidence change)
- Inferred journeys from trace DAGs
- PerceptionOrgan zero-placeholder guarantee (no svc-alpha-01, primary_business_flow, primary-datastore, primary-repo)
- Control API routers/topology.py endpoints: /topology, /business-context/{node_id}, /perceive
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from godview_shared.adapter_contract import (
    AdapterHealth,
    DiscoveryResult,
    SenseAdapter,
    SenseCategory,
)
from godview_shared.capabilities import CapabilitySpec, get_capability
from godview_shared.config import GodViewSettings
from godview_shared.contracts import (
    BusinessContext,
    DiscoveredEdgeDoc,
    DiscoveredServiceDoc,
    JourneyDoc,
    Posture,
)
from godview_shared.fakes import InMemoryFirestore, InMemoryPubSub

from godview.sovereign.bootstrap import SovereignContext
from godview.sovereign.business_context.inferencer import (
    INFERENCE_SYSTEM_PROMPT,
    BusinessContextInferencer,
    compute_evidence_hash,
)
from godview.sovereign.control_api.routers.topology import build_router
from godview.sovereign.perception import PerceptionOrgan, resolve_env_id
from godview.sovereign.topology.builder import TopologyBuilder
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


class _FailingSenseAdapter(SenseAdapter):
    name = "failing-adapter"
    family = "gcp"

    async def discover(self) -> DiscoveryResult:
        raise PermissionError("403 Forbidden: Caller lacks compute.instances.list permission")

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.INFRASTRUCTURE]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {}

    async def health(self) -> AdapterHealth:
        return AdapterHealth(
            adapter_name=self.name,
            family=self.family,
            state="unavailable",
            evidence={"error": "403 Forbidden"},
            checked_at=datetime.now(tz=UTC),
        )


class _DummySenseAdapter(SenseAdapter):
    name = "dummy-adapter"
    family = "local"

    def __init__(
        self,
        services: list[DiscoveredServiceDoc] | None = None,
        edges: list[DiscoveredEdgeDoc] | None = None,
    ) -> None:
        self._services = services or []
        self._edges = edges or []

    async def discover(self) -> DiscoveryResult:
        return DiscoveryResult(services=self._services, edges=self._edges, entities=[])

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.CODE, SenseCategory.CHANGE_PLANE]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {"http.probe": get_capability("http.probe")}

    async def health(self) -> AdapterHealth:
        return AdapterHealth(
            adapter_name=self.name,
            family=self.family,
            state="available",
            evidence={},
            checked_at=datetime.now(tz=UTC),
        )


@pytest.mark.unit
def test_topology_builder_schema_and_tiers() -> None:
    """Verify TopologyBuilder produces valid §1.4 JSON format with distinct tiers."""
    builder = TopologyBuilder(env_id="test-env")
    now = datetime.now(tz=UTC).isoformat()

    services = [
        DiscoveredServiceDoc(
            env_id="test-env",
            name="fe-ingress",
            kind="lb_frontend",
            adapter_family="gcp",
            locator="https://203.0.113.1",
            labels={"tier": "edge"},
            first_seen=now,
            last_seen=now,
        ),
        DiscoveredServiceDoc(
            env_id="test-env",
            name="svc-processor",
            kind="cloud_run",
            adapter_family="gcp",
            locator="https://svc-processor.run.app",
            labels={"godview_managed": "true"},
            posture=Posture(ingress="all", iap=True, invoker_iam=True, waf="sensitivity-1"),
            first_seen=now,
            last_seen=now,
        ),
        DiscoveredServiceDoc(
            env_id="test-env",
            name="db-relational",
            kind="cloud_sql",
            adapter_family="sql",
            locator="postgres://db-relational:5432/db",
            labels={},
            first_seen=now,
            last_seen=now,
        ),
        DiscoveredServiceDoc(
            env_id="test-env",
            name="queue-events",
            kind="pubsub_topic",
            adapter_family="gcp",
            locator="projects/p/topics/queue-events",
            labels={},
            first_seen=now,
            last_seen=now,
        ),
        DiscoveredServiceDoc(
            env_id="test-env",
            name="god-view-sovereign",
            kind="cloud_run",
            adapter_family="gcp",
            locator="https://god-view.run.app",
            labels={"godview_role": "observer"},
            first_seen=now,
            last_seen=now,
        ),
    ]

    edges = [
        DiscoveredEdgeDoc(
            src="test-env__lb_frontend.fe-ingress",
            dst="test-env__cloud_run.svc-processor",
            protocol="http",
            evidence="url_map",
            evidence_kind="lb_route",
            confidence=1.0,
            first_seen=now,
            last_seen=now,
        )
    ]

    journeys = [
        JourneyDoc(
            doc_id="j-process",
            name="j-process",
            steps=["test-env__lb_frontend.fe-ingress", "test-env__cloud_run.svc-processor"],
            status="active",
        )
    ]

    incidents = {"test-env__cloud_run.svc-processor": 1}
    golden = {"test-env__cloud_run.svc-processor": {"rps": 50.0, "p95_ms": 12.0, "error_ratio": 0.001}}

    report = builder.build(
        services=services,
        edges=edges,
        journeys=journeys,
        incidents_by_node=incidents,
        golden_by_node=golden,
    )

    assert report.env_id == "test-env"
    assert len(report.nodes) == 5
    assert len(report.edges) == 1
    assert len(report.journeys) == 1

    tier_by_name = {n.name: n.tier for n in report.nodes}
    assert tier_by_name["fe-ingress"] == "edge"
    assert tier_by_name["svc-processor"] == "compute"
    assert tier_by_name["db-relational"] == "data"
    assert tier_by_name["queue-events"] == "messaging"
    assert tier_by_name["god-view-sovereign"] == "observer"

    # Status calculation: svc-processor has 1 open incident -> red
    node_proc = next(n for n in report.nodes if n.name == "svc-processor")
    assert node_proc.status == "red"
    assert node_proc.incidents_open == 1
    assert node_proc.golden.rps == 50.0
    assert node_proc.posture.iap is True

    # Layout hints grouping
    groups = {g.tier: g.node_ids for g in report.layout_hint.groups}
    assert "edge" in groups
    assert "compute" in groups
    assert "data" in groups


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
    assert ctx1.domain
    assert ctx1.confidence > 0.0

    # Second call with identical evidence -> returned from cache
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
async def test_journey_inference_from_trace_dags() -> None:
    """Verify multi-service journeys are inferred from topology call graphs."""
    fake_llm = _FakeLlm()
    inferencer = BusinessContextInferencer(llm=fake_llm)
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
            evidence_kind="trace",
            first_seen=now,
            last_seen=now,
        ),
        DiscoveredEdgeDoc(
            src="test-env__cloud_run.svc-order",
            dst="test-env__cloud_run.svc-billing",
            protocol="http",
            evidence="trace",
            evidence_kind="trace",
            first_seen=now,
            last_seen=now,
        ),
    ]

    journeys = await inferencer.infer_journeys_from_topology(services, edges)
    assert len(journeys) >= 1
    j = journeys[0]
    assert j.intended_order_source == "trace_dag"
    assert len(j.steps) >= 2


@pytest.mark.unit
async def test_perception_organ_zero_placeholders_when_empty() -> None:
    """Verify that when no services exist, PerceptionOrgan returns honest empty results (no svc-alpha-01)."""
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    organ = PerceptionOrgan(firestore=fs, pubsub=ps, adapters=[])

    report = await organ.perceive(reset=True)
    assert report.services_count == 0
    assert report.edges_count == 0
    assert report.entities_count == 0
    assert report.journeys_count == 0

    # Discovered services & journeys collections in memory are empty
    stored_services = fs.list_docs("discovered_services")
    assert len(stored_services) == 0

    stored_journeys = fs.list_docs("journeys")
    assert len(stored_journeys) == 0

    # Ensure forbidden placeholders never appear in report or spawn plan
    banned_placeholders = {
        "svc-alpha-01",
        "primary_business_flow",
        "primary-datastore",
        "primary-repo",
    }
    for item in report.spawn_plan:
        assert item["subject_ref"] not in banned_placeholders
        assert item["agent_id"] not in banned_placeholders


@pytest.mark.unit
def test_topology_router_endpoints() -> None:
    """Test control API router endpoints /topology, /business-context/{node_id}, /perceive."""
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    settings = GodViewSettings.from_env({"GODVIEW_ENV": "hermetic"})
    now = datetime.now(tz=UTC).isoformat()

    svc = DiscoveredServiceDoc(
        env_id="hermetic",
        name="svc-processor",
        kind="cloud_run",
        adapter_family="gcp",
        locator="https://svc-processor.run.app",
        labels={"godview_managed": "true"},
        routes=[{"method": "GET", "path": "/v1/health", "probe_safe": True}],
        first_seen=now,
        last_seen=now,
    )
    edge = DiscoveredEdgeDoc(
        src="hermetic__cloud_run.svc-processor",
        dst="hermetic__cloud_sql.db-data",
        protocol="sql",
        evidence="env_ref",
        evidence_kind="env_ref",
        first_seen=now,
        last_seen=now,
    )
    adapter = _DummySenseAdapter(services=[svc], edges=[edge])

    ctx = SovereignContext(
        settings=settings,
        docs=fs,
        ledger=None,  # type: ignore[arg-type]
        bus=ps,
        agents=None,  # type: ignore[arg-type]
        adapters=[adapter],
        auth=None,  # type: ignore[arg-type]
        llm=None,  # type: ignore[arg-type]
        clock=datetime.now,
    )

    app = FastAPI()
    router = build_router(ctx)
    app.include_router(router, prefix="/api/v1")
    client = TestClient(app)

    # 1. POST /api/v1/perceive
    r_perc = client.post("/api/v1/perceive", json={"reset": True})
    assert r_perc.status_code == 200
    data_perc = r_perc.json()
    assert data_perc["services_count"] >= 1
    assert "discovered_services" in data_perc

    # 2. GET /api/v1/topology
    r_top = client.get("/api/v1/topology")
    assert r_top.status_code == 200
    data_top = r_top.json()
    assert len(data_top["nodes"]) >= 1
    assert len(data_top["edges"]) >= 1
    assert "layout_hint" in data_top

    # 3. GET /api/v1/business-context/{node_id}
    r_bc = client.get("/api/v1/business-context/svc-processor")
    assert r_bc.status_code == 200
    data_bc = r_bc.json()
    assert data_bc["service_name"] == "svc-processor"
    assert data_bc["kind"] == "cloud_run"
    assert "business_context" in data_bc

    # 4. Unknown node returns 404
    r_404 = client.get("/api/v1/business-context/unknown-node-xyz")
    assert r_404.status_code == 404


@pytest.mark.unit
async def test_adapter_partial_failure_isolation() -> None:
    """Verify failing adapter does not crash perception and marks adapter unavailable with evidence."""
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    now = datetime.now(tz=UTC).isoformat()
    ok_svc = DiscoveredServiceDoc(
        env_id="test-env",
        name="ok-svc",
        kind="cloud_run",
        adapter_family="gcp",
        locator="https://ok-svc.run.app",
        first_seen=now,
        last_seen=now,
    )
    ok_adapter = _DummySenseAdapter(services=[ok_svc])
    failing_adapter = _FailingSenseAdapter()

    organ = PerceptionOrgan(firestore=fs, pubsub=ps, adapters=[ok_adapter, failing_adapter])
    report = await organ.perceive(reset=True)

    # Perception must not crash and healthy adapter services must still be discovered
    assert report.services_count == 1
    health_by_name = {h.adapter_name: h.state for h in report.adapter_health}
    assert health_by_name["dummy-adapter"] == "available"
    assert health_by_name["failing-adapter"] == "unavailable"


@pytest.mark.unit
def test_resolve_env_id_canonical() -> None:
    """Verify canonical env_id resolution per plan §3.2."""
    # Hermetic
    s_hermetic = GodViewSettings.from_env({"GODVIEW_ENV": "hermetic"})
    assert resolve_env_id(s_hermetic) == "hermetic"

    # GCP mode with project
    gcp_env = {
        "GODVIEW_ENV": "gcp",
        "GOOGLE_CLOUD_PROJECT": "my-sample-proj",
        "GODVIEW_COURT_DAILY_CEILING_USD": "100.0",
        **{f"GODVIEW_BUDGET_{k.upper()}_USD": "10.0" for k in ("application", "business_line", "change", "data", "platform", "sovereign")},
    }
    s_gcp = GodViewSettings.from_env(gcp_env)
    assert resolve_env_id(s_gcp) == "gcp-my-sample-proj"
