"""Pydantic v2 models for Utility 3 (commercial-fleet-credit-svc, TDD §2.1 & §3.2)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CommercialCreditLineRow(BaseModel):
    credit_line_id: UUID | str
    customer_id: UUID | str
    loan_id: UUID | str
    vehicle_category: Literal["TWO_WHEELER", "COMMERCIAL_FLEET"]
    fleet_size: int = Field(ge=1)
    requested_limit_inr: Decimal
    sanctioned_limit_inr: Decimal
    utilisation_pct: Decimal
    decision: str
    status: str
    trace_id: str | None = None
    evaluated_at: datetime | None = None


class FleetCreditEvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    loan_id: UUID | str
    customer_id: UUID | str
    journey_id: str | None = None
    vehicle_category: Literal["TWO_WHEELER", "COMMERCIAL_FLEET"] = "COMMERCIAL_FLEET"
    fleet_size: int = Field(default=5, ge=1)
    requested_limit_inr: Decimal = Decimal("1500000.00")
    cibil_score: int = Field(default=750, ge=300, le=900)
    existing_exposure_inr: Decimal = Decimal("0.00")
    trace_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            updated = dict(data)
            if "requested_limit_inr" not in updated or updated["requested_limit_inr"] is None:
                for alias in ("loan_amount_inr", "amount_inr", "limit_inr"):
                    if updated.get(alias) is not None:
                        updated["requested_limit_inr"] = updated[alias]
                        break
            if updated.get("vehicle_category") not in ("TWO_WHEELER", "COMMERCIAL_FLEET"):
                updated["vehicle_category"] = "COMMERCIAL_FLEET"
            return updated
        return data


class FleetCreditEvaluateResponse(BaseModel):
    credit_line_id: UUID | str
    customer_id: UUID | str
    loan_id: UUID | str
    vehicle_category: Literal["TWO_WHEELER", "COMMERCIAL_FLEET"]
    fleet_size: int
    requested_limit_inr: Decimal
    sanctioned_limit_inr: Decimal
    utilisation_pct: Decimal
    decision: str
    status: str
    trace_id: str | None = None
    evaluated_at: datetime | None = None
    event_type: str = "credit_line.decided"
