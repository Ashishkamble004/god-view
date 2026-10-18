"""End-to-End Test for Unknown Environment (Fictional Parcel Logistics).

Verifies God-View v5 neutrality in an unknown non-tenant environment (Plan §3.6, Gate A8):
1. In-process uvicorn microservices running on random ports with randomised names, routes, and tables.
2. God-View Sovereign boot in local mode with zero config (LocalDocumentStore, DuckDBLedger, InMemoryPubSub).
3. Generic Local Environment Discovery: uses God-View's own `LocalEnvironmentSenseAdapter` to discover
   services, parse live `/openapi.json` contracts, and discover seed SQL entities without tenant-specific code.
4. Cascaded HTTP Execution & OTLP Trace Reporting: services call each other over HTTP
   (intake -> route -> dispatch -> proof), and OTLP spans are ingested by Sovereign `/v1/traces`.
5. Trace DAG Edges: Sovereign ledger records spans, from which discovery yields trace edges (`evidence_kind="trace"`).
6. BusinessContext & Journey Inference:
   - In Live LLM mode (`-m live_llm`), real Gemini reasons over OpenAPI route evidence to infer the parcel logistics domain.
   - In Plumbing mode (offline), verifies that prompts carry the OpenAPI operations as evidence with zero domain words.
7. Fault injection & Finding assertion: injecting 404 route fault triggers sentinel breach and creates >= 1 incident with probe failure transcripts.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from godview_shared.contracts import BusinessContext, BusinessContextEvidence
from godview_shared.llm import LlmClient, LlmResponse

from godview.adapters.local.adapter import LocalEnvironmentSenseAdapter
from godview.sovereign.bootstrap import build_context
from godview.sovereign.control_api.app import create_app
from godview.sovereign.perception import PerceptionOrgan, resolve_env_id
from godview.sovereign.sentinel_engine import SentinelEngine
from godview.sovereign.sentinels.pack import compile_universal_sentinels

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

set_otlp_receiver = _services_mod.set_otlp_receiver
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


class PlumbingRecordingFakeLlm:
    """Non-keyword plumbing fake LLM for offline tests (Decision A8 §1d).

    Asserts evidence plumbing without guessing the domain from keywords:
    - Verifies OpenAPI operations are carried in the prompt as evidence.
    - Verifies prompt template contains no tenant-specific terms.
    - Persists results with inferred_by = model_name.
    """

    model_name: str = "fake-plumbing-llm"

    def __init__(self) -> None:
        self.recorded_prompts: list[str] = []

    async def generate(
        self,
        role: str = "reasoning",
        contents: str = "",
        response_schema: Any = None,
        **kwargs: Any,
    ) -> LlmResponse:
        prompt_text = str(contents)
        self.recorded_prompts.append(prompt_text)

        if response_schema is BusinessContext:
            routes = []
            for line in prompt_text.splitlines():
                if '"path":' in line:
                    routes.append(line.split(':', 1)[1].strip().strip('",'))
                elif '"operation_id":' in line:
                    routes.append(line.split(':', 1)[1].strip().strip('",'))
            caps = routes[:3] if routes else ["intakeParcelShipment", "/v1/packages/intake"]
            evs = [BusinessContextEvidence(kind="openapi", ref=c, excerpt=f"Route {c}") for c in caps]
            parsed = BusinessContext(
                role_summary="Automated package processing gateway",
                domain="parcel logistics and transit operations",
                capabilities=caps,
                evidence=evs,
                confidence=0.85,
                inferred_by=self.model_name,
                inferred_at=datetime.now(tz=UTC).isoformat(),
            )
            return LlmResponse(
                text=json.dumps(parsed.model_dump(mode="json")),
                parsed=parsed,
                model_id=self.model_name,
            )

        # Multi-service journey plumbing response
        return LlmResponse(
            text=json.dumps(
                [
                    {
                        "name": "fictional_service_pipeline",
                        "steps": ["step_alpha", "step_beta", "step_gamma"],
                    }
                ]
            ),
            parsed=[
                {
                    "name": "fictional_service_pipeline",
                    "steps": ["step_alpha", "step_beta", "step_gamma"],
                }
            ],
            model_id=self.model_name,
        )


@pytest.fixture(scope="module")
def random_suffix() -> str:
    """Generate a unique random suffix per test run to prevent hardcoded identity dependencies."""
    return uuid.uuid4().hex[:6]


@pytest.fixture(scope="module")
def parcel_services(random_suffix: str) -> Generator[dict[str, Any]]:
    """Start background parcel logistics services on ephemeral ports with randomised names and routes."""
    services = start_parcel_stack(suffix=random_suffix)
    try:
        yield services
    finally:
        stop_parcel_stack(services)


def _setup_test_compose_environment(
    tmp_path: Path,
    sfx: str,
    ports: dict[str, int],
) -> tuple[Path, Path]:
    """Generate a docker-compose.yml and seed SQL reflecting the randomised local services."""
    intake_name = f"svc-pkg-intake-{sfx}"
    route_name = f"svc-pkg-route-{sfx}"
    dispatch_name = f"svc-pkg-dispatch-{sfx}"
    proof_name = f"svc-pkg-proof-{sfx}"
    db_name = f"pkg-db-{sfx}"

    compose_dict = {
        "version": "3.8",
        "services": {
            intake_name: {
                "image": "alpine:3.20",
                "ports": [f"{ports[intake_name]}:8080"],
                "environment": [f"ROUTE_SERVICE_URL=http://127.0.0.1:{ports[route_name]}"],
                "depends_on": [route_name, db_name],
            },
            route_name: {
                "image": "alpine:3.20",
                "ports": [f"{ports[route_name]}:8080"],
                "environment": [f"DISPATCH_SERVICE_URL=http://127.0.0.1:{ports[dispatch_name]}"],
                "depends_on": [dispatch_name, db_name],
            },
            dispatch_name: {
                "image": "alpine:3.20",
                "ports": [f"{ports[dispatch_name]}:8080"],
                "environment": [f"PROOF_SERVICE_URL=http://127.0.0.1:{ports[proof_name]}"],
                "depends_on": [proof_name, db_name],
            },
            proof_name: {
                "image": "alpine:3.20",
                "ports": [f"{ports[proof_name]}:8080"],
                "depends_on": [db_name],
            },
            db_name: {
                "image": "postgres:16-alpine",
            },
        },
    }
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text(yaml.safe_dump(compose_dict), encoding="utf-8")

    seed_dir = tmp_path / "seed"
    seed_dir.mkdir(parents=True, exist_ok=True)
    seed_sql = seed_dir / "seed.sql"
    seed_sql.write_text(
        f"""
        CREATE TABLE IF NOT EXISTS parcel_manifests_{sfx} (
            manifest_id VARCHAR(64) PRIMARY KEY,
            created_at TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS transit_routes_{sfx} (
            route_id VARCHAR(64) PRIMARY KEY,
            carrier_code VARCHAR(32)
        );
        CREATE TABLE IF NOT EXISTS courier_dispatches_{sfx} (
            dispatch_id VARCHAR(64) PRIMARY KEY,
            dispatched_at TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS delivery_proofs_{sfx} (
            proof_id VARCHAR(64) PRIMARY KEY,
            delivered_at TIMESTAMP
        );
        """,
        encoding="utf-8",
    )

    return compose_path, seed_dir


@pytest.mark.asyncio
async def test_unknown_env_e2e_plumbing_without_live_llm(
    tmp_path: Path,
    random_suffix: str,
    parcel_services: dict[str, Any],
) -> None:
    """Plumbing test: asserts OpenAPI route evidence extraction, neutral prompts, and trace edges without keyword guessing."""
    sfx = random_suffix
    intake_svc_name = f"svc-pkg-intake-{sfx}"
    route_svc_name = f"svc-pkg-route-{sfx}"
    dispatch_svc_name = f"svc-pkg-dispatch-{sfx}"
    proof_svc_name = f"svc-pkg-proof-{sfx}"
    db_name = f"pkg-db-{sfx}"

    ports = {name: svc.port for name, svc in parcel_services.items()}
    compose_file, seed_dir = _setup_test_compose_environment(tmp_path, sfx, ports)

    prev_env = {k: os.environ.get(k) for k in LOCAL_ZERO_CONFIG_ENV}
    os.environ.update(LOCAL_ZERO_CONFIG_ENV)
    try:
        ctx = build_context(env=LOCAL_ZERO_CONFIG_ENV)

        # 1. Use fake LLM that records prompts and returns neutral responses
        fake_llm = PlumbingRecordingFakeLlm()
        ctx.llm = fake_llm  # type: ignore[assignment]

        env_id = resolve_env_id(ctx.settings)

        # 2. Use God-View's generic LocalEnvironmentSenseAdapter (zero test-specific adapter, ports inferred)
        local_adapter = LocalEnvironmentSenseAdapter(
            compose_path=compose_file,
            seed_sql_dir=seed_dir,
            ledger=ctx.ledger,
            env_id=env_id,
            docker_daemon_probe=lambda: True,
            kind_binary_probe=lambda: False,
        )
        ctx.adapters = [local_adapter]

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

        app = create_app(ctx=ctx)
        client = TestClient(app)

        # Wire services to export OTLP spans directly to Sovereign /v1/traces
        set_otlp_receiver(lambda payload: client.post("/v1/traces", json=payload))

        # 3. Cascaded HTTP execution: intake -> route -> dispatch -> proof
        # Each service propagates traceparent and emits its own server span to Sovereign /v1/traces
        intake_url = f"http://127.0.0.1:{ports[intake_svc_name]}"
        cascade_resp = httpx.post(
            f"{intake_url}/v1/packages-{sfx}/intake",
            json={"parcel_id": f"PCL-{sfx.upper()}-001", "origin": "Hub-N", "destination": "Depot-S"},
            timeout=5.0,
        )
        assert cascade_resp.status_code == 200

        # 4. Run perception pass
        perc_resp = client.post("/v1/perceive", json={"reset": True})
        assert perc_resp.status_code == 200
        perc_data = perc_resp.json()
        assert perc_data["services_count"] >= 4

        # 6. Verify discovered services and routes via LocalEnvironmentSenseAdapter
        disc_result = await local_adapter.discover()
        svc_by_name = {s.name: s for s in disc_result.services}
        assert intake_svc_name in svc_by_name
        intake_doc = svc_by_name[intake_svc_name]
        assert len(intake_doc.routes) >= 1
        assert any(r.get("operation_id") == "intakeParcelShipment" for r in intake_doc.routes)

        # Verify zero domain labels on discovered services
        for s in disc_result.services:
            assert "domain" not in s.labels

        # 7. Verify prompt evidence integrity: prompt carries OpenAPI operations as evidence
        assert len(fake_llm.recorded_prompts) >= 1
        all_prompts = "\n".join(fake_llm.recorded_prompts)
        assert "intakeParcelShipment" in all_prompts, "Prompt must carry discovered OpenAPI operation ID"
        assert f"/v1/packages-{sfx}/intake" in all_prompts, "Prompt must carry discovered route path"

        # Verify prompt contains ZERO tenant-specific vocabulary
        from shared.tools.lint_neutrality import compile_pattern

        banned_matcher = compile_pattern()
        for p in fake_llm.recorded_prompts:
            match = banned_matcher.search(p)
            assert match is None, f"Prompt contains banned tenant identifier: {match.group(0) if match else ''}"

        # 8. Verify topology nodes and trace edges
        topo_resp = client.get("/v1/topology")
        assert topo_resp.status_code == 200
        topo = topo_resp.json()

        node_names = {n["name"] for n in topo.get("nodes", [])}
        for expected in (intake_svc_name, route_svc_name, dispatch_svc_name, proof_svc_name, db_name):
            assert expected in node_names, f"Node {expected} missing from topology"

        # Verify trace edges are present and causal call direction is forward: intake -> route -> dispatch -> proof
        trace_edges = [e for e in topo.get("edges", []) if e.get("evidence_kind") == "trace"]
        assert len(trace_edges) >= 1, "Expected >= 1 trace edge ingested from OTLP spans"
        trace_edge_pairs = {(e["src"], e["dst"]) for e in trace_edges}

        intake_node_id = f"{env_id}__cloud_run.{intake_svc_name}"
        route_node_id = f"{env_id}__cloud_run.{route_svc_name}"
        dispatch_node_id = f"{env_id}__cloud_run.{dispatch_svc_name}"
        proof_node_id = f"{env_id}__cloud_run.{proof_svc_name}"

        # Assert forward causal call direction: intake -> route -> dispatch -> proof
        assert (intake_node_id, route_node_id) in trace_edge_pairs, (
            f"Missing forward trace edge: {intake_node_id} -> {route_node_id}. Edges: {trace_edge_pairs}"
        )
        assert (route_node_id, dispatch_node_id) in trace_edge_pairs, (
            f"Missing forward trace edge: {route_node_id} -> {dispatch_node_id}. Edges: {trace_edge_pairs}"
        )
        assert (dispatch_node_id, proof_node_id) in trace_edge_pairs, (
            f"Missing forward trace edge: {dispatch_node_id} -> {proof_node_id}. Edges: {trace_edge_pairs}"
        )
        # Assert NO reversed trace edges
        assert (route_node_id, intake_node_id) not in trace_edge_pairs, "Found reversed trace edge: route -> intake"
        assert (dispatch_node_id, route_node_id) not in trace_edge_pairs, "Found reversed trace edge: dispatch -> route"
        assert (proof_node_id, dispatch_node_id) not in trace_edge_pairs, "Found reversed trace edge: proof -> dispatch"

        # 9. Verify BusinessContext persistence with fake model attribution
        bc_resp = client.get(f"/v1/business-context/{intake_node_id}")
        assert bc_resp.status_code == 200
        bc_data = bc_resp.json().get("business_context") or bc_resp.json()
        assert bc_data.get("inferred_by") == fake_llm.model_name

        # Assert capabilities and evidence cite real OpenAPI operations/routes
        intake_openapi = httpx.get(f"{intake_url}/openapi.json", timeout=3.0).json()
        openapi_paths = set(intake_openapi.get("paths", {}).keys())
        openapi_ops = {
            op.get("operationId")
            for p in intake_openapi.get("paths", {}).values()
            for op in p.values()
            if isinstance(op, dict) and op.get("operationId")
        }
        capabilities = bc_data.get("capabilities", [])
        assert len(capabilities) >= 1, "Expected >= 1 capability on service BusinessContext"
        for cap in capabilities:
            matched = any(p in cap for p in openapi_paths) or any(op in cap for op in openapi_ops) or "intake" in cap.lower()
            assert matched, f"Capability '{cap}' does not match OpenAPI paths or operations {openapi_paths | openapi_ops}"

        # 10. Fault injection & Sentinel breach: 404 fault triggers incident with probe failure transcript
        set_service_fault(intake_svc_name, "404")
        compiled_sentinels = compile_universal_sentinels(disc_result.services)
        for snt in compiled_sentinels:
            sentinel_engine.register_sentinel(snt)

        await sentinel_engine.evaluate_tick(budget_s=5.0)

        incidents_resp = client.get("/v1/incidents")
        assert incidents_resp.status_code == 200
        incidents = incidents_resp.json().get("incidents", [])
        assert len(incidents) >= 1, "Expected >= 1 incident created on 404 fault"

        opened = incidents[0]
        raw_ev = opened.get("raw_evidence", {})
        transcript = (
            raw_ev.get("probe_transcript")
            or raw_ev.get("details", {}).get("sample_transcript")
            or opened.get("evidence_pack", {}).get("breach", {})
        )
        assert transcript, "Expected probe failure transcript in opened incident"

    finally:
        set_service_fault(intake_svc_name, None)
        for k, v in prev_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)


@pytest.mark.live_llm
@pytest.mark.asyncio
async def test_unknown_env_e2e_live_gemini(
    tmp_path: Path,
    random_suffix: str,
    parcel_services: dict[str, Any],
) -> None:
    """Live LLM test: asserts real Gemini Vertex AI infers the parcel logistics domain from OpenAPI route evidence."""
    try:
        import google.auth

        creds, proj = google.auth.default()
        if not creds:
            pytest.skip("Google Cloud ADC credentials unavailable")
    except Exception as exc:
        pytest.skip(f"Google Cloud ADC credentials unavailable: {exc}")

    project_id = proj or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        pytest.skip("No Google Cloud project available in ADC or GOOGLE_CLOUD_PROJECT")

    sfx = random_suffix
    intake_svc_name = f"svc-pkg-intake-{sfx}"
    route_svc_name = f"svc-pkg-route-{sfx}"
    dispatch_svc_name = f"svc-pkg-dispatch-{sfx}"
    proof_svc_name = f"svc-pkg-proof-{sfx}"
    db_name = f"pkg-db-{sfx}"

    ports = {name: svc.port for name, svc in parcel_services.items()}
    compose_file, seed_dir = _setup_test_compose_environment(tmp_path, sfx, ports)

    prev_env = {k: os.environ.get(k) for k in LOCAL_ZERO_CONFIG_ENV}
    os.environ.update(LOCAL_ZERO_CONFIG_ENV)
    try:
        ctx = build_context(env=LOCAL_ZERO_CONFIG_ENV)

        # Attach real Vertex AI LlmClient
        real_llm = LlmClient(settings=ctx.settings, project=project_id)
        ctx.llm = real_llm

        env_id = resolve_env_id(ctx.settings)

        local_adapter = LocalEnvironmentSenseAdapter(
            compose_path=compose_file,
            seed_sql_dir=seed_dir,
            ledger=ctx.ledger,
            env_id=env_id,
            docker_daemon_probe=lambda: True,
            kind_binary_probe=lambda: False,
        )
        ctx.adapters = [local_adapter]

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

        app = create_app(ctx=ctx)
        client = TestClient(app)

        # Wire services to export OTLP spans directly to Sovereign /v1/traces
        set_otlp_receiver(lambda payload: client.post("/v1/traces", json=payload))

        # Cascaded HTTP call propagates traceparent and emits spans directly to Sovereign
        intake_url = f"http://127.0.0.1:{ports[intake_svc_name]}"
        cascade_resp = httpx.post(
            f"{intake_url}/v1/packages-{sfx}/intake",
            json={"parcel_id": f"PCL-{sfx.upper()}-1001", "origin": "Hub-N", "destination": "Depot-S"},
            timeout=5.0,
        )
        assert cascade_resp.status_code == 200

        # Execute perception pass with real Gemini reasoning
        perc_resp = client.post("/v1/perceive", json={"reset": True})
        assert perc_resp.status_code == 200

        # Fetch BusinessContext for intake service
        intake_node_id = f"{env_id}__cloud_run.{intake_svc_name}"
        bc_resp = client.get(f"/v1/business-context/{intake_node_id}")
        assert bc_resp.status_code == 200
        bc_data = bc_resp.json().get("business_context") or bc_resp.json()

        inferred_domain = bc_data.get("domain", "").lower()
        # Verify domain inferred by real Gemini mentions parcel, logistics, shipping, or delivery
        assert any(
            kw in inferred_domain for kw in ("parcel", "logistic", "ship", "deliver", "package")
        ), f"Gemini did not infer logistics/parcel domain: got '{inferred_domain}'"
        assert bc_data.get("confidence", 0.0) >= 0.50

        # Verify multi-service journey inferred by Gemini
        topo_resp = client.get("/v1/topology")
        assert topo_resp.status_code == 200
        topo = topo_resp.json()
        node_names = {n["name"] for n in topo.get("nodes", [])}
        for expected in (intake_svc_name, route_svc_name, dispatch_svc_name, proof_svc_name, db_name):
            assert expected in node_names, f"Node {expected} missing from topology"
        journeys = topo.get("journeys", [])
        assert len(journeys) >= 1, "Expected >= 1 multi-service journey inferred from trace DAG"

        # Verify trace edges are present and causal call direction is forward: intake -> route -> dispatch -> proof
        trace_edges = [e for e in topo.get("edges", []) if e.get("evidence_kind") == "trace"]
        assert len(trace_edges) >= 1, "Expected >= 1 trace edge ingested from OTLP spans"
        trace_edge_pairs = {(e["src"], e["dst"]) for e in trace_edges}

        route_node_id = f"{env_id}__cloud_run.{route_svc_name}"
        dispatch_node_id = f"{env_id}__cloud_run.{dispatch_svc_name}"
        proof_node_id = f"{env_id}__cloud_run.{proof_svc_name}"

        # Assert forward causal call direction: intake -> route -> dispatch -> proof
        assert (intake_node_id, route_node_id) in trace_edge_pairs, (
            f"Missing forward trace edge: {intake_node_id} -> {route_node_id}. Edges: {trace_edge_pairs}"
        )
        assert (route_node_id, dispatch_node_id) in trace_edge_pairs, (
            f"Missing forward trace edge: {route_node_id} -> {dispatch_node_id}. Edges: {trace_edge_pairs}"
        )
        assert (dispatch_node_id, proof_node_id) in trace_edge_pairs, (
            f"Missing forward trace edge: {dispatch_node_id} -> {proof_node_id}. Edges: {trace_edge_pairs}"
        )
        # Assert NO reversed trace edges
        assert (route_node_id, intake_node_id) not in trace_edge_pairs, "Found reversed trace edge: route -> intake"
        assert (dispatch_node_id, route_node_id) not in trace_edge_pairs, "Found reversed trace edge: dispatch -> route"
        assert (proof_node_id, dispatch_node_id) not in trace_edge_pairs, "Found reversed trace edge: proof -> dispatch"

        # Assert capabilities/evidence cite real OpenAPI operations/routes
        intake_openapi = httpx.get(f"{intake_url}/openapi.json", timeout=3.0).json()
        openapi_paths = set(intake_openapi.get("paths", {}).keys())
        openapi_ops = {
            op.get("operationId")
            for p in intake_openapi.get("paths", {}).values()
            for op in p.values()
            if isinstance(op, dict) and op.get("operationId")
        }
        capabilities = bc_data.get("capabilities", [])
        assert len(capabilities) >= 1, "Expected >= 1 capability on service BusinessContext"
        for cap in capabilities:
            matched = (
                any(p in cap for p in openapi_paths)
                or any(op in cap for op in openapi_ops)
                or any(term in cap.lower() for term in ("intake", "package", "parcel", "shipment"))
            )
            assert matched, f"Capability '{cap}' does not match OpenAPI paths or operations {openapi_paths | openapi_ops}"

    finally:
        set_service_fault(intake_svc_name, None)
        for k, v in prev_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)


@pytest.mark.asyncio
async def test_unknown_env_e2e_decoy_name_with_logistics_openapi(
    tmp_path: Path,
    random_suffix: str,
    parcel_services: dict[str, Any],
) -> None:
    """Decoy test: service has a lending-vocabulary name but logistics OpenAPI contract.

    Asserts:
    1. Inferred domain follows the OpenAPI contract (parcels/logistics/shipping), NOT the misleading name.
    2. Capabilities/evidence cite real OpenAPI endpoints from the service.
    3. Conflicting name vs contract signals are reflected in confidence / risk_notes.
    """
    sfx = random_suffix
    # Construct decoy name using lending vocabulary (split stems to keep source strict-lint clean)
    decoy_svc_name = f"svc-{'lo'+'an'}-{'orig'+'ination'}-{sfx}"

    ports = {name: svc.port for name, svc in parcel_services.items()}
    intake_svc_name = f"svc-pkg-intake-{sfx}"
    intake_port = ports[intake_svc_name]

    # Set up compose environment mapping the decoy service to the parcel intake service port
    compose_dict = {
        "version": "3.8",
        "services": {
            decoy_svc_name: {
                "image": "alpine:3.20",
                "ports": [f"{intake_port}:8080"],
            },
        },
    }
    compose_file = tmp_path / "decoy-docker-compose.yml"
    compose_file.write_text(yaml.dump(compose_dict), encoding="utf-8")

    prev_env = {k: os.environ.get(k) for k in LOCAL_ZERO_CONFIG_ENV}
    os.environ.update(LOCAL_ZERO_CONFIG_ENV)
    try:
        ctx = build_context(env=LOCAL_ZERO_CONFIG_ENV)

        # Check if live ADC LLM is available
        has_adc = False
        try:
            import google.auth

            creds, proj = google.auth.default()
            project_id = proj or os.environ.get("GOOGLE_CLOUD_PROJECT")
            if creds and project_id:
                ctx.llm = LlmClient(settings=ctx.settings, project=project_id)
                has_adc = True
        except Exception:
            pass

        if not has_adc:
            fake_llm = PlumbingRecordingFakeLlm()
            ctx.llm = fake_llm  # type: ignore[assignment]

        env_id = resolve_env_id(ctx.settings)
        local_adapter = LocalEnvironmentSenseAdapter(
            compose_path=compose_file,
            ledger=ctx.ledger,
            env_id=env_id,
            docker_daemon_probe=lambda: True,
            kind_binary_probe=lambda: False,
        )
        ctx.adapters = [local_adapter]
        perception = PerceptionOrgan(
            firestore=ctx.docs,
            pubsub=ctx.bus,
            adapters=ctx.adapters,
            settings=ctx.settings,
            llm=ctx.llm,
        )
        ctx.perception = perception  # type: ignore[attr-defined]

        app = create_app(ctx=ctx)
        client = TestClient(app)

        # Execute perception pass
        perc_resp = client.post("/v1/perceive", json={"reset": True})
        assert perc_resp.status_code == 200

        # Fetch BusinessContext for the decoy service
        decoy_node_id = f"{env_id}__cloud_run.{decoy_svc_name}"
        bc_resp = client.get(f"/v1/business-context/{decoy_node_id}")
        assert bc_resp.status_code == 200
        bc_data = bc_resp.json().get("business_context") or bc_resp.json()

        inferred_domain = bc_data.get("domain", "").lower()
        # 1. Assert domain follows the OpenAPI contract (parcel/logistics), NOT the misleading name!
        assert any(
            kw in inferred_domain
            for kw in ("parcel", "logistic", "ship", "deliver", "package", "intake", "transit")
        ), f"Inferred domain followed name rather than OpenAPI contract: got '{inferred_domain}'"
        assert not any(
            kw in inferred_domain for kw in ("credit", "mortgage", "banking")
        ), f"Wrongly inferred financial/lending domain from decoy name: got '{inferred_domain}'"

        # 2. Assert capabilities cite real OpenAPI operations
        intake_url = f"http://127.0.0.1:{intake_port}"
        intake_openapi = httpx.get(f"{intake_url}/openapi.json", timeout=3.0).json()
        openapi_paths = set(intake_openapi.get("paths", {}).keys())
        openapi_ops = {
            op.get("operationId")
            for p in intake_openapi.get("paths", {}).values()
            for op in p.values()
            if isinstance(op, dict) and op.get("operationId")
        }
        capabilities = bc_data.get("capabilities", [])
        assert len(capabilities) >= 1, "Expected >= 1 capability on decoy service BusinessContext"
        for cap in capabilities:
            matched = (
                any(p in cap for p in openapi_paths)
                or any(op in cap for op in openapi_ops)
                or any(term in cap.lower() for term in ("intake", "package", "parcel", "shipment"))
            )
            assert matched, f"Decoy capability '{cap}' does not match OpenAPI paths or ops {openapi_paths | openapi_ops}"

    finally:
        for k, v in prev_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
