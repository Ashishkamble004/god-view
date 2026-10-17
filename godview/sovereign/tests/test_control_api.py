"""M1 Unit Tests for the Sovereign Control API (`godview/sovereign/control_api/`).

Covers TDD §3.4 & §3.6 & Coordinator Decisions A3, A10, A18:
- POST /v1/perceive & GET /v1/perceive (and /api/v1/* aliases per A18)
- GET /v1/court & POST /v1/court/{agent_id}:wake|retire|warm|cool
- GET /v1/sentinels, POST /v1/sentinels, PATCH /v1/sentinels/{id}
- GET /v1/incidents, GET /v1/incidents/{id}
- POST /v1/approvals/{id}:approve & :reject with IAP X-Goog-Authenticated-User-Email
- POST /v1/capabilities/{capability_id}:invoke (Decision A3 single TrustGate endpoint)
- POST /v1/tenant/kill-switch emitting godview.operator.kill_switch (suppressing traffic-drop incident)
- POST /v1/tenant/pause & POST /v1/tenant/resume (cooling warm agents to 0)
- POST /v1/hibernate (cadence -> 60 s, god-view-hibernate-job, godview.operator.hibernate)
- GET /v1/chaos/drills & POST /v1/chaos/drills/{drill_id}:start|stop (godview_managed="true" only)
- POST /public/webhooks/github with HMAC-SHA256 verification (both valid and invalid signatures)
- GET /public/healthz & GET /metrics
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from godview.sovereign.control_api import create_app


@pytest.mark.unit
def test_control_api_perceive_court_sentinels_and_health() -> None:
    """Verify /public/healthz, /metrics, /v1/perceive, /api/v1/perceive (A18), /v1/court, /v1/sentinels."""
    app = create_app(webhook_secret="test-webhook-secret-key")
    client = TestClient(app)

    # Healthz & Prometheus metrics
    r_health = client.get("/public/healthz")
    assert r_health.status_code == 200
    assert r_health.json()["status"] == "ok"

    r_metrics = client.get("/metrics")
    assert r_metrics.status_code == 200
    assert "godview" in r_metrics.text

    # Perceive via /v1/perceive and /api/v1/perceive (Decision A18 same-origin routing)
    r_perc = client.post("/v1/perceive", json={"reset": True})
    assert r_perc.status_code == 200
    data_perc = r_perc.json()
    assert "env_id" in data_perc
    assert "adapter_health" in data_perc

    r_perc_api = client.get("/api/v1/perceive")
    assert r_perc_api.status_code == 200
    assert r_perc_api.json()["env_id"] == data_perc["env_id"]

    # Court lifecycle controls: warm -> cool -> retire
    r_court = client.get("/v1/court")
    assert r_court.status_code == 200
    agents = r_court.json()["agents"]
    assert len(agents) >= 1
    agent_id = agents[0]["doc_id"]

    r_warm = client.post(f"/v1/court/{agent_id}:warm")
    assert r_warm.status_code == 200
    assert r_warm.json()["warmth_min_instances"] == 1

    r_cool = client.post(f"/v1/court/{agent_id}:cool")
    assert r_cool.status_code == 200
    assert r_cool.json()["warmth_min_instances"] == 0

    # Sentinels compile & pause
    r_snt_create = client.post(
        "/v1/sentinels",
        json={
            "sentinel_id": "snt-invariant_sql-custom-01",
            "class": "invariant_sql",
            "source_ref": "inv-custom-01",
            "check": {"dialect": "bigquery", "statement": "SELECT COUNT(*) FROM events"},
            "cadence_s": 10,
            "baseline": {"model": "same_hour", "window_days": 7},
            "wake_condition": {"operator": ">=", "threshold": 1, "consecutive": 1, "window_s": 300},
            "target_agent_id": agent_id,
            "state": "enforced",
            "compiled_by": agent_id,
            "compiled_at": "2026-10-16T08:00:00Z",
        },
    )
    assert r_snt_create.status_code == 200
    r_snt_patch = client.patch(
        "/v1/sentinels/snt-invariant_sql-custom-01", json={"state": "paused"}
    )
    assert r_snt_patch.status_code == 200
    assert r_snt_patch.json()["state"] == "paused"


@pytest.mark.unit
def test_capability_invoke_endpoint_a3_and_approvals_flow() -> None:
    """Decision A3: POST /v1/capabilities/{capability_id}:invoke enforces TrustGate and writes audit trail."""
    app = create_app(webhook_secret="test-secret")
    client = TestClient(app)
    client.post("/v1/perceive", json={"reset": True})

    # 1. L0 read-only capability executes immediately
    r_read = client.post(
        "/v1/capabilities/obs.get_trace:invoke",
        json={
            "agent_id": "application__svc-alpha-01",
            "incident_id": "inc-20261016-0001",
            "args": {"trace_id": "tr-123"},
        },
    )
    assert r_read.status_code == 200
    assert r_read.json()["status"] == "executed"

    # 2. Mutating capability (L3) when agent is at L0/L1 creates an approval card & returns approval_required
    r_mut_unauth = client.post(
        "/v1/capabilities/k8s.rollout_restart:invoke",
        json={
            "agent_id": "platform__env-01",
            "incident_id": "inc-20261016-0001",
            "target_resource": "deployment/svc-alpha-01",
            "args": {"deployment": "svc-alpha-01"},
            "reversal_recipe": {
                "capability": "k8s.rollout_restart",
                "args": {"deployment": "svc-alpha-01"},
            },
            "narrative": "Restart pod after OOM",
        },
    )
    assert r_mut_unauth.status_code == 200
    body_unauth = r_mut_unauth.json()
    assert body_unauth["status"] == "approval_required"
    approval_id = body_unauth["approval_id"]
    assert approval_id.startswith("apr-")

    # 3. Human approves via POST /v1/approvals/{id}:approve with IAP header
    r_approve = client.post(
        f"/v1/approvals/{approval_id}:approve",
        headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:operator@example.com"},
    )
    assert r_approve.status_code == 200
    approved_data = r_approve.json()
    assert approved_data["decision"] == "approved"
    assert approved_data["decided_by"] == "operator@example.com"
    assert approved_data["audit_id"].startswith("aud-")

    # 4. Now invoking with that approved approval_id executes and writes audit record
    r_mut_exec = client.post(
        "/v1/capabilities/k8s.rollout_restart:invoke",
        json={
            "agent_id": "platform__env-01",
            "incident_id": "inc-20261016-0001",
            "target_resource": "deployment/svc-alpha-01",
            "approval_id": approval_id,
            "args": {"deployment": "svc-alpha-01"},
            "reversal_recipe": {
                "capability": "k8s.rollout_restart",
                "args": {"deployment": "svc-alpha-01"},
            },
        },
    )
    assert r_mut_exec.status_code == 200
    assert r_mut_exec.json()["status"] == "executed"
    assert r_mut_exec.json()["audit_id"].startswith("aud-")


@pytest.mark.unit
def test_kill_switch_pause_resume_hibernate_and_drills_guard() -> None:
    """UC-18, UC-19, UC-20 & §3.4: Kill switch, pause/resume, hibernate (60 s), and drills managed-only guard."""
    app = create_app(webhook_secret="test-secret")
    client = TestClient(app)
    client.post("/v1/perceive", json={"reset": True})

    # Start a drill on a godview_managed="true" resource -> succeeds
    r_drill_ok = client.post(
        "/v1/chaos/drills/gke_oom_503:start",
        json={"target_service": "svc-managed-01", "labels": {"godview_managed": "true"}},
    )
    assert r_drill_ok.status_code == 200
    assert r_drill_ok.json()["is_active"] is True

    # Start a drill on an unmanaged resource -> rejected with 403 (TDD I-07)
    r_drill_bad = client.post(
        "/v1/chaos/drills/gke_oom_503:start",
        json={"target_service": "svc-unmanaged-99", "labels": {"godview_managed": "false"}},
    )
    assert r_drill_bad.status_code == 403

    # Traffic Kill Switch (UC-18): halts simulator, pauses tenant, clears chaos, emits godview.operator.kill_switch
    r_ks = client.post(
        "/v1/tenant/kill-switch",
        json={"enabled": True, "reason": "Emergency drill stop"},
        headers={"X-Goog-Authenticated-User-Email": "operator@example.com"},
    )
    assert r_ks.status_code == 200
    ks_data = r_ks.json()
    assert ks_data["tenant_paused"] is True
    assert ks_data["simulator_enabled"] is False
    assert ks_data["active_drills_cleared"] >= 1
    assert ks_data["cloud_event_type"] == "godview.operator.kill_switch"

    # Scheduled Pause (UC-19): cools all warm agents to 0
    r_pause = client.post("/v1/tenant/pause")
    assert r_pause.status_code == 200
    assert r_pause.json()["warm_agents_count"] == 0

    r_resume = client.post("/v1/tenant/resume")
    assert r_resume.status_code == 200
    assert r_resume.json()["tenant_paused"] is False

    # Hibernate (UC-20): throttles sentinel cadence to 60 s and triggers god-view-hibernate-job
    r_hib_on = client.post("/v1/hibernate", json={"enabled": True})
    assert r_hib_on.status_code == 200
    assert r_hib_on.json()["sentinel_cadence_s"] == 60
    assert r_hib_on.json()["job_triggered"] == "god-view-hibernate-job"

    r_hib_off = client.post("/v1/hibernate", json={"enabled": False})
    assert r_hib_off.status_code == 200
    assert r_hib_off.json()["sentinel_cadence_s"] == 10


@pytest.mark.unit
def test_github_webhook_hmac_verification_both_directions() -> None:
    """§3.4 & Rule B.5: /public/webhooks/github rejects invalid HMAC and accepts valid HMAC."""
    secret = "my-hmac-webhook-secret"
    app = create_app(webhook_secret=secret)
    client = TestClient(app)

    payload_bytes = json.dumps(
        {"ref": "refs/heads/main", "after": "sha1234", "repository": {"full_name": "org/repo"}}
    ).encode("utf-8")

    # 1. Invalid signature -> 401
    r_bad = client.post(
        "/public/webhooks/github",
        content=payload_bytes,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": "sha256=deadbeef",
        },
    )
    assert r_bad.status_code == 401

    # 2. Valid HMAC-SHA256 signature -> 200 + emits godview.webhook.github.push
    sig = "sha256=" + hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    r_ok = client.post(
        "/public/webhooks/github",
        content=payload_bytes,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": sig,
        },
    )
    assert r_ok.status_code == 200
    assert r_ok.json()["event_type"] == "godview.webhook.github.push"


@pytest.mark.unit
def test_otlp_traces_receiver_and_configure_exporters_pipeline() -> None:
    """Decision #125: configure_exporters exports OTLP/HTTP JSON to POST /v1/traces -> LedgerSpanExporter."""
    import httpx
    from godview_shared.otel import (
        configure_exporters,
        get_tracer,
        set_business_span_attributes,
    )

    app = create_app(webhook_secret="test-secret")
    client = TestClient(app)

    # 1. Hermetic mode is strictly a no-op (no HTTP requests even if endpoint is set)
    configure_exporters(
        "hermetic-svc",
        env={
            "GODVIEW_ENV": "hermetic",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9",
        },
        use_simple_processor=True,
    )
    t_hermetic = get_tracer("hermetic-svc")
    with t_hermetic.start_as_current_span("noop-op") as sp:
        set_business_span_attributes(sp, journey_id="jrn-noop", decision="APPROVED")

    # 2. Local/GCP mode exports spans via OTLP/HTTP JSON to POST /v1/traces (and /api/v1/traces)
    def _mock_handler(request: httpx.Request) -> httpx.Response:
        resp = client.post(
            request.url.path, content=request.content, headers={"Content-Type": "application/json"}
        )
        return httpx.Response(resp.status_code, json=resp.json())

    transport = httpx.MockTransport(_mock_handler)
    configure_exporters(
        "svc-alpha-telemetry",
        env={
            "GODVIEW_ENV": "local",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://sovereign:8080",
        },
        transport=transport,
        use_simple_processor=True,
    )
    tracer = get_tracer("svc-alpha-telemetry")
    with tracer.start_as_current_span("evaluate.step") as span:
        set_business_span_attributes(
            span,
            journey_id="jrn-trace-001",
            customer_id="cust-trace-01",
            decision="APPROVED",
            foir_pct=41.5,
            cibil_score=765,
            status_from="STEP_A",
            status_to="STEP_B",
            event_type="step.evaluated",
            amount_inr=250000.0,
            synthetic=True,
        )

    events = app.state.bigquery.get_table("events")
    transitions = app.state.bigquery.get_table("state_transitions")
    journeys = app.state.bigquery.get_table("journeys")
    assert len(events) == 1
    assert events[0]["journey_id"] == "jrn-trace-001"
    assert (
        events[0].get("cibil_score") == 765
        or events[0].get("numeric_attributes", {}).get("cibil_score") == 765.0
    )
    assert len(transitions) == 1
    assert transitions[0]["status_to"] == "STEP_B"
    assert len(journeys) == 1
    assert journeys[0]["journey_id"] == "jrn-trace-001"

    # 3. Direct OTLP resourceSpans payload to /api/v1/traces also works
    r_otlp = client.post(
        "/api/v1/traces",
        json={
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "svc-beta"}}
                        ]
                    },
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": "0102030405060708090a0b0c0d0e0f10",
                                    "spanId": "0102030405060708",
                                    "name": "settle.op",
                                    "startTimeUnixNano": "1760600000000000000",
                                    "endTimeUnixNano": "1760600000150000000",
                                    "attributes": [
                                        {
                                            "key": "journey.id",
                                            "value": {"stringValue": "jrn-trace-002"},
                                        },
                                        {"key": "decision", "value": {"stringValue": "APPROVED"}},
                                        {"key": "cibil_score", "value": {"intValue": "780"}},
                                        {"key": "foir_pct", "value": {"doubleValue": 35.0}},
                                        {"key": "synthetic", "value": {"boolValue": True}},
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        },
    )
    assert r_otlp.status_code == 200
    assert r_otlp.json()["events_written"] == 1


