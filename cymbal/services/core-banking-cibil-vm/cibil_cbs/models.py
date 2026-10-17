"""Pydantic v2 models for Utility 5 (core-banking-cibil-vm, TDD §2.1 & §3.2)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DisbursementStatus(StrEnum):
    PENDING = "PENDING"
    DISBURSED = "DISBURSED"
    FAILED = "FAILED"
    REPLAYED = "REPLAYED"


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


class DisbursementRow(BaseModel):
    disbursement_id: UUID | str
    loan_id: UUID | str
    sanction_letter_no: str
    cbs_utr_number: str | None = None
    nach_mandate_id: str
    disbursed_amount_inr: Decimal
    dealer_account_no: str
    status: DisbursementStatus
    attempts: int = 0
    replay_of: UUID | str | None = None
    disbursed_at: datetime | None = None


class ChaosFaultStateRow(BaseModel):
    fault_id: str = Field(max_length=64)
    fault_class: ChaosFaultClass = ChaosFaultClass.BUSINESS
    fault_type: ChaosFaultType
    target_service: str = Field(default="core-banking-cibil-vm", max_length=64)
    is_active: bool = False
    parameters_json: dict[str, Any] = Field(default_factory=dict)
    triggered_by: str | None = None
    activated_at: datetime | None = None
    expires_at: datetime | None = None


class ScoreRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    pan_number: str = Field(default="ABCDE1234F", min_length=5, max_length=32)
    persona_seed: str | None = None
    customer_id: UUID | str | None = None
    loan_id: UUID | str | None = None
    journey_id: str | None = None
    cibil_stub_900: bool | None = None
    gce_vm_latency_504: bool | None = None


class ScoreResponse(BaseModel):
    cibil_score: int = Field(ge=300, le=900)
    cibil_bureau_ref: str
    gce_vm_name: str
    pan_number: str
    event_type: str = "cibil.scored"


class DisburseRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    loan_id: UUID | str
    customer_id: UUID | str | None = None
    journey_id: str | None = None
    pan_number: str = "ABCDE1234F"
    persona_seed: str | None = None
    disbursed_amount_inr: Decimal = Decimal("500000.00")
    dealer_account_no: str = "CYMBAL-DLR-0001"
    disburse: bool = True
    is_replay: bool = False
    target_status: DisbursementStatus | None = None
    cibil_stub_900: bool | None = None
    gce_vm_latency_504: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_amount_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "disbursed_amount_inr" not in data or data["disbursed_amount_inr"] is None:
                for alias in ("loan_amount_inr", "amount_inr", "principal_inr"):
                    if data.get(alias) is not None:
                        data = dict(data)
                        data["disbursed_amount_inr"] = data[alias]
                        break
        return data


class DisburseResponse(BaseModel):
    disbursement_id: UUID | str
    loan_id: UUID | str
    sanction_letter_no: str
    cbs_utr_number: str | None = None
    nach_mandate_id: str
    disbursed_amount_inr: Decimal
    dealer_account_no: str
    status: DisbursementStatus
    attempts: int
    replay_of: UUID | str | None = None
    disbursed_at: datetime | None = None
    cibil_score: int = Field(ge=300, le=900)
    cibil_bureau_ref: str
    gce_vm_name: str
    idempotent_replay: bool = False
    event_type: str = "disbursement.settled"


class ChaosFaultUpsertRequest(BaseModel):
    fault_id: str = "fault-cbs"
    fault_class: ChaosFaultClass = ChaosFaultClass.BUSINESS
    fault_type: ChaosFaultType
    target_service: str = "core-banking-cibil-vm"
    is_active: bool = True
    parameters_json: dict[str, Any] = Field(default_factory=dict)
    triggered_by: str | None = "operator"
    expires_at: datetime | None = None
