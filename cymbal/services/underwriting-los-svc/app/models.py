"""Pydantic request and response schemas for underwriting-los-svc."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class UnderwritingEvaluateRequest(BaseModel):
    """Inbound payload for POST /internal/underwriting/evaluate."""

    model_config = ConfigDict(extra="ignore")

    loan_id: str = Field(..., min_length=1)
    customer_id: str | None = None
    journey_id: str | None = None
    pan_number: str = Field(
        ..., min_length=10, max_length=10, pattern=r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$"
    )
    loan_amount_inr: float = Field(..., gt=0)
    down_payment_inr: float = Field(default=0.0, ge=0)
    ex_showroom_inr: float | None = Field(default=None, gt=0)
    tenure_months: int = Field(..., gt=0, le=120)
    interest_rate_pct: float = Field(default=9.25, ge=0)
    monthly_income_inr: float = Field(..., gt=0)
    existing_emi_inr: float = Field(default=0.0, ge=0)
    cibil_score: int | None = Field(default=None, ge=300, le=900)
    cibil_bureau_ref: str | None = None
    synthetic: bool = False
    chaos_fault: str | None = None


class UnderwritingEvaluateResponse(BaseModel):
    """Outbound verdict for POST /internal/underwriting/evaluate.

    Note: Business rejections (decision='REJECTED') ALWAYS return HTTP 200 (UC-07).
    """

    audit_id: str
    loan_id: str
    status: Literal["APPROVED", "REJECTED"]
    decision: Literal["APPROVED", "REJECTED"]
    emi_inr: float
    foir_pct: float
    ltv_pct: float
    cibil_score: int
    cibil_bureau_ref: str
    rules_version: str
    gke_pod_name: str
    gce_vm_name: str
    rejection_reason: str | None = None
    event_type: Literal["loan.decided"] = "loan.decided"
    evaluated_at: datetime


class ChaosFaultActivateRequest(BaseModel):
    """Payload to activate a chaos fault on underwriting-los-svc."""

    model_config = ConfigDict(extra="allow")

    fault_id: str | None = None
    fault_type: Literal["gke_oom_503", "cloudsql_lock_contention"]
    duration_seconds: int = Field(default=120, ge=1, le=3600)
    parameters_json: dict[str, Any] = Field(default_factory=dict)
    triggered_by: str = "operator"