@pytest.mark.unit
def test_control_api_Remaining_endpoints_reject_snooze_whitelist_and_summaries() -> None:
    """Verify reject, snooze, whitelist, wake, retire, drills list/stop, changes, cost, incidents."""
    app = create_app(webhook_secret="test-secret")
    client = TestClient(app)
    client.post("/v1/perceive", json={"reset": True})

    # Create an approval card and test snooze + reject
    r_req = client.post(
        "/v1/capabilities/k8s.scale:invoke",
        json={
            "agent_id": "platform__env-01",
            "incident_id": "inc-20261016-0009",
            "target_resource": "deployment/svc-alpha-01",
            "args": {"replicas": 2},
        },
    )
    approval_id = r_req.json()["approval_id"]

    r_list_apr = client.get("/v1/approvals")
    assert r_list_apr.status_code == 200
    assert len(r_list_apr.json()["approvals"]) >= 1

    r_snooze = client.post(f"/v1/approvals/{approval_id}:snooze")
    assert r_snooze.status_code == 200
    assert r_snooze.json()["decision"] == "snoozed"

    # Create a second approval card to reject
    r_req2 = client.post(
        "/v1/capabilities/k8s.scale:invoke",
        json={
            "agent_id": "platform__env-01",
            "incident_id": "inc-20261016-0010",
            "target_resource": "deployment/svc-alpha-02",
            "args": {"replicas": 3},
        },
    )
    apr2 = r_req2.json()["approval_id"]
    r_rej = client.post(
        f"/v1/approvals/{apr2}:reject",
        headers={"X-Goog-Authenticated-User-Email": "operator@example.com"},
    )
    assert r_rej.status_code == 200
    assert r_rej.json()["decision"] == "rejected"

    # Whitelist trust record (requires IAP header)
    r_wl_unauth = client.post("/v1/trust-records/platform__env-01__k8s.scale:whitelist")
    assert r_wl_unauth.status_code == 403

    r_wl_ok = client.post(
        "/v1/trust-records/platform__env-01__k8s.scale:whitelist",
        headers={"X-Goog-Authenticated-User-Email": "owner@example.com"},
    )
    assert r_wl_ok.status_code == 200
    assert r_wl_ok.json()["whitelisted_by"] == "owner@example.com"

    # Court wake & retire
    agents = client.get("/v1/court").json()["agents"]
    target_agent = agents[0]["doc_id"]
    r_wake = client.post(
        f"/v1/court/{target_agent}:wake",
        json={"incident_id": "inc-20261016-0011", "sentinel_id": "snt-1", "summary": "Manual wake"},
    )
    assert r_wake.status_code == 200
    r_retire = client.post(f"/v1/court/{target_agent}:retire")
    assert r_retire.status_code == 200
    assert r_retire.json()["state"] == "archived"

    # Drills list & stop, changes, cost, invariants, journeys, incidents
    assert client.get("/v1/chaos/drills").status_code == 200
    assert client.post("/v1/chaos/drills/gke_oom_503:stop").status_code == 200
    assert client.get("/v1/changes").status_code == 200
    assert client.get("/v1/cost").status_code == 200
    assert client.get("/v1/invariants").status_code == 200
    assert client.get("/v1/journeys").status_code == 200
    assert client.get("/v1/incidents").status_code == 200
    assert client.get("/v1/sentinels").status_code == 200


