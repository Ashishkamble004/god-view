"""Unit and contract tests for Utility 2: underwriting-los-svc.

Covers TDD §2.1 (underwriting_audits), §3.2 (POST /internal/underwriting/evaluate, /metrics, /healthz),
§4 UC-04, UC-07, UC-14, UC-15, and §6.2 #7.
"""

from __future__ import annotations

import importlib
import json
import sys
import time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

SERVICE_ROOT = Path(__file__).resolve().parents[1]


def _load_local_app_package() -> Any:
    """Ensure `app` imports resolve strictly to underwriting-los-svc/app even in multi-service pytest runs."""
    svc_path = str(SERVICE_ROOT)
    if svc_path in sys.path:
        sys.path.remove(svc_path)
    sys.path.insert(0, svc_path)

    for mod_name in list(sys.modules.keys()):
        if mod_name == "app" or mod_name.startswith("app."):
            mod = sys.modules[mod_name]
            mod_file = getattr(mod, "__file__", "") or ""
            if not str(mod_file).startswith(svc_path):
                del sys.modules[mod_name]

    return importlib.import_module("app.main")


_main_mod = _load_local_app_package()
create_app = _main_mod.create_app
rules_mod = importlib.import_module("app.rules_engine")
cibil_mod = importlib.import_module("app.cibil_client")
chaos_mod = importlib.import_module("app.chaos")

MIN_CIBIL_SCORE = rules_mod.MIN_CIBIL_SCORE
MAX_FOIR_PCT = rules_mod.MAX_FOIR_PCT
RULES_VERSION = rules_mod.RULES_VERSION
calculate_emi_inr = rules_mod.calculate_emi_inr
calculate_foir_pct = rules_mod.calculate_foir_pct
calculate_ltv_pct = rules_mod.calculate_ltv_pct
evaluate_underwriting_rules = rules_mod.evaluate_underwriting_rules
CibilScoreClient = cibil_mod.CibilScoreClient
ChaosFaultManager = chaos_mod.ChaosFaultManager


