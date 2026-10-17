"""Pydantic v2 request, response, and row contracts for Utility 1 (kyc-model-armor-svc)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class KycMode(StrEnum):
    IMAGE = "IMAGE"
    TEXT_ONLY = "TEXT_ONLY"


class KycVerifyRequest(BaseModel):
    """Inbound payload for POST /internal/kyc/verify.

    Accepts either flat fields or a nested `customer` object from loan-orchestrator-api.
    """

    model_config = ConfigDict(extra="allow")

    kyc_id: UUID | str | None = None
    customer_id: UUID | str
    loan_id: UUID | str | None = None
    journey_id: str | None = None
    kyc_mode: KycMode = KycMode.TEXT_ONLY
    pan_number: str = Field(min_length=10, max_length=10)
    aadhaar_number: str = Field(min_length=12, max_length=12)
    pan_gcs_uri: str | None = None
    aadhaar_gcs_uri: str | None = None
    pan_image_model: str | None = None
    aadhaar_image_model: str | None = None
    pan_image_bytes_b64: str | None = None
    aadhaar_image_bytes_b64: str | None = None
    loan_purpose_text: str = ""
    employment_remarks: str | None = None
    applicant_notes: str | None = None
    chaos_fault: str | None = None
    synthetic: bool = False

    @model_validator(mode="before")
    @classmethod
    def _unpack_nested_customer(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        customer = data.get("customer")
        if isinstance(customer, dict):
            merged = dict(data)
            for key in ("customer_id", "pan_number", "aadhaar_number"):
                if not merged.get(key) and customer.get(key):
                    merged[key] = customer[key]
            return merged
        return data


class KycVerifyResponse(BaseModel):
    """Response payload for POST /internal/kyc/verify matching TDD §2.1 kyc_documents + §3.2 event_type."""

    kyc_id: str
    customer_id: str
    loan_id: str | None = None
    journey_id: str | None = None
    kyc_mode: KycMode
    pan_gcs_uri: str | None = None
    aadhaar_gcs_uri: str | None = None
    pan_image_model: str | None = None
    aadhaar_image_model: str | None = None
    pan_verified: bool
    aadhaar_verified: bool
    model_armor_text_status: Literal["CLEAN", "MODEL_ARMOR_BLOCKED"]
    model_armor_findings_json: dict[str, Any]
    verified_at: datetime
    status: Literal["KYC_VERIFIED", "MODEL_ARMOR_BLOCKED"]
    event_type: Literal["kyc.verified", "armor.blocked"]
    blocked: bool
    masked_pan: str
    masked_aadhaar: str


class ChaosFaultActivateRequest(BaseModel):
    fault_id: str | None = None
    fault_type: Literal["gke_oom_503", "cloudsql_lock_contention"]
    duration_seconds: int = Field(default=60, ge=1, le=3600)
    parameters_json: dict[str, Any] = Field(default_factory=dict)
    triggered_by: str = "operator"