@pytest.mark.unit
def test_cockpit_contracts_simulator_drift_sse_webhooks_and_adapter_dispatch() -> None:
    """Findings #2, #3, #6, #7, #10: Cockpit routes, schemas, webhook events, and SenseAdapter dispatch."""
    from datetime import UTC, datetime
    from typing import Any

    from godview_shared.adapter_contract import (
        AdapterHealth,
        DiscoveryResult,
        SenseAdapter,
        SenseCategory,
    )
    from godview_shared.capabilities import CAPABILITY_REGISTRY, CapabilitySpec
    from godview_shared.contracts import TrustDecision

    class _DummyAdapter(SenseAdapter):
        family = "kubernetes"
        name = "dummy-k8s"

        def senses(self) -> list[SenseCategory]:
            return [SenseCategory.SCOPED_OBSERVABILITY]

        def capabilities(self) -> dict[str, CapabilitySpec]:
            return {"k8s.get_resource": CAPABILITY_REGISTRY["k8s.get_resource"]}

        async def health(self) -> AdapterHealth:
            return AdapterHealth(
                adapter_name="dummy-k8s",
                family="kubernetes",
                state="available",
                evidence={"clusters": 1},
                checked_at=datetime.now(tz=UTC),
            )

        async def discover(self) -> DiscoveryResult:
            return DiscoveryResult()

        async def _execute_capability_impl(
            self,
            capability_id: str,
            args: dict[str, Any],
            trust_decision: TrustDecision | None = None,
        ) -> Any:
            return {"pods": ["pod-alpha-01"], "namespace": args.get("namespace", "default")}

    app = create_app(webhook_secret="test-secret", adapters=[_DummyAdapter()])
    client = TestClient(app)

    # 1. GET /api/v1/me
    r_me = client.get(
        "/api/v1/me",
        headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:op@example.com"},
    )
    assert r_me.status_code == 200
    assert r_me.json()["email"] == "op@example.com"
    assert r_me.json()["iap_authenticated"] is True

    # 2. POST /api/v1/perceive returns full Cockpit PerceptionReport shape
    r_perc = client.post("/api/v1/perceive", json={"reset": True})
    assert r_perc.status_code == 200
    perc = r_perc.json()
    for key in (
        "env_id",
        "sovereign_id",
        "sovereign_status",
        "heartbeat_at",
        "sentinel_cadence_s",
        "senses",
        "discovered_services",
        "discovered_edges",
        "discovered_entities",
        "operator_state",
    ):
        assert key in perc

    # 3. GET /api/v1/events/stream SSE endpoint
    r_sse = client.get("/api/v1/events/stream")
    assert r_sse.status_code == 200
    assert "text/event-stream" in r_sse.headers["content-type"]
    assert "sovereign.heartbeat" in r_sse.text

    # 4. Simulator state and journeys burst
    r_sim_state = client.post("/api/v1/simulator/state", json={"enabled": False})
    assert r_sim_state.status_code == 200
    assert r_sim_state.json()["simulator_enabled"] is False

    r_sim_jrn = client.post("/api/v1/simulator/journeys", json={"count": 7})
    assert r_sim_jrn.status_code == 200
    assert r_sim_jrn.json()["simulator_journeys_today"] >= 7

    # 5. Terraform drift remediation endpoints & GET /api/v1/cost overview
    r_drift_restore = client.post("/api/v1/cost/drift/drift-01:restore_attribute", json={})
    assert r_drift_restore.status_code == 200
    assert r_drift_restore.json()["status"] == "restored"

    r_drift_apply = client.post("/api/v1/cost/drift/drift-01:apply_dispatch", json={})
    assert r_drift_apply.status_code == 200
    assert r_drift_apply.json()["status"] == "reconciling"

    r_cost = client.get("/api/v1/cost")
    assert r_cost.status_code == 200
    assert "drift_diffs" in r_cost.json()
    assert "daily_court_ceiling_usd" in r_cost.json()

    # 6. POST /api/v1/sentinels with {"invariant_id": "..."} and invalid payload -> 422
    r_compile = client.post("/api/v1/sentinels", json={"invariant_id": "inv-alpha-01"})
    assert r_compile.status_code == 200
    assert r_compile.json()["sentinel_id"] == "snt-invariant_sql-alpha-01"

    r_bad_snt = client.post("/api/v1/sentinels", json={"invariant_id": ""})
    assert r_bad_snt.status_code == 422

    # 7. POST /api/v1/chaos/drills/{id}:start with empty body {} (Cockpit default)
    r_drill_empty = client.post("/api/v1/chaos/drills/gke_oom_503:start", json={})
    assert r_drill_empty.status_code == 200
    assert r_drill_empty.json()["is_active"] is True
    assert r_drill_empty.json()["fault_class"] == "infrastructure"

    # 8. Whitelist toggle off ({"whitelisted": False})
    iap_hdr = {"X-Goog-Authenticated-User-Email": "accounts.google.com:owner@example.com"}
    client.post(
        "/api/v1/trust-records/platform__env-01__k8s.scale:whitelist",
        json={"whitelisted": True},
        headers=iap_hdr,
    )
    r_unwl = client.post(
        "/api/v1/trust-records/platform__env-01__k8s.scale:whitelist",
        json={"whitelisted": False},
        headers=iap_hdr,
    )
    assert r_unwl.status_code == 200
    assert r_unwl.json()["whitelisted"] is False

    # 9. SenseAdapter capability dispatch via POST /v1/capabilities/k8s.get_resource:invoke
    r_cap = client.post(
        "/v1/capabilities/k8s.get_resource:invoke",
        json={"agent_id": "platform__env-01", "args": {"namespace": "prod-ns"}},
    )
    assert r_cap.status_code == 200
    assert r_cap.json()["result"]["adapter_family"] == "kubernetes"
    assert r_cap.json()["result"]["adapter_result"]["pods"] == ["pod-alpha-01"]

    # 10. GitHub webhook PR SHA extraction & unsupported event 400
    pr_bytes = json.dumps(
        {
            "pull_request": {"head": {"sha": "abcdef123456"}},
            "repository": {"full_name": "org/repo-alpha"},
        }
    ).encode("utf-8")
    pr_sig = "sha256=" + hmac.new(b"test-secret", pr_bytes, hashlib.sha256).hexdigest()
    r_pr = client.post(
        "/public/webhooks/github",
        content=pr_bytes,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": pr_sig,
        },
    )
    assert r_pr.status_code == 200
    assert r_pr.json()["change_event_id"] == "chg-abcdef12"

    r_unsup = client.post(
        "/public/webhooks/github",
        content=pr_bytes,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "star",
            "X-Hub-Signature-256": pr_sig,
        },
    )
    assert r_unsup.status_code == 400

    # 11. Cloud Scheduler & Pub/Sub push endpoints (#189): /v1/probes/run, /v1/memory/reflect, /v1/nervous-system/push
    r_probe = client.post(
        "/v1/probes/run", json={"source": "cloud_scheduler", "action": "probe_run"}
    )
    assert r_probe.status_code == 200
    assert r_probe.json()["status"] == "ok"
    assert r_probe.json()["action"] == "probe_run"

    r_reflect = client.post(
        "/v1/memory/reflect", json={"source": "cloud_scheduler", "action": "reflect"}
    )
    assert r_reflect.status_code == 200
    assert r_reflect.json()["status"] == "ok"
    assert r_reflect.json()["action"] == "reflect"

    import base64

    audit_b64 = base64.b64encode(
        json.dumps(
            {
                "protoPayload": {
                    "methodName": "google.cloud.run.v2.Services.UpdateService",
                    "resourceName": "projects/p/locations/us-central1/services/svc-alpha-01",
                },
                "timestamp": "2026-10-17T08:00:00Z",
            }
        ).encode("utf-8")
    ).decode("ascii")
    r_push = client.post(
        "/v1/nervous-system/push",
        json={"message": {"data": audit_b64, "messageId": "msg-audit-01"}},
    )
    assert r_push.status_code == 200
    assert r_push.json()["status"] == "acknowledged"

    r_lc_pause = client.post("/v1/lifecycle/tenant-pause", json={"source": "cloud_scheduler"})
    assert r_lc_pause.status_code == 200
    assert r_lc_pause.json()["tenant_paused"] is True

    r_probe_paused = client.post("/v1/probes/run", json={"source": "cloud_scheduler"})
    assert r_probe_paused.status_code == 200
    assert r_probe_paused.json()["status"] == "skipped"

    r_lc_resume = client.post("/v1/lifecycle/tenant-resume", json={"source": "cloud_scheduler"})
    assert r_lc_resume.status_code == 200
    assert r_lc_resume.json()["tenant_paused"] is False
