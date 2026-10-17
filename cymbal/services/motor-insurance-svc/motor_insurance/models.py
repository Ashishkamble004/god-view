"""Pydantic v2 models for Utility 4 (motor-insurance-svc, TDD §2.1 & §3.2)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

ALLOWED_INSURANCE_LOAN_STATUSES: frozenset[str] = frozenset({"APPROVED", "DISBURSED"})


class ChaosFaultClass(StrEnum):
    BUSINESS = "BUSINESS"
    INFRA = "INFRA"


class ChaosFaultType(StrEnum):
    FOIR_THRESHOLD_REGRESSION = "foir_threshold_regression"
    INSURANCE_NO_POLICY_WRITE = "insurance_no_policy_write"
    EMI_ROUNDING = "emi_rounding"
    CIBIL_STUB_900 = "cibil_stub_900"
    STEP_ORDER_INSURANCE_BEFORE_KYC = "step_order_insurance_before_kyc"
    CLOUDSQL_LOCK_CONTENTION = "cloudsql_lock_contention"
    GKE_OOM_503 = "gke_oom_503"
    GCE_VM_LATENCY_504 = "gce_vm_latency_504"


class InsurancePolicyRow(BaseModel):
    policy_id: UUID | str
    customer_id: UUID | str
    loan_id: UUID | str
    policy_type: str
    fasttag_id: str
    premium_inr: Decimal
    coverage_inr: Decimal
    status: str
    trace_id: str | None = None
    issued_at: datetime | None = None


class ChaosFaultStateRow(BaseModel):
    fault_id: str = Field(max_length=64)
    fault_class: ChaosFaultClass = ChaosFaultClass.BUSINESS
    fault_type: ChaosFaultType
    target_service: str = Field(default="motor-insurance-svc", max_length=64)
    is_active: bool = False
    parameters_json: dict[str, Any] = Field(default_factory=dict)
    triggered_by: str | None = None
    activated_at: datetime | None = None
    expires_at: datetime | None = None


class InsuranceIssueRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    loan_id: UUID | str
    customer_id: UUID | str
    journey_id: str | None = None
    loan_status: str | None = None
    policy_type: str = "ZERO_DEP_COMPREHENSIVE"
    loan_amount_inr: Decimal = Decimal("1000000.00")
    premium_rate_pct: Decimal = Decimal("2.50")
    insurance_no_policy_write: bool | None = None
    trace_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            updated = dict(data)
            if "loan_status" not in updated or updated["loan_status"] is None:
                if updated.get("status") is not None:
                    updated["loan_status"] = updated["status"]
            if "loan_amount_inr" not in updated or updated["loan_amount_inr"] is None:
                for alias in ("coverage_inr", "ex_showroom_inr", "amount_inr"):
                    if updated.get(alias) is not None:
                        updated["loan_amount_inr"] = updated[alias]
                        break
            return updated
        return data


class InsuranceIssueResponse(BaseModel):
    policy_id: UUID | str
    customer_id: UUID | str
    loan_id: UUID | str
    policy_type: str
    fasttag_id: str
    premium_inr: Decimal
    coverage_inr: Decimal
    status: str
    trace_id: str | None = None
    issued_at: datetime | None = None
    policy_written: bool = True
    event_type: str = "insurance.issued"


class ChaosFaultUpsertRequest(BaseModel):
    fault_id: str = "fault-insurance"
    fault_class: ChaosFaultClass = ChaosFaultClass.BUSINESS
    fault_type: ChaosFaultType = ChaosFaultType.INSURANCE_NO_POLICY_WRITE
    target_service: str = "motor-insurance-svc"
    is_active: bool = True
    parameters_json: dict[str, Any] = Field(default_factory=dict)
    triggered_by: str | None = "operator"
    expires_at: datetime | None = None
