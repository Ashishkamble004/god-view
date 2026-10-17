"""Unit tests for Utility 5: core-banking-cibil-vm (TDD §2.1, §2.2, §3.2, §4 UC-07, UC-10, UC-16, §6.2 #8)."""

from __future__ import annotations

import re
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from cibil_cbs.main import create_app  # noqa: E402
from cibil_cbs.models import DisbursementStatus  # noqa: E402
from cibil_cbs.repository import (  # noqa: E402
    InMemoryCbsRepository,
    RepositoryConfigurationError,
    create_repository_from_env,
)


@pytest.fixture()
def repo() -> InMemoryCbsRepository:
    return InMemoryCbsRepository()


@pytest.fixture()
def client(repo: InMemoryCbsRepository) -> TestClient:
    app = create_app(repository=repo)
    return TestClient(app)


def test_deterministic_persona_seeded_cibil_score_range_and_stability(
    client: TestClient,
) -> None:
    resp_a1 = client.post(
        "/internal/cibil-cbs/score",
        json={
            "pan_number": "ABCDE1234F",
            "persona_seed": "salaried-prime-mumbai",
            "customer_id": str(uuid4()),
            "loan_id": str(uuid4()),
        },
    )
    assert resp_a1.status_code == 200
    body_a1 = resp_a1.json()
    score_a1 = body_a1["cibil_score"]
    assert 300 <= score_a1 <= 900
    assert score_a1 != 900
    assert re.match(r"^CIBIL-2026-\d{6}$", body_a1["cibil_bureau_ref"])
    assert body_a1["gce_vm_name"]

    # Deterministic stability for identical (PAN, persona_seed)
    resp_a2 = client.post(
        "/internal/cibil-cbs/score",
        json={
            "pan_number": "abcde1234f",
            "persona_seed": "salaried-prime-mumbai",
        },
    )
    assert resp_a2.status_code == 200
    assert resp_a2.json()["cibil_score"] == score_a1

    # Different PAN or persona_seed changes the score deterministically
    scores = set()
    for idx in range(10):
        r = client.post(
            "/internal/cibil-cbs/score",
            json={
                "pan_number": f"ABCDE123{idx}F",
                "persona_seed": f"persona-{idx}",
            },
        )
        assert r.status_code == 200
        s = r.json()["cibil_score"]
        assert 300 <= s <= 900
        assert s != 900
        scores.add(s)
    assert len(scores) >= 5


def test_cibil_stub_900_chaos_hook_both_directions(
    client: TestClient,
    repo: InMemoryCbsRepository,
) -> None:
    # Direction 1: chaos hook inactive -> score is NOT 900
    base_resp = client.post(
        "/internal/cibil-cbs/score",
        json={"pan_number": "PQRSX9876Z", "persona_seed": "retail-delhi"},
    )
    assert base_resp.status_code == 200
    base_score = base_resp.json()["cibil_score"]
    assert 300 <= base_score < 900

    # Unauthenticated HTTP chaos write endpoints on :8085 are closed (TDD §3.3 / #100 item 4)
    assert client.post("/internal/chaos/faults", json={"fault_type": "cibil_stub_900"}).status_code == 404
    assert client.post("/admin/v1/chaos/faults", json={"fault_type": "cibil_stub_900"}).status_code == 404

    # Activate cibil_stub_900 via chaos_fault_states table in repository (UC-07)
    repo.set_chaos_fault(
        "cibil_stub_900",
        active=True,
        fault_id="drill-cibil-stub-900",
        fault_class="BUSINESS",
    )

    # Direction 2: chaos hook active -> every PAN + persona seed returns 900
    for pan, seed in [
        ("PQRSX9876Z", "retail-delhi"),
        ("LMNOP5432Q", "fleet-chennai"),
        ("ZZZZZ0000A", "low-income-tier"),
    ]:
        stub_resp = client.post(
            "/internal/cibil-cbs/score",
            json={"pan_number": pan, "persona_seed": seed},
        )
        assert stub_resp.status_code == 200
        assert stub_resp.json()["cibil_score"] == 900

    # Deactivate chaos hook -> restores deterministic persona-seeded score
    repo.set_chaos_fault("cibil_stub_900", active=False)

    restored_resp = client.post(
        "/internal/cibil-cbs/score",
        json={"pan_number": "PQRSX9876Z", "persona_seed": "retail-delhi"},
    )
    assert restored_resp.status_code == 200
    assert restored_resp.json()["cibil_score"] == base_score


