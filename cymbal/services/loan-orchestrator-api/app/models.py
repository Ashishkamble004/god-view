"""Pydantic v2 data and API contract models for loan-orchestrator-api (TDD §2.1, §3.1–§3.3)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

TENANT_BUSINESS_TABLES: tuple[str, ...] = (
    "vehicles",
    "customers",
    "kyc_documents",
    "loan_applications",
    "underwriting_audits",
    "commercial_credit_lines",
    "insurance_policies",
    "disbursements",
    "chaos_fault_states",
)

FORBIDDEN_TELEMETRY_TABLES: tuple[str, ...] = (
    "service_metric_rollups",
    "otel_trace_spans",
)

VehicleCategory = Literal["EV_SUV", "PASSENGER_SUV", "TWO_WHEELER", "COMMERCIAL_FLEET"]
KycMode = Literal["IMAGE", "TEXT_ONLY"]
UtilityType = Literal["RETAIL_AUTO_LOAN", "COMMERCIAL_FLEET_TOPUP"]
LoanStatus = Literal[
    "KYC_PENDING",
    "KYC_VERIFIED",
    "MODEL_ARMOR_BLOCKED",
    "APPROVED",
    "REJECTED",
    "INSURED",
    "DISBURSEMENT_PENDING",
    "DISBURSED",
]
UnderwritingDecision = Literal["APPROVED", "REJECTED"]
DisbursementStatus = Literal["PENDING", "DISBURSED", "FAILED", "REPLAYED"]
ChaosFaultClass = Literal["BUSINESS", "INFRA"]
ChaosFaultType = Literal[
    "foir_threshold_regression",
    "insurance_no_policy_write",
    "emi_rounding",
    "cibil_stub_900",
    "step_order_insurance_before_kyc",
    "cloudsql_lock_contention",
    "gke_oom_503",
    "gce_vm_latency_504",
]


def utc_now() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------------------
# 9 Tenant Business Table Row Models (TDD §2.1)
# ------------------------------------------------------------------------------


class VehicleRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    vehicle_id: str = Field(min_length=1, max_length=32)
    make: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=64)
    category: VehicleCategory
    ex_showroom_inr: Decimal
    cdn_image_url: str
    min_down_payment_pct: Decimal


class CustomerRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    customer_id: UUID = Field(default_factory=uuid4)
    full_name: str = Field(min_length=1, max_length=128)
    dob: date
    email: str = Field(min_length=3, max_length=128)
    phone: str = Field(min_length=6, max_length=20)
    city: str = Field(min_length=1, max_length=64)
    employment_type: str = Field(min_length=1, max_length=32)
    employer_name: str = Field(min_length=1, max_length=128)
    monthly_income_inr: Decimal
    pan_number: str = Field(min_length=10, max_length=10)
    aadhaar_number: str = Field(min_length=12, max_length=12)
    is_ai_generated: bool = False
    ai_persona_json: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=utc_now)


class KycDocumentRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kyc_id: UUID = Field(default_factory=uuid4)
    customer_id: UUID
    loan_id: UUID | None = None
    kyc_mode: KycMode
    pan_gcs_uri: str | None = None
    aadhaar_gcs_uri: str | None = None
    pan_image_model: str | None = None
    aadhaar_image_model: str | None = None
    pan_verified: bool = False
    aadhaar_verified: bool = False
    model_armor_text_status: str = Field(min_length=1, max_length=32)
    model_armor_findings_json: dict[str, Any] | None = None
    verified_at: datetime | None = None


class LoanApplicationRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    loan_id: UUID = Field(default_factory=uuid4)
    customer_id: UUID
    vehicle_id: str = Field(min_length=1, max_length=32)
    utility_type: UtilityType
    loan_amount_inr: Decimal
    down_payment_inr: Decimal
    tenure_months: int = Field(gt=0)
    interest_rate_pct: Decimal
    emi_inr: Decimal
    loan_purpose_text: str | None = None
    employment_remarks: str | None = None
    applicant_notes: str | None = None
    status: LoanStatus = "KYC_PENDING"
    journey_id: str = Field(min_length=1, max_length=64)
    trace_id: str | None = None
    total_latency_ms: int | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class UnderwritingAuditRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    audit_id: UUID = Field(default_factory=uuid4)
    loan_id: UUID
    cibil_score: int
    cibil_bureau_ref: str = Field(min_length=1, max_length=64)
    foir_pct: Decimal
    ltv_pct: Decimal
    rules_version: str = Field(min_length=1, max_length=32)
    gke_pod_name: str | None = None
    gce_vm_name: str | None = None
    decision: UnderwritingDecision
    rejection_reason: str | None = None
    evaluated_at: datetime = Field(default_factory=utc_now)


class CommercialCreditLineRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    credit_line_id: UUID = Field(default_factory=uuid4)
    customer_id: UUID
    loan_id: UUID
    vehicle_category: Literal["TWO_WHEELER", "COMMERCIAL_FLEET"]
    fleet_size: int = Field(gt=0)
    requested_limit_inr: Decimal
    sanctioned_limit_inr: Decimal
    utilisation_pct: Decimal
    decision: str
    status: str
    trace_id: str | None = None
    evaluated_at: datetime = Field(default_factory=utc_now)


class InsurancePolicyRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    policy_id: UUID = Field(default_factory=uuid4)
    customer_id: UUID
    loan_id: UUID
    policy_type: str = Field(min_length=1, max_length=64)
    fasttag_id: str = Field(min_length=1, max_length=64)
    premium_inr: Decimal
    coverage_inr: Decimal
    status: str = Field(min_length=1, max_length=32)
    trace_id: str | None = None
    issued_at: datetime = Field(default_factory=utc_now)


class DisbursementRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    disbursement_id: UUID = Field(default_factory=uuid4)
    loan_id: UUID
    sanction_letter_no: str = Field(min_length=1, max_length=64)
    cbs_utr_number: str | None = None
    nach_mandate_id: str = Field(min_length=1, max_length=64)
    disbursed_amount_inr: Decimal
    dealer_account_no: str = Field(min_length=1, max_length=64)
    status: DisbursementStatus
    attempts: int = Field(default=0, ge=0)
    replay_of: UUID | None = None
    disbursed_at: datetime | None = None


class ChaosFaultStateRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    fault_id: str = Field(min_length=1, max_length=64)
    fault_class: ChaosFaultClass
    fault_type: ChaosFaultType
    target_service: str = Field(min_length=1, max_length=64)
    is_active: bool = False
    parameters_json: dict[str, Any] | None = None
    triggered_by: str | None = None
    activated_at: datetime | None = None
    expires_at: datetime | None = None


# ------------------------------------------------------------------------------
# Public API Request & Response Models (TDD §3.1)
# ------------------------------------------------------------------------------


class CustomerApplyInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    customer_id: UUID | None = None
    full_name: str = Field(default="Aarav Deshmukh", min_length=1, max_length=128)
    dob: date = Field(default=date(1991, 5, 14))
    email: str = Field(default="applicant@cymbal-motors.example", min_length=3, max_length=128)
    phone: str = Field(default="+919820112233", min_length=6, max_length=20)
    city: str = Field(default="Pune", min_length=1, max_length=64)
    employment_type: str = Field(default="SALARIED", min_length=1, max_length=32)
    employer_name: str = Field(default="Apex Precision Systems", min_length=1, max_length=128)
    monthly_income_inr: Decimal = Field(default=Decimal("145000.00"), gt=Decimal("0"))
    pan_number: str = Field(default="ABCDE1234Z", min_length=10, max_length=10)
    aadhaar_number: str = Field(default="999911112222", min_length=12, max_length=12)
    ai_persona_json: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        if not out.get("full_name") and out.get("name"):
            out["full_name"] = out["name"]
        if not out.get("employment_type") and out.get("employment"):
            out["employment_type"] = out["employment"]
        if out.get("monthly_income_inr") is None and out.get("income") is not None:
            out["monthly_income_inr"] = out["income"]
        return out


class LoanApplyRequest(BaseModel):
    """Loan application request supporting both nested `customer` and flat applicant fields."""

    model_config = ConfigDict(extra="ignore")

    loan_id: UUID | None = None
    trace_id: str | None = None
    customer: CustomerApplyInput
    vehicle_id: str = Field(min_length=1, max_length=32)
    utility_type: UtilityType = "RETAIL_AUTO_LOAN"
    loan_amount_inr: Decimal = Field(gt=Decimal("0"))
    down_payment_inr: Decimal = Field(ge=Decimal("0"))
    tenure_months: int = Field(default=48, ge=6, le=84)
    interest_rate_pct: Decimal = Field(default=Decimal("9.25"), ge=Decimal("0"))
    existing_obligations_inr: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0"))
    kyc_mode: KycMode = "TEXT_ONLY"
    pan_gcs_uri: str | None = None
    aadhaar_gcs_uri: str | None = None
    pan_image_model: str | None = None
    aadhaar_image_model: str | None = None
    loan_purpose_text: str = "Personal vehicle finance"
    employment_remarks: str | None = None
    applicant_notes: str | None = None
    fleet_size: int | None = None
    requested_limit_inr: Decimal | None = None
    dealer_account_no: str = "CYMBAL-DLR-0001"
    journey_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_customer_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        merged = dict(data)
        nested = merged.get("customer")
        customer_keys = (
            "customer_id",
            "full_name",
            "name",
            "dob",
            "email",
            "phone",
            "city",
            "employment_type",
            "employment",
            "employer_name",
            "monthly_income_inr",
            "income",
            "pan_number",
            "aadhaar_number",
            "ai_persona_json",
        )
        if isinstance(nested, dict):
            cust_dict = dict(nested)
            for k in customer_keys:
                if k not in cust_dict and k in merged and merged[k] is not None:
                    cust_dict[k] = merged[k]
            merged["customer"] = cust_dict
        elif nested is None:
            cust_dict = {
                k: merged[k]
                for k in customer_keys
                if k in merged and merged[k] is not None
            }
            merged["customer"] = cust_dict
        return merged


class LoanJourneyDetailResponse(BaseModel):
    loan_id: str
    customer_id: str
    vehicle_id: str
    utility_type: UtilityType
    loan_amount_inr: float
    down_payment_inr: float
    tenure_months: int
    interest_rate_pct: float
    emi_inr: float
    loan_purpose_text: str | None = None
    employment_remarks: str | None = None
    applicant_notes: str | None = None
    status: LoanStatus
    journey_id: str
    trace_id: str
    total_latency_ms: int
    is_synthetic: bool
    created_at: str
    updated_at: str
    vehicle: dict[str, Any] | None = None
    customer: dict[str, Any] | None = None
    kyc_document: dict[str, Any] | None = None
    underwriting_audit: dict[str, Any] | None = None
    commercial_credit_line: dict[str, Any] | None = None
    insurance_policy: dict[str, Any] | None = None
    disbursement: dict[str, Any] | None = None


class LoanListResponse(BaseModel):
    items: list[LoanJourneyDetailResponse]
    loans: list[LoanJourneyDetailResponse]
    total: int
    limit: int
    offset: int = 0
    next_cursor: str | None = None


class KycSignedDocumentResponse(BaseModel):
    kyc_id: str
    customer_id: str
    loan_id: str | None = None
    doc_type: Literal["pan", "aadhaar"]
    kyc_mode: KycMode
    gcs_uri: str
    signed_url: str
    expires_at: str
    image_model: str
    watermark_text: str = "SPECIMEN · NOT A GOVERNMENT DOCUMENT"
    model_armor_scanned: bool = False


class TopologyNodeModel(BaseModel):
    id: str
    name: str
    kind: Literal["cloud_run", "gke_workload", "gce_vm", "cloud_sql", "api_gateway", "bucket"]
    utility_number: int | None = None
    utility_label: str
    status: Literal["healthy", "degraded", "critical"] = "healthy"
    p95_ms: float
    error_rate_pct: float
    requests_per_min: float
    active_faults: list[str] = Field(default_factory=list)


class TopologyEdgeModel(BaseModel):
    src: str
    dst: str
    protocol: Literal["HTTP/REST", "W3C-OTel-HTTP", "SQL/TCP", "GCS-SignedURL"]
    call_rate_per_min: float
    p95_ms: float
    error_rate_pct: float


class ObservabilityTopologyResponse(BaseModel):
    env_id: str
    journey_name: str = "vehicle_loan_origination"
    intended_sequence: str = "KYC < LOS < INS < CBS"
    nodes: list[TopologyNodeModel]
    edges: list[TopologyEdgeModel]
    updated_at: str


class TraceSpanViewModel(BaseModel):
    span_id: str
    parent_span_id: str | None = None
    service: str
    operation: str
    event_type: str | None = None
    started_at: str
    duration_ms: float
    http_status: int = 200
    status_from: str | None = None
    status_to: str | None = None
    decision: str | None = None
    foir_pct: float | None = None
    cibil_score: int | None = None
    emi_inr: float | None = None
    amount_inr: float | None = None
    gke_pod_name: str | None = None
    gce_vm_name: str | None = None
    rules_version: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class ObservabilityTraceResponse(BaseModel):
    trace_id: str
    journey_id: str
    loan_id: str
    customer_id: str
    is_synthetic: bool
    conformance: Literal["CONFORMANT", "SKIPPED", "REORDERED", "LOOPED", "STALLED"]
    observed_sequence: str
    total_duration_ms: float
    spans: list[TraceSpanViewModel]


class ApplicantMeResponse(BaseModel):
    principal: str
    email: str | None = None
    display_name: str
    authenticated: bool
    channel: str
    env_id: str


# ------------------------------------------------------------------------------
# Admin API Request & Response Models (TDD §3.3)
# ------------------------------------------------------------------------------


class TenantStateRequest(BaseModel):
    paused: bool = True
    reason: str | None = "Operator action via Sovereign control plane"


class TenantStateResponse(BaseModel):
    paused: bool
    reason: str | None = None
    updated_at: str


class ChaosFaultActivateRequest(BaseModel):
    fault_id: str = Field(min_length=1, max_length=64)
    fault_class: ChaosFaultClass = "BUSINESS"
    fault_type: ChaosFaultType
    target_service: str = "loan-orchestrator-api"
    parameters_json: dict[str, Any] | None = None
    triggered_by: str = "sovereign-chaos-controller"
    godview_managed: str = "true"
    ttl_seconds: int = Field(default=900, ge=10, le=86400)


class DisbursementReplayResponse(BaseModel):
    loan_id: str
    loan_status: LoanStatus
    idempotent_replay: bool
    disbursement: dict[str, Any]


class StuckDisbursementsResponse(BaseModel):
    status_filter: list[str]
    count: int
    disbursements: list[dict[str, Any]]


class LoanReevaluateResponse(BaseModel):
    loan_id: str
    previous_status: LoanStatus
    new_status: LoanStatus
    emi_inr: float
    underwriting_audit: dict[str, Any]


class JourneyCompensateResponse(BaseModel):
    journey_id: str
    loan_id: str
    previous_status: LoanStatus
    loan_status: LoanStatus
    insurance_cancelled: bool
    disbursement_reversed: bool
    compensated: bool = True
    compensated_at: str
