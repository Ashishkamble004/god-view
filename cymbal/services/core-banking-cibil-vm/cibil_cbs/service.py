"""Business logic for Utility 5: CIBIL bureau scoring & Core Banking Settlement (CBS)."""

from __future__ import annotations

import asyncio
import hashlib
import os
import socket
from datetime import UTC, datetime
from uuid import uuid4

from cibil_cbs.models import (
    ChaosFaultType,
    DisbursementRow,
    DisbursementStatus,
    DisburseRequest,
    DisburseResponse,
    ScoreRequest,
    ScoreResponse,
)
from cibil_cbs.repository import CbsRepositoryProtocol, is_hermetic_env


class CbsGatewayTimeoutError(Exception):
    """Raised when chaos fault `gce_vm_latency_504` triggers a 6s delay + HTTP 504."""

    def __init__(self, row: DisbursementRow, message: str) -> None:
        super().__init__(message)
        self.row = row
        self.message = message


def _six_digit_code(namespace: str, value: str) -> str:
    digest = hashlib.sha256(f"{namespace}:{value}".encode()).digest()
    number = int.from_bytes(digest[:4], "big") % 900000 + 100000
    return f"{number:06d}"


def compute_cibil_score(
    pan_number: str,
    persona_seed: str | None = None,
    *,
    cibil_stub_900: bool = False,
) -> int:
    """Compute persona-seeded deterministic CIBIL score in [300, 900].

    When chaos hook `cibil_stub_900` is active (UC-07), returns 900 for every borrower.
    """
    if cibil_stub_900:
        return 900
    normalized_pan = (pan_number or "ABCDE1234F").strip().upper()
    normalized_seed = (persona_seed or "").strip()
    digest = hashlib.sha256(f"{normalized_pan}:{normalized_seed}".encode()).digest()
    # Maps deterministically into 300..899 so normal evaluations are in [300, 900]
    # and never accidentally collide with the 900 stub value.
    return 300 + (int.from_bytes(digest[:8], "big") % 600)


def generate_bureau_ref(pan_number: str, persona_seed: str | None = None) -> str:
    code = _six_digit_code("CIBIL", f"{pan_number.strip().upper()}:{persona_seed or ''}")
    return f"CIBIL-2026-{code}"


def generate_sanction_letter_no(loan_id: str) -> str:
    return f"SANC-2026-{_six_digit_code('SANC', str(loan_id))}"


def generate_nach_mandate_id(loan_id: str) -> str:
    return f"NACH-2026-{_six_digit_code('NACH', str(loan_id))}"


def generate_cbs_utr_number(loan_id: str) -> str:
    return f"CBS-UTR-2026-{_six_digit_code('UTR', str(loan_id))}"


def get_vm_name() -> str:
    return os.environ.get("GCE_VM_NAME") or os.environ.get("HOSTNAME") or socket.gethostname() or "core-banking-cibil-vm"


