"""Business logic for Utility 3: Commercial Fleet & Two-Wheeler Credit Line evaluation."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from fleet_credit.models import (
    CommercialCreditLineRow,
    FleetCreditEvaluateRequest,
    FleetCreditEvaluateResponse,
)
from fleet_credit.repository import FleetCreditRepositoryProtocol

MIN_CIBIL_FOR_CREDIT_LINE = 680


class FleetCreditService:
    """Evaluates commercial fleet top-ups and two-wheeler credit lines."""

    def __init__(self, repository: FleetCreditRepositoryProtocol) -> None:
        self._repo = repository

    def evaluate(
        self,
        req: FleetCreditEvaluateRequest,
        *,
        trace_id: str | None = None,
    ) -> FleetCreditEvaluateResponse:
        requested = Decimal(str(req.requested_limit_inr)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        eff_trace_id = trace_id or req.trace_id

        if req.cibil_score >= MIN_CIBIL_FOR_CREDIT_LINE and req.fleet_size >= 1 and requested > Decimal("0.00"):
            decision = "APPROVED"
            status = "SANCTIONED"
            sanctioned = requested
            if req.existing_exposure_inr > Decimal("0.00"):
                util = ((req.existing_exposure_inr / sanctioned) * Decimal("100.00")).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                utilisation_pct = min(util, Decimal("100.00"))
            else:
                utilisation_pct = Decimal("25.00") if req.vehicle_category == "COMMERCIAL_FLEET" else Decimal("15.00")
        else:
            decision = "REJECTED"
            status = "REJECTED"
            sanctioned = Decimal("0.00")
            utilisation_pct = Decimal("0.00")

        now = datetime.now(UTC)
        row = CommercialCreditLineRow(
            credit_line_id=str(uuid4()),
            customer_id=str(req.customer_id),
            loan_id=str(req.loan_id),
            vehicle_category=req.vehicle_category,
            fleet_size=req.fleet_size,
            requested_limit_inr=requested,
            sanctioned_limit_inr=sanctioned,
            utilisation_pct=utilisation_pct,
            decision=decision,
            status=status,
            trace_id=eff_trace_id,
            evaluated_at=now,
        )
        saved = self._repo.save_credit_line(row)
        return FleetCreditEvaluateResponse(
            credit_line_id=saved.credit_line_id,
            customer_id=saved.customer_id,
            loan_id=saved.loan_id,
            vehicle_category=saved.vehicle_category,
            fleet_size=saved.fleet_size,
            requested_limit_inr=saved.requested_limit_inr,
            sanctioned_limit_inr=saved.sanctioned_limit_inr,
            utilisation_pct=saved.utilisation_pct,
            decision=saved.decision,
            status=saved.status,
            trace_id=saved.trace_id,
            evaluated_at=saved.evaluated_at,
            event_type="credit_line.decided",
        )