def test_disburse_pending_to_disbursed_and_identifiers(
    client: TestClient,
    repo: InMemoryCbsRepository,
) -> None:
    loan_id = str(uuid4())
    customer_id = str(uuid4())

    resp = client.post(
        "/internal/cibil-cbs/disburse",
        json={
            "loan_id": loan_id,
            "customer_id": customer_id,
            "pan_number": "ABCDE1234F",
            "persona_seed": "borrower-01",
            "disbursed_amount_inr": "1450000.00",
            "dealer_account_no": "CYMBAL-DLR-MUM-01",
        },
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["loan_id"] == loan_id
    assert data["status"] == DisbursementStatus.DISBURSED
    assert data["attempts"] == 1
    assert data["replay_of"] is None
    assert data["idempotent_replay"] is False
    assert Decimal(str(data["disbursed_amount_inr"])) == Decimal("1450000.00")
    assert re.match(r"^SANC-2026-\d{6}$", data["sanction_letter_no"])
    assert re.match(r"^NACH-2026-\d{6}$", data["nach_mandate_id"])
    assert re.match(r"^CBS-UTR-2026-\d{6}$", data["cbs_utr_number"])
    assert data["disbursed_at"] is not None

    # Verify PENDING -> DISBURSED state progression in repository
    assert repo.status_history[loan_id] == [
        DisbursementStatus.PENDING,
        DisbursementStatus.DISBURSED,
    ]


def test_disburse_idempotent_replay_both_directions(
    client: TestClient,
    repo: InMemoryCbsRepository,
) -> None:
    loan_id_1 = str(uuid4())
    loan_id_2 = str(uuid4())

    first = client.post(
        "/internal/cibil-cbs/disburse",
        json={
            "loan_id": loan_id_1,
            "customer_id": str(uuid4()),
            "disbursed_amount_inr": "880000.00",
        },
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["idempotent_replay"] is False

    # Same loan_id replayed -> idempotent return without duplicate row or new UTR
    second = client.post(
        "/internal/cibil-cbs/disburse",
        json={
            "loan_id": loan_id_1,
            "customer_id": str(uuid4()),
            "disbursed_amount_inr": "880000.00",
        },
    )
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["idempotent_replay"] is True
    assert second_body["disbursement_id"] == first_body["disbursement_id"]
    assert second_body["cbs_utr_number"] == first_body["cbs_utr_number"]
    assert second_body["sanction_letter_no"] == first_body["sanction_letter_no"]
    assert second_body["nach_mandate_id"] == first_body["nach_mandate_id"]
    assert second_body["attempts"] == 1
    assert len(repo.list_disbursements()) == 1

    # Other direction: distinct loan_id creates a new disbursement row and new UTR
    other = client.post(
        "/internal/cibil-cbs/disburse",
        json={
            "loan_id": loan_id_2,
            "customer_id": str(uuid4()),
            "disbursed_amount_inr": "920000.00",
        },
    )
    assert other.status_code == 200
    other_body = other.json()
    assert other_body["idempotent_replay"] is False
    assert other_body["disbursement_id"] != first_body["disbursement_id"]
    assert other_body["cbs_utr_number"] != first_body["cbs_utr_number"]
    assert len(repo.list_disbursements()) == 2


def test_gce_vm_latency_504_and_saga_replay_recovery(
    client: TestClient,
    repo: InMemoryCbsRepository,
) -> None:
    loan_id = str(uuid4())
    customer_id = str(uuid4())

    # Activate gce_vm_latency_504 drill (UC-16)
    repo.set_chaos_fault("gce_vm_latency_504", active=True)

    with patch("cibil_cbs.service.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        # Attempt 1 during fault -> 6s latency + 504 leaving PENDING row
        resp_504_1 = client.post(
            "/internal/cibil-cbs/disburse",
            json={
                "loan_id": loan_id,
                "customer_id": customer_id,
                "disbursed_amount_inr": "1200000.00",
            },
        )
        assert resp_504_1.status_code == 504
        mock_sleep.assert_awaited_once_with(6.0)

        stuck_row_1 = repo.get_by_loan_id(loan_id)
        assert stuck_row_1 is not None
        assert stuck_row_1.status == DisbursementStatus.PENDING
        assert stuck_row_1.cbs_utr_number is None
        assert stuck_row_1.attempts == 1

        # Attempt 2 while fault still active -> 6s latency + 504 leaving FAILED row
        resp_504_2 = client.post(
            "/internal/cibil-cbs/disburse",
            json={
                "loan_id": loan_id,
                "customer_id": customer_id,
                "disbursed_amount_inr": "1200000.00",
            },
        )
        assert resp_504_2.status_code == 504
        stuck_row_2 = repo.get_by_loan_id(loan_id)
        assert stuck_row_2 is not None
        assert stuck_row_2.status == DisbursementStatus.FAILED
        assert stuck_row_2.attempts == 2

    # Clear fault and replay (UC-10)
    repo.set_chaos_fault("gce_vm_latency_504", active=False)

    replay_resp = client.post(
        "/internal/cibil-cbs/disburse",
        json={
            "loan_id": loan_id,
            "customer_id": customer_id,
            "disbursed_amount_inr": "1200000.00",
        },
    )
    assert replay_resp.status_code == 200
    replayed = replay_resp.json()
    assert replayed["status"] == DisbursementStatus.DISBURSED
    assert replayed["attempts"] == 3
    assert replayed["replay_of"] == str(stuck_row_1.disbursement_id)
    assert re.match(r"^CBS-UTR-2026-\d{6}$", replayed["cbs_utr_number"])
    assert replayed["disbursed_at"] is not None


def test_score_and_disburse_combined_endpoint(
    client: TestClient,
    repo: InMemoryCbsRepository,
) -> None:
    loan_id = str(uuid4())
    resp = client.post(
        "/internal/cibil-cbs/score-and-disburse",
        json={
            "loan_id": loan_id,
            "customer_id": str(uuid4()),
            "pan_number": "KLMNO4321P",
            "persona_seed": "ev-buyer-pune",
            "disbursed_amount_inr": "1750000.00",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert 300 <= body["cibil_score"] < 900
    assert body["status"] == DisbursementStatus.DISBURSED
    assert re.match(r"^CBS-UTR-2026-\d{6}$", body["cbs_utr_number"])


def test_otel_business_span_attributes_and_w3c_baggage_propagation(
    client: TestClient,
) -> None:
    loan_id = str(uuid4())
    customer_id = str(uuid4())
    journey_id = "jrn-test-cbs-001"
    traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    baggage = f"journey.id={journey_id},customer.id={customer_id},synthetic=true"

    resp = client.post(
        "/internal/cibil-cbs/disburse",
        json={
            "loan_id": loan_id,
            "customer_id": customer_id,
            "pan_number": "ABCDE1234F",
            "persona_seed": "synthetic-01",
            "disbursed_amount_inr": "650000.00",
        },
        headers={
            "traceparent": traceparent,
            "baggage": baggage,
            "X-Synthetic": "true",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("traceparent") == traceparent
    assert "journey.id=jrn-test-cbs-001" in resp.headers.get("baggage", "")

    spans = client.app.state.recorded_spans
    assert len(spans) >= 1
    attrs = spans[-1]["attributes"]
    assert attrs["journey.id"] == journey_id
    assert attrs["customer.id"] == customer_id
    assert attrs["loan.id"] == loan_id
    assert attrs["event_type"] == "disbursement.settled"
    assert attrs["amount_inr"] == pytest.approx(650000.0)
    assert 300 <= attrs["cibil_score"] <= 900
    assert attrs["status_from"] == "PENDING"
    assert attrs["status_to"] == "DISBURSED"
    assert attrs["entity"] == "disbursement"
    assert attrs["synthetic"] is True


def test_metrics_healthz_and_systemd_payload_artifacts(
    client: TestClient,
) -> None:
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["service"] == "core-banking-cibil-vm"

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "godview_http_requests_total" in metrics.text

    unit_file = SERVICE_ROOT / "systemd" / "cibil-cbs.service"
    assert unit_file.is_file()
    unit_text = unit_file.read_text()
    assert "Restart=always" in unit_text
    assert "EnvironmentFile" in unit_text
    assert "8085" in unit_text

    install_script = SERVICE_ROOT / "install.sh"
    assert install_script.is_file()
    install_text = install_script.read_text()
    assert "python3 -m venv" in install_text
    assert "cibil-cbs.service" in install_text


def test_request_level_fault_toggles_honoured_only_in_hermetic(
    client: TestClient,
    repo: InMemoryCbsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Direction 1: GODVIEW_ENV=hermetic honours request-level cibil_stub_900 & target_status
    monkeypatch.setenv("GODVIEW_ENV", "hermetic")
    herm_score = client.post(
        "/internal/cibil-cbs/score",
        json={"pan_number": "ABCDE1234F", "persona_seed": "seed-1", "cibil_stub_900": True},
    )
    assert herm_score.status_code == 200
    assert herm_score.json()["cibil_score"] == 900

    # Direction 2: GODVIEW_ENV=gcp ignores caller-supplied fault toggles & target_status
    monkeypatch.setenv("GODVIEW_ENV", "gcp")
    prod_inject = client.post(
        "/internal/cibil-cbs/score",
        json={"pan_number": "ABCDE1234F", "persona_seed": "seed-1", "cibil_stub_900": True},
        headers={"x-chaos-fault": "cibil_stub_900"},
    )
    assert prod_inject.status_code == 200
    assert prod_inject.json()["cibil_score"] < 900

    # When drill is active in DB under GODVIEW_ENV=gcp, caller cibil_stub_900=False cannot override it
    repo.set_chaos_fault("cibil_stub_900", active=True)
    prod_override = client.post(
        "/internal/cibil-cbs/score",
        json={"pan_number": "ABCDE1234F", "persona_seed": "seed-1", "cibil_stub_900": False},
    )
    assert prod_override.status_code == 200
    assert prod_override.json()["cibil_score"] == 900
    repo.set_chaos_fault("cibil_stub_900", active=False)

    # Caller target_status="PENDING" is ignored when GODVIEW_ENV=gcp
    prod_disb = client.post(
        "/internal/cibil-cbs/disburse",
        json={
            "loan_id": str(uuid4()),
            "customer_id": str(uuid4()),
            "disbursed_amount_inr": "500000.00",
            "target_status": "PENDING",
            "gce_vm_latency_504": True,
        },
        headers={"x-chaos-fault": "gce_vm_latency_504"},
    )
    assert prod_disb.status_code == 200
    assert prod_disb.json()["status"] == DisbursementStatus.DISBURSED


def test_production_mode_requires_database_url_fail_loudly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from cibil_cbs.env_sync import resolve_database_url, sync_vm_env_file

    # Unset GODVIEW_ENV must raise (never default to hermetic fake)
    monkeypatch.delenv("GODVIEW_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_HOST", raising=False)
    with pytest.raises(RepositoryConfigurationError):
        create_repository_from_env()
    with pytest.raises(RepositoryConfigurationError):
        create_app()

    # Non-hermetic GODVIEW_ENV without DB config must raise
    monkeypatch.setenv("GODVIEW_ENV", "gcp")
    with patch("cibil_cbs.env_sync.fetch_metadata", return_value=None):
        with pytest.raises(RepositoryConfigurationError):
            create_repository_from_env()

    # DB_HOST + DB_PASSWORD contract resolves DSN cleanly
    monkeypatch.setenv("DB_HOST", "10.100.0.3")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_NAME", "godview_lms")
    monkeypatch.setenv("DB_USER", "app_user")
    monkeypatch.setenv("DB_PASSWORD", "supersecret123")
    assert (
        resolve_database_url(check_vm_metadata=False)
        == "postgresql://app_user:supersecret123@10.100.0.3:5432/godview_lms"
    )

    # DB_PASSWORD_SECRET_ID via Secret Manager + VM env file sync (0640)
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    monkeypatch.delenv("DB_HOST", raising=False)
    env_file = tmp_path / "cibil-cbs"
    with (
        patch(
            "cibil_cbs.env_sync.fetch_metadata",
            side_effect=lambda p, **_: "10.100.0.3" if "DB_HOST" in p else "general-ak",
        ),
        patch("cibil_cbs.env_sync.fetch_secret_from_gcp", return_value="sm-secret-pw"),
    ):
        synced = sync_vm_env_file(str(env_file))
        assert synced["DB_HOST"] == "10.100.0.3"
        assert synced["DB_PASSWORD"] == "sm-secret-pw"
        assert (env_file.stat().st_mode & 0o777) == 0o640
