"""Unit tests for Utility 4: motor-insurance-svc (TDD §2.1, §2.2, §3.2, §4 UC-05, §6.2 #8)."""

from __future__ import annotations

import re
import sys
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from motor_insurance.main import create_app  # noqa: E402
from motor_insurance.repository import (  # noqa: E402
    InMemoryInsuranceRepository,
    RepositoryConfigurationError,
    create_repository_from_env,
    resolve_database_url,
)


@pytest.fixture()
def repo() -> InMemoryInsuranceRepository:
    return InMemoryInsuranceRepository()


@pytest.fixture()
def client(repo: InMemoryInsuranceRepository) -> TestClient:
    app = create_app(repository=repo)
    return TestClient(app)


@pytest.mark.parametrize("allowed_status", ["APPROVED", "DISBURSED"])
def test_issue_policy_when_loan_approved_or_disbursed(
    client: TestClient,
    repo: InMemoryInsuranceRepository,
    allowed_status: str,
) -> None:
    loan_id = str(uuid4())
    customer_id = str(uuid4())
    repo.seed_loan_status(loan_id, allowed_status)

    resp = client.post(
        "/internal/insurance/issue",
        json={
            "loan_id": loan_id,
            "customer_id": customer_id,
            "loan_status": allowed_status,
            "policy_type": "ZERO_DEP_COMPREHENSIVE",
            "loan_amount_inr": "1620000.00",
        },
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["loan_id"] == loan_id
    assert body["customer_id"] == customer_id
    assert body["policy_type"] == "ZERO_DEP_COMPREHENSIVE"
    assert re.match(r"^FTAG-2026-\d{6}$", body["fasttag_id"])
    assert Decimal(str(body["coverage_inr"])) == Decimal("1620000.00")
    assert Decimal(str(body["premium_inr"])) > Decimal("0.00")
    assert body["status"] == "ACTIVE"
    assert body["event_type"] == "insurance.issued"

    stored = repo.get_by_loan_id(loan_id)
    assert stored is not None
    assert stored.fasttag_id == body["fasttag_id"]


@pytest.mark.parametrize(
    "blocked_status",
    ["KYC_PENDING", "KYC_VERIFIED", "MODEL_ARMOR_BLOCKED", "REJECTED"],
)
def test_issue_policy_guard_rejects_unapproved_loan_statuses(
    client: TestClient,
    repo: InMemoryInsuranceRepository,
    blocked_status: str,
) -> None:
    loan_id = str(uuid4())
    customer_id = str(uuid4())
    repo.seed_loan_status(loan_id, blocked_status)

    resp = client.post(
        "/internal/insurance/issue",
        json={
            "loan_id": loan_id,
            "customer_id": customer_id,
            "loan_status": blocked_status,
            "loan_amount_inr": "1290000.00",
        },
    )
    assert resp.status_code == 409
    assert "LOAN_NOT_APPROVED" in resp.json()["detail"]
    assert repo.get_by_loan_id(loan_id) is None
    assert len(repo.list_policies()) == 0


def test_chaos_hook_insurance_no_policy_write_both_directions(
    client: TestClient,
    repo: InMemoryInsuranceRepository,
) -> None:
    loan_id_drill = str(uuid4())
    customer_id = str(uuid4())
    repo.seed_loan_status(loan_id_drill, "APPROVED")

    # Unauthenticated HTTP chaos write endpoints are closed (TDD §3.3 / #100 item 4)
    assert (
        client.post(
            "/internal/chaos/faults",
            json={"fault_type": "insurance_no_policy_write"},
        ).status_code
        == 404
    )

    # Activate insurance_no_policy_write via chaos_fault_states in repository (UC-05 silent-failure drill)
    repo.set_chaos_fault(
        "insurance_no_policy_write",
        active=True,
        fault_id="drill-ins-no-write",
        fault_class="BUSINESS",
    )

    # Direction 1 (fault active): returns normal-looking ACTIVE HTTP 200 (with policy_id & fasttag_id),
    # writes NO policy row to insurance_policies, and emits NO business event on the span (#100 item 3)
    drill_resp = client.post(
        "/internal/insurance/issue",
        json={
            "loan_id": loan_id_drill,
            "customer_id": customer_id,
            "loan_status": "APPROVED",
            "loan_amount_inr": "1850000.00",
        },
    )
    assert drill_resp.status_code == 200
    drill_body = drill_resp.json()
    assert drill_body["status"] == "ACTIVE"
    assert drill_body["policy_id"]
    assert re.match(r"^FTAG-2026-\d{6}$", drill_body["fasttag_id"])
    assert repo.get_by_loan_id(loan_id_drill) is None
    assert len(repo.list_policies()) == 0

    last_span = client.app.state.recorded_spans[-1]["attributes"]
    assert "event_type" not in last_span

    # Deactivate chaos fault -> normal policy persistence resumes (Direction 2)
    repo.set_chaos_fault("insurance_no_policy_write", active=False)

    normal_resp = client.post(
        "/internal/insurance/issue",
        json={
            "loan_id": loan_id_drill,
            "customer_id": customer_id,
            "loan_status": "APPROVED",
            "loan_amount_inr": "1850000.00",
        },
    )
    assert normal_resp.status_code == 200
    assert normal_resp.json()["status"] == "ACTIVE"
    assert repo.get_by_loan_id(loan_id_drill) is not None
    assert len(repo.list_policies()) == 1
    assert client.app.state.recorded_spans[-1]["attributes"]["event_type"] == "insurance.issued"


def test_loan_not_found_and_hermetic_toggle_guards(
    client: TestClient,
    repo: InMemoryInsuranceRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. Missing loan when DB has 0 loans and loan_status is omitted -> 409 LOAN_NOT_FOUND (never defaults to APPROVED)
    missing_resp = client.post(
        "/internal/insurance/issue",
        json={
            "loan_id": str(uuid4()),
            "customer_id": str(uuid4()),
            "loan_amount_inr": "1100000.00",
        },
    )
    assert missing_resp.status_code == 409
    assert missing_resp.json()["code"] == "LOAN_NOT_FOUND"

    # 2. When repository has persisted loans, an unknown loan_id claiming loan_status="APPROVED" is rejected with 409 LOAN_NOT_FOUND
    seeded_loan = str(uuid4())
    repo.seed_loan_status(seeded_loan, "APPROVED")
    unknown_claiming_approved = client.post(
        "/internal/insurance/issue",
        json={
            "loan_id": str(uuid4()),
            "customer_id": str(uuid4()),
            "loan_status": "APPROVED",
            "loan_amount_inr": "1100000.00",
        },
    )
    assert unknown_claiming_approved.status_code == 409
    assert unknown_claiming_approved.json()["code"] == "LOAN_NOT_FOUND"

    # 3. Under GODVIEW_ENV=gcp, request-level insurance_no_policy_write is ignored (cannot inject or override)
    monkeypatch.setenv("GODVIEW_ENV", "gcp")
    inject_attempt = client.post(
        "/internal/insurance/issue",
        json={
            "loan_id": seeded_loan,
            "customer_id": str(uuid4()),
            "loan_status": "APPROVED",
            "loan_amount_inr": "1100000.00",
            "insurance_no_policy_write": True,
        },
        headers={"x-chaos-fault": "insurance_no_policy_write"},
    )
    assert inject_attempt.status_code == 200
    assert repo.get_by_loan_id(seeded_loan) is not None


def test_insurance_otel_span_attributes_and_w3c_baggage(
    client: TestClient,
    repo: InMemoryInsuranceRepository,
) -> None:
    loan_id = str(uuid4())
    customer_id = str(uuid4())
    journey_id = "jrn-ins-001"
    traceparent = "00-9bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    baggage = f"journey.id={journey_id},customer.id={customer_id},synthetic=true"
    repo.seed_loan_status(loan_id, "APPROVED")

    resp = client.post(
        "/internal/insurance/issue",
        json={
            "loan_id": loan_id,
            "customer_id": customer_id,
            "loan_status": "APPROVED",
            "loan_amount_inr": "2000000.00",
        },
        headers={
            "traceparent": traceparent,
            "baggage": baggage,
            "X-Synthetic": "true",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("traceparent") == traceparent
    assert "journey.id=jrn-ins-001" in resp.headers.get("baggage", "")

    spans = client.app.state.recorded_spans
    assert len(spans) >= 1
    attrs = spans[-1]["attributes"]
    assert attrs["journey.id"] == journey_id
    assert attrs["customer.id"] == customer_id
    assert attrs["loan.id"] == loan_id
    assert attrs["event_type"] == "insurance.issued"
    assert attrs["amount_inr"] == pytest.approx(float(resp.json()["premium_inr"]))
    assert attrs["status_from"] == "APPROVED"
    assert attrs["status_to"] == "INSURED"
    assert attrs["entity"] == "insurance_policy"
    assert attrs["synthetic"] is True


def test_insurance_metrics_healthz_and_fail_loudly(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["service"] == "motor-insurance-svc"

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "godview_http_requests_total" in metrics.text

    # Unset GODVIEW_ENV must raise (no default to hermetic fake)
    monkeypatch.delenv("GODVIEW_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_HOST", raising=False)
    with pytest.raises(RepositoryConfigurationError):
        create_repository_from_env()
    with pytest.raises(RepositoryConfigurationError):
        create_app()

    # Non-hermetic GODVIEW_ENV without DB config must raise
    monkeypatch.setenv("GODVIEW_ENV", "gcp")
    with pytest.raises(RepositoryConfigurationError):
        create_repository_from_env()

    # DB_HOST + DB_PASSWORD contract resolves DSN cleanly
    monkeypatch.setenv("DB_HOST", "10.100.0.3")
    monkeypatch.setenv("DB_PASSWORD", "inspass")
    assert (
        resolve_database_url()
        == "postgresql://app_user:inspass@10.100.0.3:5432/godview_lms"
    )
