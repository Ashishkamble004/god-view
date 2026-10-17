"""End-to-End Test for Unknown Environment (Fictional Parcel Logistics).

Verifies God-View v5 neutrality in an unknown non-tenant environment (Plan §3.6, Gate A8):
1. In-process uvicorn microservices running on random ports (parcel intake -> route -> dispatch -> proof).
2. God-View Sovereign boot in local mode with zero config (LocalDocumentStore, DuckDBLedger, InMemoryPubSub).
3. Perception & Topology assertion: correct nodes and dependency edges discovered.
4. BusinessContext assertion: inferred domain mentions 'parcels' or 'logistics' without hardcoding.
5. Inferred journeys assertion: >= 1 multi-service journey inferred from topology DAG.
6. OTLP trace reporting: sends parcel journey spans to Sovereign `/v1/traces`.
7. Fault injection & Finding assertion: injecting 404 route fault triggers sentinel breach and creates >= 1 incident.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

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


@pytest.fixture(scope="module")
def parcel_services():
    """Start background parcel logistics services on ephemeral ports."""
    services = start_parcel_stack()
    try:
        yield services
    finally:
        stop_parcel_stack(services)


@pytest.mark.asyncio
async def test_unknown_env_e2e_full_lifecycle(parcel_services: dict[str, Any]) -> None:
    """Execute end-to-end unknown environment verification against fictional parcel logistics."""
    # 1. Inspect live service ports
    ports = {name: svc.port for name, svc in parcel_services.items()}
    assert len(ports) == 4
    for _name, port in ports.items():
        assert port > 1024

    # Verify live OpenAPI specs are served by the services
    for name, svc in parcel_services.items():
        resp = httpx.get(f"{svc.base_url}/openapi.json", timeout=2.0)
        assert resp.status_code == 200
        schema = resp.json()
        assert "paths" in schema
        assert name in schema["info"]["title"]

    # 2. Boot God-View Sovereign in local mode with zero config
    prev_env = {k: os.environ.get(k) for k in LOCAL_ZERO_CONFIG_ENV}
    os.environ.update(LOCAL_ZERO_CONFIG_ENV)
    try:
        ctx = build_context(env=LOCAL_ZERO_CONFIG_ENV)

        # Attach the unknown environment sense adapter pointing to the running services
        adapter = UnknownLogisticsSenseAdapter(
            env_id="unknown-parcel-env",
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
        assert perc_data["env_id"] == "unknown-parcel-env"
        assert perc_data["services_count"] >= 4

        # Fetch canonical topology
        topo_resp = client.get("/v1/topology")
        assert topo_resp.status_code == 200
        topo = topo_resp.json()

        node_names = {n["name"] for n in topo.get("nodes", [])}
        expected_services = {
            "parcel-intake-svc",
            "parcel-route-svc",
            "parcel-dispatch-svc",
            "parcel-proof-svc",
            "parcel-db",
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
        assert ("parcel-intake-svc", "parcel-route-svc") in edge_pairs
        assert ("parcel-route-svc", "parcel-dispatch-svc") in edge_pairs
        assert ("parcel-dispatch-svc", "parcel-proof-svc") in edge_pairs

        # 4. BusinessContext assertion: domain mentions 'parcels' or 'logistics'
        intake_node_id = "unknown-parcel-env__cloud_run.parcel-intake-svc"
        bc_resp = client.get(f"/v1/business-context/{intake_node_id}")
        assert bc_resp.status_code == 200
        bc_data = bc_resp.json()

        inferred_domain = bc_data.get("domain", "").lower()
        role_summary = bc_data.get("role_summary", "").lower()
        capabilities = [c.lower() for c in bc_data.get("capabilities", [])]

        domain_mentions_parcels = "parcel" in inferred_domain or "logistic" in inferred_domain
        summary_mentions_parcels = "parcel" in role_summary or "logistic" in role_summary
        caps_mention_parcels = any("parcel" in c for c in capabilities)

        assert (
            domain_mentions_parcels or summary_mentions_parcels or caps_mention_parcels
        ), f"BusinessContext did not infer parcel domain. domain={inferred_domain}, role={role_summary}"

        # 5. Inferred Journeys assertion: >= 1 multi-service journey inferred from DAG
        journeys = topo.get("journeys", [])
        assert len(journeys) >= 1, "Expected at least 1 inferred journey from topology DAG"
        assert any(
            any(term in j["name"].lower() for term in ("parcel", "flow", "courier", "dispatch", "route", "proof"))
            for j in journeys
        ), f"Unexpected journey names: {[j['name'] for j in journeys]}"

        # 6. OTLP trace reporting: send parcel journey spans to Sovereign /v1/traces
        otlp_payload = build_parcel_otlp_spans(
            parcel_id="PCL-TEST-1001",
            intake_port=ports["parcel-intake-svc"],
            route_port=ports["parcel-route-svc"],
            dispatch_port=ports["parcel-dispatch-svc"],
            proof_port=ports["parcel-proof-svc"],
        )
        traces_resp = client.post("/v1/traces", json=otlp_payload)
        assert traces_resp.status_code == 200
        traces_data = traces_resp.json()
        assert traces_data["status"] == "ok"
        assert traces_data["spans_received"] == 4
        assert traces_data["events_written"] >= 1

        # 7. Fault Injection & Finding assertion: inject 404 fault and assert >= 1 incident
        # Inject 404 on parcel-intake-svc
        set_service_fault("parcel-intake-svc", "404")

        # Compile universal sentinels for discovered services and register with engine
        discovery_result = await adapter.discover()
        compiled_sentinels = compile_universal_sentinels(discovery_result.services)
        assert len(compiled_sentinels) >= 1

        for snt in compiled_sentinels:
            sentinel_engine.register_sentinel(snt)

        # Run SentinelEngine evaluation tick
        tick_report = await sentinel_engine.evaluate_tick(budget_s=5.0)

        # Check incidents in Sovereign Control API
        incidents_resp = client.get("/v1/incidents")
        assert incidents_resp.status_code == 200
        incidents_data = incidents_resp.json()
        incidents = incidents_data.get("incidents", [])

        # Verify >= 1 finding / incident opened
        assert len(incidents) >= 1 or len(tick_report.wakes_sent) >= 1, (
            f"Expected >= 1 finding/incident on 404 fault, got {len(incidents)} incidents, "
            f"{len(tick_report.wakes_sent)} wakes"
        )

        if incidents:
            opened_incident = incidents[0]
            assert opened_incident.get("outcome") in ("open", "investigating") or opened_incident.get("closed_at") is None
            assert "probe" in opened_incident.get("dedupe_key", "") or "404" in opened_incident.get("symptom_summary", "")

    finally:
        # Reset fault state and restore environment
        set_service_fault("parcel-intake-svc", None)
        for k, v in prev_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
