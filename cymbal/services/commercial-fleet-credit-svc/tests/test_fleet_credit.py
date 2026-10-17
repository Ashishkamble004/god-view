"""Unit tests for Utility 3: commercial-fleet-credit-svc (TDD §2.1, §2.2, §3.2, §6.2 #8)."""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from fleet_credit.main import create_app  # noqa: E402
from fleet_credit.repository import (  # noqa: E402
    InMemoryFleetCreditRepository,
    RepositoryConfigurationError,
    create_repository_from_env,
    resolve_database_url,
)


@pytest.fixture()
def repo() -> InMemoryFleetCreditRepository:
    return InMemoryFleetCreditRepository()


@pytest.fixture()
def client(repo: InMemoryFleetCreditRepository) -> TestClient:
    app = create_app(repository=repo)
    return TestClient(app)


def test_fleet_credit_evaluate_commercial_fleet_approved(
    client: TestClient,
    repo: InMemoryFleetCreditRepository,
) -> None:
    loan_id = str(uuid4())
    customer_id = str(uuid4())

    resp = client.post(
        "/internal/fleet-credit/evaluate",
        json={
            "loan_id": loan_id,
            "customer_id": customer_id,
            "vehicle_category": "COMMERCIAL_FLEET",
            "fleet_size": 8,
            "requested_limit_inr": "3450000.00",
            "cibil_score": 760,
        },
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["loan_id"] == loan_id
    assert body["customer_id"] == customer_id
    assert body["vehicle_category"] == "COMMERCIAL_FLEET"
    assert body["fleet_size"] == 8
    assert Decimal(str(body["requested_limit_inr"])) == Decimal("3450000.00")
    assert Decimal(str(body["sanctioned_limit_inr"])) == Decimal("3450000.00")
    assert body["decision"] == "APPROVED"
    assert body["status"] == "SANCTIONED"
    assert body["event_type"] == "credit_line.decided"

    rows = repo.list_credit_lines()
    assert len(rows) == 1
    assert str(rows[0].loan_id) == loan_id
    assert rows[0].decision == "APPROVED"
    assert rows[0].sanctioned_limit_inr == Decimal("3450000.00")


def test_fleet_credit_evaluate_two_wheeler_and_rejection_both_directions(
    client: TestClient,
    repo: InMemoryFleetCreditRepository,
) -> None:
    # Direction 1: TWO_WHEELER approved when CIBIL >= 680 and fleet_size >= 1
    loan_ok = str(uuid4())
    resp_ok = client.post(
        "/internal/fleet-credit/evaluate",
        json={
            "loan_id": loan_ok,
            "customer_id": str(uuid4()),
            "vehicle_category": "TWO_WHEELER",
            "fleet_size": 2,
            "requested_limit_inr": "142000.00",
            "cibil_score": 710,
        },
    )
    assert resp_ok.status_code == 200
    assert resp_ok.json()["decision"] == "APPROVED"
    assert Decimal(str(resp_ok.json()["sanctioned_limit_inr"])) == Decimal("142000.00")

    # Direction 2: rejected when CIBIL < 680
    loan_rej = str(uuid4())
    resp_rej = client.post(
        "/internal/fleet-credit/evaluate",
        json={
            "loan_id": loan_rej,
            "customer_id": str(uuid4()),
            "vehicle_category": "COMMERCIAL_FLEET",
            "fleet_size": 4,
            "requested_limit_inr": "2180000.00",
            "cibil_score": 620,
        },
    )
    assert resp_rej.status_code == 200
    rej_body = resp_rej.json()
    assert rej_body["decision"] == "REJECTED"
    assert rej_body["status"] == "REJECTED"
    assert Decimal(str(rej_body["sanctioned_limit_inr"])) == Decimal("0.00")
    assert rej_body["event_type"] == "credit_line.decided"
    assert len(repo.list_credit_lines()) == 2


def test_fleet_credit_otel_span_attributes_and_w3c_baggage(
    client: TestClient,
    repo: InMemoryFleetCreditRepository,
) -> None:
    loan_id = str(uuid4())
    customer_id = str(uuid4())
    journey_id = "jrn-fleet-001"
    traceparent = "00-8bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    baggage = f"journey.id={journey_id},customer.id={customer_id},synthetic=true"

    resp = client.post(
        "/internal/fleet-credit/evaluate",
        json={
            "loan_id": loan_id,
            "customer_id": customer_id,
            "vehicle_category": "COMMERCIAL_FLEET",
            "fleet_size": 5,
            "requested_limit_inr": "2500000.00",
            "cibil_score": 745,
        },
        headers={
            "traceparent": traceparent,
            "baggage": baggage,
            "X-Synthetic": "true",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("traceparent") == traceparent
    assert "journey.id=jrn-fleet-001" in resp.headers.get("baggage", "")

    spans = client.app.state.recorded_spans
    assert len(spans) >= 1
    attrs = spans[-1]["attributes"]
    assert attrs["journey.id"] == journey_id
    assert attrs["customer.id"] == customer_id
    assert attrs["loan.id"] == loan_id
    assert attrs["event_type"] == "credit_line.decided"
    assert attrs["decision"] == "APPROVED"
    assert attrs["amount_inr"] == pytest.approx(2500000.0)
    assert attrs["entity"] == "credit_line"
    assert attrs["synthetic"] is True

    stored = repo.get_by_loan_id(loan_id)
    assert stored is not None
    assert stored.trace_id == "8bf92f3577b34da6a3ce929d0e0e4736"


def test_fleet_credit_metrics_healthz_and_fail_loudly(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["service"] == "commercial-fleet-credit-svc"

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
    monkeypatch.setenv("DB_PASSWORD", "fleetpass")
    assert (
        resolve_database_url()
        == "postgresql://app_user:fleetpass@10.100.0.3:5432/godview_lms"
    )