def _independent_emi(principal: float, annual_rate_pct: float, tenure_months: int) -> Decimal:
    """Independent reducing-balance EMI calculation for verification."""
    p = Decimal(str(principal))
    n = Decimal(str(tenure_months))
    if annual_rate_pct == 0:
        return (p / n).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    r = Decimal(str(annual_rate_pct)) / Decimal("1200")
    factor = (Decimal("1") + r) ** int(tenure_months)
    emi = (p * r * factor) / (factor - Decimal("1"))
    return emi.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("principal", "annual_rate_pct", "tenure_months"),
    [
        (950000.0, 9.25, 48),
        (1500000.0, 8.75, 60),
        (500000.0, 10.50, 36),
        (2200000.0, 9.90, 72),
        (120000.0, 11.25, 24),
        (600000.0, 0.0, 24),
    ],
)
def test_emi_foir_ltv_maths_exact_across_cases(
    principal: float,
    annual_rate_pct: float,
    tenure_months: int,
) -> None:
    assert MIN_CIBIL_SCORE == 680
    assert MAX_FOIR_PCT == Decimal("60.00")
    assert isinstance(RULES_VERSION, str) and len(RULES_VERSION) >= 6

    expected_emi = _independent_emi(principal, annual_rate_pct, tenure_months)
    computed_emi = calculate_emi_inr(
        loan_amount_inr=Decimal(str(principal)),
        interest_rate_pct=Decimal(str(annual_rate_pct)),
        tenure_months=tenure_months,
    )
    assert computed_emi == expected_emi
    assert abs(computed_emi - expected_emi) <= Decimal("1.00")

    foir = calculate_foir_pct(emi_inr=computed_emi, monthly_income_inr=Decimal("100000.00"))
    expected_foir = ((computed_emi / Decimal("100000.00")) * Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    assert foir == expected_foir

    ltv = calculate_ltv_pct(
        loan_amount_inr=Decimal(str(principal)),
        ex_showroom_inr=Decimal(str(principal * 1.25)),
    )
    assert ltv == Decimal("80.00")


@pytest.mark.unit
def test_rules_engine_decision_boundaries_both_directions() -> None:
    """Approval iff cibil_score >= 680 AND foir_pct <= 60.00."""
    # Exact boundary: 680 and 60.00 -> APPROVED
    res_boundary_ok = evaluate_underwriting_rules(
        loan_amount_inr=Decimal("600000"),
        down_payment_inr=Decimal("150000"),
        tenure_months=10,
        interest_rate_pct=Decimal("0"),
        monthly_income_inr=Decimal("100000"),
        cibil_score=680,
    )
    assert res_boundary_ok.foir_pct == Decimal("60.00")
    assert res_boundary_ok.decision == "APPROVED"
    assert res_boundary_ok.rejection_reason is None

    # CIBIL 679 (< 680) with low FOIR -> REJECTED
    res_low_cibil = evaluate_underwriting_rules(
        loan_amount_inr=Decimal("300000"),
        down_payment_inr=Decimal("100000"),
        tenure_months=24,
        interest_rate_pct=Decimal("9.0"),
        monthly_income_inr=Decimal("150000"),
        cibil_score=679,
    )
    assert res_low_cibil.decision == "REJECTED"
    assert res_low_cibil.rejection_reason is not None
    assert "679" in res_low_cibil.rejection_reason
    assert "680" in res_low_cibil.rejection_reason

    # FOIR > 60.00 with high CIBIL 790 -> REJECTED
    res_high_foir = evaluate_underwriting_rules(
        loan_amount_inr=Decimal("1800000"),
        down_payment_inr=Decimal("200000"),
        tenure_months=24,
        interest_rate_pct=Decimal("10.0"),
        monthly_income_inr=Decimal("100000"),
        cibil_score=790,
    )
    assert res_high_foir.foir_pct > Decimal("60.00")
    assert res_high_foir.decision == "REJECTED"
    assert res_high_foir.rejection_reason is not None
    assert "FOIR" in res_high_foir.rejection_reason


@pytest.mark.unit
def test_evaluate_endpoint_propagates_traceparent_and_baggage_to_cbs_and_returns_200_on_rejection() -> (
    None
):
    """Verify W3C traceparent + baggage propagation to core-banking-cibil-vm:8085 and HTTP 200 on business rejections."""
    captured_cbs_requests: list[httpx.Request] = []

    def mock_cbs_handler(request: httpx.Request) -> httpx.Response:
        captured_cbs_requests.append(request)
        body = json.loads(request.content.decode("utf-8"))
        pan = body.get("pan_number", "")
        score = 620 if pan == "LOWCB1234Z" else 765
        return httpx.Response(
            200,
            json={
                "cibil_score": score,
                "cibil_bureau_ref": f"CIBIL-REF-2026-{score}",
                "gce_vm_name": "core-banking-cibil-vm",
                "pan_number": pan,
            },
        )

    cibil_client = CibilScoreClient(
        base_url="http://core-banking-cibil-vm:8085",
        http_client=httpx.Client(transport=httpx.MockTransport(mock_cbs_handler)),
    )
    spans_recorded: list[dict[str, Any]] = []
    app = create_app(cibil_client=cibil_client, span_recorder=spans_recorded)
    client = TestClient(app)

    # 1. Approved application
    resp_ok = client.post(
        "/internal/underwriting/evaluate",
        headers={
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            "baggage": "journey.id=jrn-los-101,customer.id=cust-101,synthetic=true",
            "X-Synthetic": "true",
        },
        json={
            "loan_id": "11111111-2222-3333-4444-555555555555",
            "customer_id": "cust-101",
            "journey_id": "jrn-los-101",
            "pan_number": "ABCDE1234Z",
            "loan_amount_inr": 950000.0,
            "down_payment_inr": 250000.0,
            "ex_showroom_inr": 1200000.0,
            "tenure_months": 48,
            "interest_rate_pct": 9.25,
            "monthly_income_inr": 145000.0,
        },
    )
    assert resp_ok.status_code == 200, resp_ok.text
    data_ok = resp_ok.json()
    assert data_ok["decision"] == "APPROVED"
    assert data_ok["status"] == "APPROVED"
    assert data_ok["cibil_score"] == 765
    assert data_ok["rules_version"] == RULES_VERSION
    assert data_ok["gke_pod_name"]
    assert data_ok["gce_vm_name"] == "core-banking-cibil-vm"
    assert Decimal(str(data_ok["foir_pct"])) <= Decimal("60.00")

    # Verify outbound CBS request headers carried traceparent, baggage, and X-Synthetic
    assert len(captured_cbs_requests) == 1
    cbs_req = captured_cbs_requests[0]
    assert cbs_req.url == httpx.URL("http://core-banking-cibil-vm:8085/internal/cibil-cbs/score")
    assert (
        cbs_req.headers["traceparent"] == "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    )
    assert "journey.id=jrn-los-101" in cbs_req.headers["baggage"]
    assert "customer.id=cust-101" in cbs_req.headers["baggage"]
    assert "synthetic=true" in cbs_req.headers["baggage"]
    assert cbs_req.headers["X-Synthetic"] == "true"

    # Verify span attributes
    assert len(spans_recorded) == 1
    span_ok = spans_recorded[0]
    assert span_ok["decision"] == "APPROVED"
    assert span_ok["status_from"] == "KYC_VERIFIED"
    assert span_ok["status_to"] == "APPROVED"
    assert span_ok["cibil_score"] == 765
    assert span_ok["foir_pct"] == float(Decimal(str(data_ok["foir_pct"])))
    assert span_ok["emi_inr"] == float(Decimal(str(data_ok["emi_inr"])))
    assert span_ok["rules_version"] == RULES_VERSION
    assert span_ok["event_type"] == "loan.decided"

    # 2. Business rejection returns HTTP 200 (never 4xx/5xx) with decision="REJECTED"
    resp_rej = client.post(
        "/internal/underwriting/evaluate",
        json={
            "loan_id": "22222222-3333-4444-5555-666666666666",
            "customer_id": "cust-102",
            "pan_number": "LOWCB1234Z",
            "loan_amount_inr": 950000.0,
            "down_payment_inr": 250000.0,
            "tenure_months": 48,
            "interest_rate_pct": 9.25,
            "monthly_income_inr": 35000.0,
        },
    )
    assert resp_rej.status_code == 200, resp_rej.text
    data_rej = resp_rej.json()
    assert data_rej["decision"] == "REJECTED"
    assert data_rej["status"] == "REJECTED"
    assert data_rej["cibil_score"] == 620
    assert data_rej["rejection_reason"] is not None
    assert "CIBIL" in data_rej["rejection_reason"]
    assert "FOIR" in data_rej["rejection_reason"]


@pytest.mark.unit
def test_chaos_hooks_gke_oom_503_and_cloudsql_lock_contention() -> None:
    """Verify UC-14 (cloudsql_lock_contention lock lifecycle) and UC-15 (gke_oom_503) in both directions."""
    executed_sql: list[str] = []
    closed_connections: list[bool] = []

    class FakeCursor:
        def execute(self, sql: str) -> None:
            executed_sql.append(sql)

        def __enter__(self) -> FakeCursor:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

    class FakeConnection:
        def cursor(self) -> FakeCursor:
            return FakeCursor()

        def rollback(self) -> None:
            executed_sql.append("ROLLBACK")

        def close(self) -> None:
            closed_connections.append(True)

    chaos_mgr = ChaosFaultManager(
        service_name="underwriting-los-svc",
        db_conn_factory=FakeConnection,
    )
    app = create_app(chaos_manager=chaos_mgr)
    client = TestClient(app)

    # 1. Activate cloudsql_lock_contention with a short 0.15s duration
    lock_resp = client.post(
        "/internal/chaos/faults",
        json={"fault_type": "cloudsql_lock_contention", "duration_seconds": 1},
    )
    assert lock_resp.status_code == 200
    assert any("LOCK TABLE loan_applications" in s for s in executed_sql)

    # Clear lock fault and confirm ROLLBACK + connection close
    clear_lock = client.delete("/internal/chaos/faults/cloudsql_lock_contention")
    assert clear_lock.status_code == 200
    time.sleep(0.05)
    assert "ROLLBACK" in executed_sql
    assert len(closed_connections) >= 1

    # 2. Activate gke_oom_503 -> returns 503 on evaluate, then 200 after clear
    oom_on = client.post(
        "/internal/chaos/faults",
        json={"fault_type": "gke_oom_503", "duration_seconds": 60},
    )
    assert oom_on.status_code == 200

    eval_503 = client.post(
        "/internal/underwriting/evaluate",
        json={
            "loan_id": "33333333-3333-3333-3333-333333333333",
            "pan_number": "ABCDE1234Z",
            "loan_amount_inr": 500000.0,
            "down_payment_inr": 100000.0,
            "tenure_months": 36,
            "interest_rate_pct": 9.0,
            "monthly_income_inr": 100000.0,
            "cibil_score": 750,
        },
    )
    assert eval_503.status_code == 503
    assert eval_503.json()["error_code"] == "GKE_OOM_503"

    client.delete("/internal/chaos/faults/gke_oom_503")
    eval_200 = client.post(
        "/internal/underwriting/evaluate",
        headers={"X-Synthetic": "true"},
        json={
            "loan_id": "33333333-3333-3333-3333-333333333333",
            "pan_number": "ABCDE1234Z",
            "loan_amount_inr": 500000.0,
            "down_payment_inr": 100000.0,
            "tenure_months": 36,
            "interest_rate_pct": 9.0,
            "monthly_income_inr": 100000.0,
            "cibil_score": 750,
        },
    )
    assert eval_200.status_code == 200
    assert eval_200.json()["decision"] == "APPROVED"

    assert client.get("/healthz").status_code == 200
    assert client.get("/metrics").status_code == 200


@pytest.mark.unit
def test_rev2_guards_no_disburse_fallback_synthetic_cibil_override_and_lock_failures() -> None:
    """Verify all #119 verifier findings in both directions:
    - 404 on /score raises CibilScoreEndpointError and NEVER falls back to /score-and-disburse
    - cibil_score override rejected with 422 without X-Synthetic / baggage synthetic=true, accepted with it
    - RULES_VERSION is file SHA-256 prefix (never constant)
    - missing pan_number rejected with 422
    - lock failure raises 500 and leaves fault inactive (and reports pending while acquiring)
    - chaos endpoints enforce Google OIDC ID token and expose chaos.activate / chaos.clear
    """
    import hashlib
    import threading

    CibilScoreEndpointError = cibil_mod.CibilScoreEndpointError

    # 1. 404 on /score raises CibilScoreEndpointError and never calls /score-and-disburse
    called_paths: list[str] = []

    def _mock_404(request: httpx.Request) -> httpx.Response:
        called_paths.append(request.url.path)
        return httpx.Response(404, json={"detail": "Not found"})

    client_404 = CibilScoreClient(
        base_url="http://core-banking-cibil-vm:8085",
        http_client=httpx.Client(transport=httpx.MockTransport(_mock_404)),
    )
    with pytest.raises(CibilScoreEndpointError) as exc_info:
        client_404.fetch_score(loan_id="loan-404", pan_number="ABCDE1234Z")
    assert exc_info.value.status_code == 404
    assert called_paths == ["/internal/cibil-cbs/score"]

    # 2. RULES_VERSION is derived from SHA-256 of rules_engine.py bytes[:12]
    expected_sha = hashlib.sha256(Path(rules_mod.__file__).read_bytes()).hexdigest()[:12]
    assert RULES_VERSION == expected_sha
    assert rules_mod.rules_version == expected_sha

    # 3. Caller-supplied cibil_score without X-Synthetic / baggage synthetic=true -> 422 (0 CBS calls)
    cbs_calls = 0

    def _unexpected_cbs(request: httpx.Request) -> httpx.Response:
        nonlocal cbs_calls
        cbs_calls += 1
        return httpx.Response(200, json={"cibil_score": 750, "cibil_bureau_ref": "REF"})

    app = create_app(
        cibil_client=CibilScoreClient(
            base_url="http://core-banking-cibil-vm:8085",
            http_client=httpx.Client(transport=httpx.MockTransport(_unexpected_cbs)),
        )
    )
    client = TestClient(app)

    non_syn_override = client.post(
        "/internal/underwriting/evaluate",
        json={
            "loan_id": "44444444-4444-4444-4444-444444444444",
            "pan_number": "ABCDE1234Z",
            "loan_amount_inr": 500000.0,
            "down_payment_inr": 100000.0,
            "tenure_months": 36,
            "interest_rate_pct": 9.0,
            "monthly_income_inr": 100000.0,
            "cibil_score": 900,
        },
    )
    assert non_syn_override.status_code == 422
    assert non_syn_override.json()["error_code"] == "SYNTHETIC_HEADER_REQUIRED_FOR_CIBIL_OVERRIDE"
    assert cbs_calls == 0

    # With baggage synthetic=true -> 200 APPROVED
    syn_override = client.post(
        "/internal/underwriting/evaluate",
        headers={"baggage": "journey.id=jrn-syn,synthetic=true"},
        json={
            "loan_id": "44444444-4444-4444-4444-444444444444",
            "pan_number": "ABCDE1234Z",
            "loan_amount_inr": 500000.0,
            "down_payment_inr": 100000.0,
            "tenure_months": 36,
            "interest_rate_pct": 9.0,
            "monthly_income_inr": 100000.0,
            "cibil_score": 900,
        },
    )
    assert syn_override.status_code == 200
    assert syn_override.json()["decision"] == "APPROVED"

    # Missing pan_number -> 422
    missing_pan = client.post(
        "/internal/underwriting/evaluate",
        json={
            "loan_id": "44444444-4444-4444-4444-444444444444",
            "loan_amount_inr": 500000.0,
            "tenure_months": 36,
            "monthly_income_inr": 100000.0,
        },
    )
    assert missing_pan.status_code == 422

    # 4. Failing lock acquisition returns 500 and leaves cloudsql_lock_contention inactive
    def _failing_db() -> Any:
        raise RuntimeError("Postgres connection refused")

    fail_chaos = ChaosFaultManager(
        service_name="underwriting-los-svc",
        db_conn_factory=_failing_db,
        token_verifier=lambda tok: {
            "email": "godview-act-sa@general-ak.iam.gserviceaccount.com"
            if tok == "valid-act-token"
            else "intruder@evil.example.com",
            "email_verified": True,
        },
    )
    fail_app = create_app(chaos_manager=fail_chaos)
    fail_client = TestClient(fail_app)

    fail_resp = fail_client.post(
        "/internal/chaos/faults",
        headers={"Authorization": "Bearer valid-act-token"},
        json={"fault_type": "cloudsql_lock_contention", "duration_seconds": 30},
    )
    assert fail_resp.status_code == 500
    assert fail_resp.json()["error_code"] == "CHAOS_LOCK_ACQUISITION_FAILED"
    assert fail_chaos.is_active("cloudsql_lock_contention") is False
    assert fail_chaos.fault_status("cloudsql_lock_contention") == "inactive"

    # Unauthorized SA token on /internal/chaos/faults -> 403
    unauth_resp = fail_client.post(
        "/internal/chaos/faults",
        headers={"Authorization": "Bearer bad-sa-token"},
        json={"fault_type": "gke_oom_503", "duration_seconds": 30},
    )
    assert unauth_resp.status_code == 403

    # Pending state while lock is being acquired
    gate = threading.Event()
    entered = threading.Event()

    class SlowLockConnection:
        def cursor(self) -> Any:
            class _Cur:
                def execute(self, sql: str) -> None:
                    if "LOCK TABLE" in sql:
                        entered.set()
                        gate.wait(timeout=2.0)

                def __enter__(self) -> Any:
                    return self

                def __exit__(self, *args: Any) -> None:
                    pass

            return _Cur()

        def rollback(self) -> None:
            pass

        def close(self) -> None:
            pass

    pending_chaos = ChaosFaultManager(
        service_name="underwriting-los-svc",
        db_conn_factory=SlowLockConnection,
    )
    bg_thread = threading.Thread(
        target=lambda: pending_chaos.activate("cloudsql_lock_contention", duration_seconds=5)
    )
    bg_thread.start()
    entered.wait(timeout=1.0)
    assert pending_chaos.fault_status("cloudsql_lock_contention") == "pending"
    assert pending_chaos.is_active("cloudsql_lock_contention") is False
    gate.set()
    bg_thread.join(timeout=2.0)
    assert pending_chaos.fault_status("cloudsql_lock_contention") == "active"
    assert pending_chaos.is_active("cloudsql_lock_contention") is True
    pending_chaos.deactivate("cloudsql_lock_contention")

    # OpenAPI x-godview-admin-action matches CAPABILITY_REGISTRY
    schema = fail_client.get("/openapi.json").json()
    post_chaos_op = schema["paths"]["/internal/chaos/faults"]["post"]
    del_chaos_op = schema["paths"]["/internal/chaos/faults/{fault_type}"]["delete"]
    assert post_chaos_op["x-godview-admin-action"] == "chaos.activate"
    assert del_chaos_op["x-godview-admin-action"] == "chaos.clear"
