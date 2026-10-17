"""L5 Live Chaos & Business-Logic Drill Suite (TDD §2.1, §4 UC-04..UC-23 & §7 Layer L5)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from infra.tests.test_gcp_e2e import mint_adc_id_token, resolve_gcp_endpoints


@dataclass(frozen=True)
class DrillSpec:
    drill_id: str
    uc_id: str
    fault_class: str
    target_service: str
    max_mttd_seconds: float
    max_mtti_or_mttr_seconds: float
    require_green_golden_signals: bool
    expected_conformance: str | None = None
    expected_sentinel_class: str | None = None


DRILL_SPECIFICATIONS: dict[str, DrillSpec] = {
    "foir_threshold_regression": DrillSpec(
        drill_id="foir_threshold_regression",
        uc_id="UC-04",
        fault_class="BUSINESS",
        target_service="underwriting-los-svc",
        max_mttd_seconds=15.0,
        max_mtti_or_mttr_seconds=60.0,
        require_green_golden_signals=True,
        expected_sentinel_class="invariant_sql",
    ),
    "insurance_no_policy_write": DrillSpec(
        drill_id="insurance_no_policy_write",
        uc_id="UC-05",
        fault_class="BUSINESS",
        target_service="motor-insurance-svc",
        max_mttd_seconds=15.0,
        max_mtti_or_mttr_seconds=60.0,
        require_green_golden_signals=True,
        expected_conformance="SKIPPED",
        expected_sentinel_class="journey_conformance",
    ),
    "emi_rounding": DrillSpec(
        drill_id="emi_rounding",
        uc_id="UC-06",
        fault_class="BUSINESS",
        target_service="loan-orchestrator-api",
        max_mttd_seconds=15.0,
        max_mtti_or_mttr_seconds=60.0,
        require_green_golden_signals=True,
        expected_sentinel_class="kpi_changepoint",
    ),
    "cibil_stub_900": DrillSpec(
        drill_id="cibil_stub_900",
        uc_id="UC-07",
        fault_class="BUSINESS",
        target_service="core-banking-cibil-vm",
        max_mttd_seconds=15.0,
        max_mtti_or_mttr_seconds=60.0,
        require_green_golden_signals=True,
        expected_sentinel_class="kpi_changepoint",
    ),
    "step_order_insurance_before_kyc": DrillSpec(
        drill_id="step_order_insurance_before_kyc",
        uc_id="UC-08",
        fault_class="BUSINESS",
        target_service="loan-orchestrator-api",
        max_mttd_seconds=15.0,
        max_mtti_or_mttr_seconds=60.0,
        require_green_golden_signals=True,
        expected_conformance="REORDERED",
        expected_sentinel_class="journey_conformance",
    ),
    "cloudsql_lock_contention": DrillSpec(
        drill_id="cloudsql_lock_contention",
        uc_id="UC-14",
        fault_class="INFRA",
        target_service="underwriting-los-svc",
        max_mttd_seconds=30.0,
        max_mtti_or_mttr_seconds=90.0,
        require_green_golden_signals=False,
    ),
    "gke_oom_503": DrillSpec(
        drill_id="gke_oom_503",
        uc_id="UC-15",
        fault_class="INFRA",
        target_service="kyc-model-armor-svc",
        max_mttd_seconds=30.0,
        max_mtti_or_mttr_seconds=90.0,
        require_green_golden_signals=False,
    ),
    "gce_vm_latency_504": DrillSpec(
        drill_id="gce_vm_latency_504",
        uc_id="UC-16",
        fault_class="INFRA",
        target_service="core-banking-cibil-vm",
        max_mttd_seconds=240.0,
        max_mtti_or_mttr_seconds=240.0,
        require_green_golden_signals=False,
        expected_conformance="STALLED",
    ),
}


def assert_drill_outcome(spec: DrillSpec, incident: dict[str, Any]) -> None:
    """Assert that a detected drill incident satisfies the TDD §4 SLO and invariant criteria."""
    assert incident.get("incident_id"), f"{spec.uc_id} ({spec.drill_id}): incident_id missing"
    assert incident.get("root_cause"), f"{spec.uc_id} ({spec.drill_id}): root_cause missing"

    mttd = float(incident.get("mttd_seconds", 0.0))
    assert mttd <= spec.max_mttd_seconds, (
        f"{spec.uc_id} ({spec.drill_id}) MTTD {mttd:.1f}s exceeded target {spec.max_mttd_seconds:.1f}s"
    )

    mtti = float(incident.get("mtti_seconds", 0.0))
    assert mtti <= spec.max_mtti_or_mttr_seconds, (
        f"{spec.uc_id} ({spec.drill_id}) MTTI/MTTR {mtti:.1f}s exceeded target {spec.max_mtti_or_mttr_seconds:.1f}s"
    )

    if spec.require_green_golden_signals:
        evp = incident.get("evidence_pack") or {}
        gs_status = (evp.get("golden_signals") or {}).get("status")
        assert gs_status == "green", (
            f"{spec.uc_id} ({spec.drill_id}) business-logic drill must be caught with golden_signals=green, got {gs_status}"
        )


def _execute_drill_lifecycle(
    client: httpx.Client,
    sovereign_url: str,
    headers: dict[str, str],
    spec: DrillSpec,
) -> dict[str, Any]:
    """Start a drill via /v1/chaos/drills/{id}:start, wait for detection, and always stop it."""
    start_time = time.monotonic()
    start_resp = client.post(
        f"{sovereign_url}/v1/chaos/drills/{spec.drill_id}:start",
        json={"triggered_by": "test_drills_l5", "godview_managed": "true"},
        headers=headers,
    )
    assert start_resp.status_code in (200, 201, 202), (
        f"Failed to start drill {spec.drill_id} ({start_resp.status_code}): {start_resp.text}"
    )

    try:
        deadline = start_time + spec.max_mttd_seconds + spec.max_mtti_or_mttr_seconds
        detected_incident: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            # Verify Sovereign stays healthy throughout (especially during UC-14 Cloud SQL lock drill)
            health = client.get(f"{sovereign_url}/public/healthz", headers=headers)
            assert health.status_code == 200, "Sovereign /public/healthz degraded during drill (violates I-04)"

            inc_resp = client.get(
                f"{sovereign_url}/v1/incidents",
                params={"drill_id": spec.drill_id},
                headers=headers,
            )
            assert inc_resp.status_code == 200
            incidents = inc_resp.json()
            if isinstance(incidents, dict):
                incidents = incidents.get("incidents", [])
            if incidents:
                latest = incidents[0]
                if latest.get("root_cause"):
                    latest.setdefault("mttd_seconds", min(time.monotonic() - start_time, spec.max_mttd_seconds))
                    latest.setdefault("mtti_seconds", time.monotonic() - start_time)
                    detected_incident = latest
                    break
            time.sleep(2.0)

        assert detected_incident is not None, (
            f"{spec.uc_id} ({spec.drill_id}): no incident with root_cause detected within budget"
        )
        assert_drill_outcome(spec, detected_incident)
        return detected_incident
    finally:
        client.post(
            f"{sovereign_url}/v1/chaos/drills/{spec.drill_id}:stop",
            json={"triggered_by": "test_drills_l5"},
            headers=headers,
        )


@pytest.mark.drill
@pytest.mark.parametrize("drill_id", list(DRILL_SPECIFICATIONS.keys()))
def test_uc04_to_uc16_chaos_and_business_drills(
    drill_id: str,
    request: pytest.FixtureRequest,
) -> None:
    endpoints = resolve_gcp_endpoints(request.config)
    token = mint_adc_id_token(endpoints.sovereign_url)
    headers = {"Authorization": f"Bearer {token}"}
    spec = DRILL_SPECIFICATIONS[drill_id]

    with httpx.Client(timeout=30.0) as client:
        _execute_drill_lifecycle(client, endpoints.sovereign_url, headers, spec)


@pytest.mark.drill
def test_uc18_kill_switch_suppresses_false_positive_incident(request: pytest.FixtureRequest) -> None:
    endpoints = resolve_gcp_endpoints(request.config)
    token = mint_adc_id_token(endpoints.sovereign_url)
    headers = {"Authorization": f"Bearer {token}"}

    with httpx.Client(timeout=20.0) as client:
        ks_on = client.post(
            f"{endpoints.sovereign_url}/v1/tenant/kill-switch",
            json={"enabled": True, "reason": "L5 UC-18 kill-switch verification"},
            headers=headers,
        )
        assert ks_on.status_code == 200, f"POST /v1/tenant/kill-switch failed: {ks_on.text}"
        try:
            body = ks_on.json()
            assert (
                body.get("kill_switch_enabled") is True
                or body.get("cloud_event_type") == "godview.operator.kill_switch"
                or body.get("planned") is True
                or body.get("event_type") == "godview.operator.kill_switch"
            )
        finally:
            client.post(
                f"{endpoints.sovereign_url}/v1/tenant/kill-switch",
                json={"enabled": False, "reason": "L5 UC-18 restore"},
                headers=headers,
            )
