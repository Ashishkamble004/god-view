"""Business logic for Utility 4: Motor Insurance & FastTag Add-On Service."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from motor_insurance.models import (
    ALLOWED_INSURANCE_LOAN_STATUSES,
    ChaosFaultType,
    InsuranceIssueRequest,
    InsuranceIssueResponse,
    InsurancePolicyRow,
)
from motor_insurance.repository import (
    InsuranceRepositoryProtocol,
    LoanNotFoundInRepositoryError,
    is_hermetic_env,
)


class LoanNotApprovedForInsuranceError(Exception):
    """Raised when attempting to issue motor insurance for a loan not in {APPROVED, DISBURSED}."""

    def __init__(self, loan_id: str, observed_status: str) -> None:
        self.loan_id = loan_id
        self.observed_status = observed_status
        super().__init__(
            f"LOAN_NOT_APPROVED: Cannot issue insurance policy for loan_id={loan_id} with status={observed_status!r}; allowed statuses are APPROVED or DISBURSED"
        )


class LoanNotFoundForInsuranceError(Exception):
    """Raised when the referenced loan_id does not exist in loan_applications."""

    def __init__(self, loan_id: str) -> None:
        self.loan_id = loan_id
        super().__init__(
            f"LOAN_NOT_FOUND: Cannot issue insurance policy because loan_id={loan_id} was not found in loan_applications"
        )


def generate_fasttag_id(loan_id: str) -> str:
    digest = hashlib.sha256(f"FTAG:{loan_id}".encode()).digest()
    code = int.from_bytes(digest[:4], "big") % 900000 + 100000
    return f"FTAG-2026-{code:06d}"


class MotorInsuranceService:
    """Issues comprehensive motor insurance and FastTag linkage for APPROVED / DISBURSED loans."""

    def __init__(self, repository: InsuranceRepositoryProtocol) -> None:
        self._repo = repository

    def _resolve_and_validate_loan_status(self, req: InsuranceIssueRequest) -> str:
        loan_id_str = str(req.loan_id)
        repo_status = self._repo.get_loan_status(loan_id_str)
        req_status = req.loan_status.strip().upper() if req.loan_status else None

        if repo_status is None:
            if self._repo.requires_persisted_loan() or req_status is None:
                raise LoanNotFoundForInsuranceError(loan_id_str)

        if repo_status is not None and repo_status.strip().upper() not in ALLOWED_INSURANCE_LOAN_STATUSES:
            raise LoanNotApprovedForInsuranceError(loan_id_str, repo_status.strip().upper())

        if req_status is not None and req_status not in ALLOWED_INSURANCE_LOAN_STATUSES:
            raise LoanNotApprovedForInsuranceError(loan_id_str, req_status)

        effective = repo_status.strip().upper() if repo_status is not None else req_status
        if not effective or effective not in ALLOWED_INSURANCE_LOAN_STATUSES:
            raise LoanNotFoundForInsuranceError(loan_id_str)
        return effective

    def _is_no_policy_write_active(
        self,
        explicit_flag: bool | None,
        chaos_header: str | None = None,
    ) -> bool:
        if is_hermetic_env():
            if explicit_flag is not None:
                return explicit_flag
            if chaos_header and ChaosFaultType.INSURANCE_NO_POLICY_WRITE.value in chaos_header:
                return True
        return self._repo.is_fault_active(ChaosFaultType.INSURANCE_NO_POLICY_WRITE.value)

    def issue_policy(
        self,
        req: InsuranceIssueRequest,
        *,
        trace_id: str | None = None,
        chaos_header: str | None = None,
    ) -> tuple[InsuranceIssueResponse, str, bool]:
        """Issue motor insurance policy + FastTag ID.

        Returns `(InsuranceIssueResponse, resolved_loan_status, policy_persisted)`.
        When chaos hook `insurance_no_policy_write` is active (UC-05), returns a normal-looking
        `ACTIVE` HTTP 200 payload without writing to `insurance_policies` and with
        `policy_persisted=False` so no `insurance.issued` span event is emitted.
        """
        effective_loan_status = self._resolve_and_validate_loan_status(req)
        loan_id_str = str(req.loan_id)
        coverage = Decimal(str(req.loan_amount_inr)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        premium = ((coverage * Decimal(str(req.premium_rate_pct))) / Decimal("100.00")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        fasttag_id = generate_fasttag_id(loan_id_str)
        eff_trace_id = trace_id or req.trace_id
        now = datetime.now(UTC)

        no_write_active = self._is_no_policy_write_active(
            req.insurance_no_policy_write, chaos_header
        )
        if no_write_active:
            # UC-05 silent-failure drill: return normal-looking 200 ACTIVE response without persisting row
            return (
                InsuranceIssueResponse(
                    policy_id=str(uuid4()),
                    customer_id=str(req.customer_id),
                    loan_id=loan_id_str,
                    policy_type=req.policy_type,
                    fasttag_id=fasttag_id,
                    premium_inr=premium,
                    coverage_inr=coverage,
                    status="ACTIVE",
                    trace_id=eff_trace_id,
                    issued_at=now,
                    policy_written=True,
                    event_type="insurance.issued",
                ),
                effective_loan_status,
                False,
            )

        row = InsurancePolicyRow(
            policy_id=str(uuid4()),
            customer_id=str(req.customer_id),
            loan_id=loan_id_str,
            policy_type=req.policy_type,
            fasttag_id=fasttag_id,
            premium_inr=premium,
            coverage_inr=coverage,
            status="ACTIVE",
            trace_id=eff_trace_id,
            issued_at=now,
        )
        try:
            saved = self._repo.save_policy(row)
        except LoanNotFoundInRepositoryError as exc:
            raise LoanNotFoundForInsuranceError(loan_id_str) from exc

        return (
            InsuranceIssueResponse(
                policy_id=saved.policy_id,
                customer_id=saved.customer_id,
                loan_id=saved.loan_id,
                policy_type=saved.policy_type,
                fasttag_id=saved.fasttag_id,
                premium_inr=saved.premium_inr,
                coverage_inr=saved.coverage_inr,
                status=saved.status,
                trace_id=saved.trace_id,
                issued_at=saved.issued_at,
                policy_written=True,
                event_type="insurance.issued",
            ),
            effective_loan_status,
            True,
        )
