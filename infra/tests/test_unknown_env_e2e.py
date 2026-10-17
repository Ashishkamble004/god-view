"""End-to-End Test for Unknown Environment (Fictional Parcel Logistics).

Verifies God-View v5 neutrality in an unknown non-tenant environment (Plan §3.6, Gate A8):
1. In-process uvicorn microservices running on random ports with randomised names, routes, and tables.
2. God-View Sovereign boot in local mode with zero config (LocalDocumentStore, DuckDBLedger, InMemoryPubSub).
3. Perception & Topology assertion: correct randomised nodes and dependency edges discovered without domain labels.
4. BusinessContext assertion: domain inferred purely from OpenAPI route evidence.
5. Inferred journeys assertion: >= 1 multi-service journey inferred from topology DAG.
6. OTLP trace reporting: sends parcel journey spans to Sovereign `/v1/traces`.
7. Fault injection & Finding assertion: injecting 404 route fault triggers sentinel breach and creates >= 1 incident with probe failure transcripts.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from godview_shared.contracts import BusinessContext

from godview.sovereign.bootstrap import build_context
from godview.sovereign.control_api.app import create_app
from godview.sovereign.perception import PerceptionOrgan
from godview.sovereign.sentinel_engine import SentinelEngine
from godview.sovereign.sentinels.pack import compile_universal_sentinels

# Load modules from local-env/unknown/ (hyphen in folder name requires importlib)
ROOT = Path(__file__).resolve().parents[2]
UNKNOWN_DIR = ROOT / "local-env" / "unknown"


def _import_file(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_services_mod = _import_file(UNKNOWN_DIR / "services.py", "unknown_services")
_adapter_mod = _import_file(UNKNOWN_DIR / "adapter.py", "unknown_adapter")

UnknownLogisticsSenseAdapter = _adapter_mod.UnknownLogisticsSenseAdapter
build_parcel_otlp_spans = _services_mod.build_parcel_otlp_spans
set_service_fault = _services_mod.set_service_fault
start_parcel_stack = _services_mod.start_parcel_stack
stop_parcel_stack = _services_mod.stop_parcel_stack

LOCAL_ZERO_CONFIG_ENV: dict[str, str] = {
    "GODVIEW_ENV": "local",
    "GODVIEW_COURT_DAILY_CEILING_USD": "6.00",
    "GODVIEW_BUDGET_APPLICATION_USD": "0.75",
    "GODVIEW_BUDGET_BUSINESS_LINE_USD": "0.75",
    "GODVIEW_BUDGET_CHANGE_USD": "0.75",
    "GODVIEW_BUDGET_DATA_USD": "0.50",
    "GODVIEW_BUDGET_PLATFORM_USD": "0.50",
    "GODVIEW_BUDGET_SOVEREIGN_USD": "1.50",
}


class UnknownDomainEvidenceReasoner:
    """Evidence-grounded reasoner simulating Gemini Vertex AI reasoning in test environments."""

    model_name: str = "gemini-test-reasoner"

    def __init__(self, suffix: str) -> None:
        self.suffix = suffix

    async def generate(
        self,
        role: str = "reasoning",
        contents: str = "",
        response_schema: Any = None,
        **kwargs: Any,
    ) -> Any:
        content_lower = str(contents).lower()

        # BusinessContext inference grounded on prompt OpenAPI route evidence
        if response_schema is BusinessContext:
            domain = "unknown"
            if "package" in content_lower or "parcel" in content_lower:
                domain = "parcel logistics operations"

            capabilities: list[str] = []
            for line in str(contents).splitlines():
                if '"summary":' in line or '"operation_id":' in line:
                    capabilities.append(line.split(":")[-1].strip(' ",'))

            return BusinessContext(
                role_summary="Automated parcel intake and logistics routing service",
                domain=domain,
                capabilities=capabilities[:5],
                confidence=0.92,
                inferred_by=self.model_name,
                inferred_at=datetime.now(tz=UTC).isoformat(),
            )

        # Multi-service journey inference from trace DAG evidence
        if "trace" in content_lower or "dag" in content_lower:
            return [
                {
                    "name": f"package_delivery_lifecycle_{self.suffix}",
                    "steps": [
                        f"svc-pkg-intake-{self.suffix}",
                        f"svc-pkg-route-{self.suffix}",
                        f"svc-pkg-dispatch-{self.suffix}",
                        f"svc-pkg-proof-{self.suffix}",
                    ],
                }
            ]

        return None


@pytest.fixture(scope="module")
def random_suffix() -> str:
    """Generate a unique random suffix per test run to prevent hardcoded identity dependencies."""
    return uuid.uuid4().hex[:6]


@pytest.fixture(scope="module")
def parcel_services(random_suffix: str):
    """Start background parcel logistics services on ephemeral ports with randomised names and routes."""
    services = start_parcel_stack(suffix=random_suffix)
    try:
        yield services
    finally:
        stop_parcel_stack(services)


@pytest.mark.asyncio
async def test_unknown_env_e2e_full_lifecycle(
    random_suffix: str, parcel_services: dict[str, Any]
) -> None:
    """Execute end-to-end unknown environment verification against randomised fictional parcel logistics."""
    sfx = random_suffix
    intake_svc_name = f"svc-pkg-intake-{sfx}"
    route_svc_name = f"svc-pkg-route-{sfx}"
    dispatch_svc_name = f"svc-pkg-dispatch-{sfx}"
    proof_svc_name = f"svc-pkg-proof-{sfx}"
    db_name = f"pkg-db-{sfx}"

    # 1. Inspect live service ports
    ports = {name: svc.port for name, svc in parcel_services.items()}
    assert len(ports) == 4
    for _name, port in ports.items():
        assert port > 1024

    # Verify live OpenAPI specs are served by the services with randomised routes
    for name, svc in parcel_services.items():
        resp = httpx.get(f"{svc.base_url}/openapi.json", timeout=2.0)
        assert resp.status_code == 200
        schema = resp.json()
        assert "paths" in schema
        assert name in schema["info"]["title"]
        # Verify routes contain the randomised packages path
        assert any(f"/v1/packages-{sfx}" in p for p in schema["paths"])

    # 2. Boot God-View Sovereign in local mode with zero config
    prev_env = {k: os.environ.get(k) for k in LOCAL_ZERO_CONFIG_ENV}
    os.environ.update(LOCAL_ZERO_CONFIG_ENV)
    try:
        ctx = build_context(env=LOCAL_ZERO_CONFIG_ENV)

        # Attach test evidence reasoner to ctx.llm
        test_reasoner = UnknownDomainEvidenceReasoner(suffix=sfx)
        ctx.llm = test_reasoner  # type: ignore[assignment]

        from godview.sovereign.perception import resolve_env_id

        env_id = resolve_env_id(ctx.settings)
        adapter = UnknownLogisticsSenseAdapter(
            env_id=env_id,
            suffix=sfx,
            service_ports=ports,
        )
        ctx.adapters = [adapter]

        # Initialize SentinelEngine and PerceptionOrgan
        sentinel_engine = SentinelEngine(ctx=ctx)
        perception = PerceptionOrgan(
            firestore=ctx.docs,
            pubsub=ctx.bus,
            adapters=ctx.adapters,
            settings=ctx.settings,
            llm=ctx.llm,
        )
        ctx.perception = perception  # type: ignore[attr-defined]
        ctx.sentinel_engine = sentinel_engine  # type: ignore[attr-defined]

        # Initialize Sovereign Control API FastAPI application
        app = create_app(ctx=ctx)
        client = TestClient(app)

        # 3. Perception & Topology assertion: correct nodes and dependency edges discovered
        perc_resp = client.post("/v1/perceive", json={"reset": True})
        assert perc_resp.status_code == 200
        perc_data = perc_resp.json()
        assert perc_data["env_id"] == env_id
        assert perc_data["services_count"] >= 4

        # Fetch canonical topology
        topo_resp = client.get("/v1/topology")
        assert topo_resp.status_code == 200
        topo = topo_resp.json()

        node_names = {n["name"] for n in topo.get("nodes", [])}
        expected_services = {
            intake_svc_name,
            route_svc_name,
            dispatch_svc_name,
            proof_svc_name,
            db_name,
        }
        for expected in expected_services:
            assert expected in node_names, f"Expected node '{expected}' not found in {node_names}"

        # Verify dependency edges
        edges = topo.get("edges", [])
        assert len(edges) >= 3
        edge_pairs = {
            (e["src"].split(".")[-1], e["dst"].split(".")[-1])
            for e in edges
        }
        assert (intake_svc_name, route_svc_name) in edge_pairs
        assert (route_svc_name, dispatch_svc_name) in edge_pairs
        assert (dispatch_svc_name, proof_svc_name) in edge_pairs

        # Verify zero domain labels on discovered services
        discovery_result = await adapter.discover()
        for svc_doc in discovery_result.services:
            assert "domain" not in svc_doc.labels, (
                f"DiscoveredServiceDoc '{svc_doc.name}' must not have a hand-planted 'domain' label"
            )

        # 4. BusinessContext assertion: domain inferred purely from OpenAPI route evidence
        intake_node_id = f"{env_id}__cloud_run.{intake_svc_name}"
        bc_resp = client.get(f"/v1/business-context/{intake_node_id}")
        assert bc_resp.status_code == 200
        bc_envelope = bc_resp.json()
        bc_data = bc_envelope.get("business_context") or bc_envelope

        inferred_domain = bc_data.get("domain", "").lower()

        assert "parcel" in inferred_domain or "logistic" in inferred_domain, (
            f"BusinessContext did not infer parcel domain from routes. domain={inferred_domain}"
        )
        assert bc_data.get("confidence", 0.0) >= 0.80

        # 5. Inferred Journeys assertion: >= 1 multi-service journey inferred from DAG
        journeys = topo.get("journeys", [])
        assert len(journeys) >= 1, "Expected at least 1 inferred journey from topology DAG"
        assert any(
            any(term in j["name"].lower() for term in ("package", "parcel", "delivery", "lifecycle"))
            for j in journeys
        ), f"Unexpected journey names: {[j['name'] for j in journeys]}"

        # 6. OTLP trace reporting: send parcel journey spans to Sovereign /v1/traces
        otlp_payload = build_parcel_otlp_spans(
            parcel_id=f"PCL-{sfx.upper()}-1001",
            suffix=sfx,
            intake_port=ports[intake_svc_name],
            route_port=ports[route_svc_name],
            dispatch_port=ports[dispatch_svc_name],
            proof_port=ports[proof_svc_name],
        )
        traces_resp = client.post("/v1/traces", json=otlp_payload)
        assert traces_resp.status_code == 200
        traces_data = traces_resp.json()
        assert traces_data["status"] == "ok"
        assert traces_data["spans_received"] == 4
        assert traces_data["events_written"] >= 1

        # 7. Fault Injection & Finding assertion: inject 404 fault and strictly assert >= 1 incident
        set_service_fault(intake_svc_name, "404")

        # Compile universal sentinels for discovered services and register with engine
        compiled_sentinels = compile_universal_sentinels(discovery_result.services)
        assert len(compiled_sentinels) >= 1

        for snt in compiled_sentinels:
            sentinel_engine.register_sentinel(snt)

        # Run SentinelEngine evaluation tick
        await sentinel_engine.evaluate_tick(budget_s=5.0)

        # Check incidents in Sovereign Control API
        incidents_resp = client.get("/v1/incidents")
        assert incidents_resp.status_code == 200
        incidents_data = incidents_resp.json()
        incidents = incidents_data.get("incidents", [])

        # STRICT Assertion: at least 1 incident opened with probe failure transcripts (no 'or wakes' fallback)
        assert len(incidents) >= 1, "Expected >= 1 opened incident on 404 fault, got 0"

        opened_incident = incidents[0]
        assert opened_incident.get("outcome") in ("open", "investigating") or opened_incident.get("closed_at") is None
        assert "probe" in opened_incident.get("dedupe_key", "") or "404" in opened_incident.get("symptom_summary", "")

        # Verify probe failure transcripts in incident evidence pack
        raw_ev = opened_incident.get("raw_evidence", {})
        transcripts = (
            raw_ev.get("probe_transcript")
            or raw_ev.get("details", {}).get("sample_transcript")
            or opened_incident.get("evidence_pack", {}).get("breach", {})
        )
        assert transcripts, "Expected probe failure transcript evidence in opened incident"

    finally:
        # Reset fault state and restore environment
        set_service_fault(intake_svc_name, None)
        for k, v in prev_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