class CibilCbsService:
    """Domain service for CIBIL credit bureau scoring and idempotent CBS disbursement."""

    def __init__(self, repository: CbsRepositoryProtocol) -> None:
        self._repo = repository

    def _is_stub_900_active(
        self,
        explicit_flag: bool | None,
        chaos_header: str | None = None,
    ) -> bool:
        if is_hermetic_env():
            if explicit_flag is not None:
                return explicit_flag
            if chaos_header and ChaosFaultType.CIBIL_STUB_900.value in chaos_header:
                return True
        return self._repo.is_fault_active(ChaosFaultType.CIBIL_STUB_900.value)

    def _is_latency_504_active(
        self,
        explicit_flag: bool | None,
        chaos_header: str | None = None,
    ) -> tuple[bool, float, str | None]:
        if is_hermetic_env():
            if explicit_flag is True:
                return True, 6.0, None
            if explicit_flag is False:
                return False, 0.0, None
            if chaos_header and ChaosFaultType.GCE_VM_LATENCY_504.value in chaos_header:
                return True, 6.0, None
        fault = self._repo.get_fault(ChaosFaultType.GCE_VM_LATENCY_504.value)
        if fault is not None and fault.is_active:
            raw_sleep = (
                fault.parameters_json.get("sleep_seconds")
                if "sleep_seconds" in fault.parameters_json
                else fault.parameters_json.get("latency_seconds", 6.0)
            )
            sleep_s = float(raw_sleep)
            forced_status = fault.parameters_json.get("status")
            return True, sleep_s, forced_status
        return False, 0.0, None

    def evaluate_score(
        self,
        req: ScoreRequest,
        *,
        chaos_header: str | None = None,
    ) -> ScoreResponse:
        stub_active = self._is_stub_900_active(req.cibil_stub_900, chaos_header)
        score = compute_cibil_score(
            req.pan_number,
            req.persona_seed,
            cibil_stub_900=stub_active,
        )
        return ScoreResponse(
            cibil_score=score,
            cibil_bureau_ref=generate_bureau_ref(req.pan_number, req.persona_seed),
            gce_vm_name=get_vm_name(),
            pan_number=req.pan_number.strip().upper(),
        )

    async def disburse(
        self,
        req: DisburseRequest,
        *,
        chaos_header: str | None = None,
    ) -> tuple[DisburseResponse, str]:
        """Execute idempotent CBS disbursement for `req.loan_id`.

        Returns `(DisburseResponse, previous_status)`.
        Raises `CbsGatewayTimeoutError` after a 6 s delay when `gce_vm_latency_504` is active,
        leaving the row in `PENDING` (first attempt) or `FAILED` (subsequent attempts) for later replay.
        """
        loan_id_str = str(req.loan_id)
        stub_active = self._is_stub_900_active(req.cibil_stub_900, chaos_header)
        cibil_score = compute_cibil_score(
            req.pan_number,
            req.persona_seed,
            cibil_stub_900=stub_active,
        )
        bureau_ref = generate_bureau_ref(req.pan_number, req.persona_seed)
        vm_name = get_vm_name()

        existing = self._repo.get_by_loan_id(loan_id_str)
        if existing is not None and existing.status in (
            DisbursementStatus.DISBURSED,
            DisbursementStatus.REPLAYED,
        ):
            # Idempotent replay for an already settled loan_id
            return (
                DisburseResponse(
                    disbursement_id=existing.disbursement_id,
                    loan_id=existing.loan_id,
                    sanction_letter_no=existing.sanction_letter_no,
                    cbs_utr_number=existing.cbs_utr_number,
                    nach_mandate_id=existing.nach_mandate_id,
                    disbursed_amount_inr=existing.disbursed_amount_inr,
                    dealer_account_no=existing.dealer_account_no,
                    status=existing.status,
                    attempts=existing.attempts,
                    replay_of=existing.replay_of,
                    disbursed_at=existing.disbursed_at,
                    cibil_score=cibil_score,
                    cibil_bureau_ref=bureau_ref,
                    gce_vm_name=vm_name,
                    idempotent_replay=True,
                ),
                str(existing.status.value),
            )

        if existing is None:
            pending_row = DisbursementRow(
                disbursement_id=str(uuid4()),
                loan_id=loan_id_str,
                sanction_letter_no=generate_sanction_letter_no(loan_id_str),
                cbs_utr_number=None,
                nach_mandate_id=generate_nach_mandate_id(loan_id_str),
                disbursed_amount_inr=req.disbursed_amount_inr,
                dealer_account_no=req.dealer_account_no,
                status=DisbursementStatus.PENDING,
                attempts=1,
                replay_of=None,
                disbursed_at=None,
            )
            self._repo.save_disbursement(
                pending_row, update_loan_status="DISBURSEMENT_PENDING"
            )
            prev_status = DisbursementStatus.PENDING.value
        else:
            prev_status = existing.status.value
            pending_row = existing.model_copy(
                update={
                    "attempts": existing.attempts + 1,
                    "replay_of": existing.replay_of or existing.disbursement_id,
                }
            )

        latency_active, sleep_seconds, forced_status = self._is_latency_504_active(
            req.gce_vm_latency_504, chaos_header
        )
        if latency_active:
            await asyncio.sleep(sleep_seconds)
            if forced_status in ("PENDING", "FAILED"):
                stuck_status = DisbursementStatus(forced_status)
            elif pending_row.attempts >= 2:
                stuck_status = DisbursementStatus.FAILED
            else:
                stuck_status = DisbursementStatus.PENDING
            stuck_row = pending_row.model_copy(update={"status": stuck_status})
            self._repo.save_disbursement(
                stuck_row, update_loan_status="DISBURSEMENT_PENDING"
            )
            raise CbsGatewayTimeoutError(
                stuck_row,
                f"CBS_GATEWAY_TIMEOUT: core-banking-cibil-vm timed out after {sleep_seconds:.1f}s (status={stuck_status.value})",
            )

        final_status = (
            req.target_status
            if (is_hermetic_env() and req.target_status is not None)
            else DisbursementStatus.DISBURSED
        )
        settled_row = pending_row.model_copy(
            update={
                "cbs_utr_number": generate_cbs_utr_number(loan_id_str),
                "status": final_status,
                "disbursed_at": datetime.now(UTC),
            }
        )
        self._repo.save_disbursement(settled_row, update_loan_status="DISBURSED")

        return (
            DisburseResponse(
                disbursement_id=settled_row.disbursement_id,
                loan_id=settled_row.loan_id,
                sanction_letter_no=settled_row.sanction_letter_no,
                cbs_utr_number=settled_row.cbs_utr_number,
                nach_mandate_id=settled_row.nach_mandate_id,
                disbursed_amount_inr=settled_row.disbursed_amount_inr,
                dealer_account_no=settled_row.dealer_account_no,
                status=settled_row.status,
                attempts=settled_row.attempts,
                replay_of=settled_row.replay_of,
                disbursed_at=settled_row.disbursed_at,
                cibil_score=cibil_score,
                cibil_bureau_ref=bureau_ref,
                gce_vm_name=vm_name,
                idempotent_replay=False,
            ),
            prev_status,
        )
