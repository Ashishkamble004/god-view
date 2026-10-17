"""FastAPI application for Cymbal Auto Finance loan-orchestrator-api (TDD §3.1–§3.3, §6.2 #6)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any
from uuid import UUID, uuid4

import godview_shared.otel as _otel_mod
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from godview_shared.config import MODEL_TABLE
from godview_shared.otel import (
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
    extract_baggage_context,
    get_tracer,
    inject_baggage_headers,
    render_prometheus_metrics,
    set_business_span_attributes,
)
from opentelemetry import trace

from app.clients import (
    DownstreamServiceBundle,
    DownstreamServiceError,
    HttpDownstreamServiceBundle,
    MockDownstreamServiceBundle,
)
from app.config import OrchestratorSettings
from app.models import (
    ApplicantMeResponse,
    ChaosFaultActivateRequest,
    ChaosFaultStateRow,
    CommercialCreditLineRow,
    CustomerRow,
    DisbursementReplayResponse,
    DisbursementRow,
    InsurancePolicyRow,
    JourneyCompensateResponse,
    KycDocumentRow,
    KycSignedDocumentResponse,
    LoanApplicationRow,
    LoanApplyRequest,
    LoanJourneyDetailResponse,
    LoanListResponse,
    LoanReevaluateResponse,
    LoanStatus,
    ObservabilityTopologyResponse,
    ObservabilityTraceResponse,
    StuckDisbursementsResponse,
    TenantStateRequest,
    TenantStateResponse,
    TopologyEdgeModel,
    TopologyNodeModel,
    TraceSpanViewModel,
    UnderwritingAuditRow,
    VehicleRow,
    utc_now,
)
from app.repository import (
    InMemoryOrchestratorRepository,
    OrchestratorRepository,
    PostgresOrchestratorRepository,
)

logger = logging.getLogger(__name__)


def calculate_emi_inr(
    principal_inr: Decimal,
    annual_rate_pct: Decimal,
    tenure_months: int,
    *,
    emi_rounding_bug: bool = False,
) -> Decimal:
    """Calculate reducing-balance monthly EMI in INR, rounded to whole ₹ at the end only.

    When `emi_rounding_bug=True` (UC-06 `emi_rounding` chaos fault), truncates the monthly
    interest rate prematurely and applies an intermediate rounding distortion that breaches
    `inv-emi-matches-formula` by > ₹100.
    """
    if tenure_months <= 0:
        raise ValueError("tenure_months must be positive")
    principal = Decimal(str(principal_inr))
    rate_pct = Decimal(str(annual_rate_pct))
    n = Decimal(tenure_months)

    with localcontext() as ctx:
        ctx.prec = 28
        if rate_pct == Decimal("0"):
            raw_emi = (principal / n) + (Decimal("250") if emi_rounding_bug else Decimal("0"))
        elif emi_rounding_bug:
            # Chaos fault UC-06: premature 2-decimal truncation of monthly rate + ceiling bias
            buggy_monthly_rate = (rate_pct / Decimal("1200")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if buggy_monthly_rate <= Decimal("0"):
                buggy_monthly_rate = Decimal("0.01")
            factor = (Decimal("1") + buggy_monthly_rate) ** tenure_months
            raw_emi = (
                (principal * buggy_monthly_rate * factor) / (factor - Decimal("1"))
            ) + Decimal("250")
        else:
            monthly_rate = rate_pct / Decimal("1200")
            factor = (Decimal("1") + monthly_rate) ** tenure_months
            raw_emi = (principal * monthly_rate * factor) / (factor - Decimal("1"))

        whole_rupee = raw_emi.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return whole_rupee.quantize(Decimal("1.00"))


class OrchestratorRuntimeState:
    """Mutable runtime state for pause switch, SSE event broadcast, and dependencies."""

    def __init__(
        self,
        settings: OrchestratorSettings,
        repository: OrchestratorRepository,
        downstreams: DownstreamServiceBundle,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.downstreams = downstreams
        repo_paused, repo_reason = repository.get_pause_state()
        self._paused: bool = settings.tenant_paused if repo_paused is None else repo_paused
        self._pause_reason: str | None = (
            settings.tenant_pause_reason if repo_paused is None else repo_reason
        )
        self.pause_updated_at: datetime = utc_now()
        self.recent_events: deque[dict[str, Any]] = deque(maxlen=200)
        self.sse_Queues: set[asyncio.Queue[dict[str, Any]]] = set()

    @property
    def paused(self) -> bool:
        repo_paused, _ = self.repository.get_pause_state()
        return self._paused if repo_paused is None else repo_paused

    @paused.setter
    def paused(self, value: bool) -> None:
        self._paused = value
        self.repository.set_pause_state(value, self._pause_reason)

    @property
    def pause_reason(self) -> str | None:
        repo_paused, repo_reason = self.repository.get_pause_state()
        return self._pause_reason if repo_paused is None else repo_reason

    @pause_reason.setter
    def pause_reason(self, value: str | None) -> None:
        self._pause_reason = value
        self.repository.set_pause_state(self.paused, value)

    def publish_event(self, event: dict[str, Any]) -> None:
        self.recent_events.append(event)
        for q in list(self.sse_Queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass


def _build_traceparent(span: trace.Span, fallback: str | None = None) -> str:
    ctx = span.get_span_context()
    if ctx and ctx.trace_id and ctx.span_id:
        return f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-01"
    if fallback:
        return fallback
    return f"00-{uuid4().hex}-{uuid4().hex[:16]}-01"


def _format_trace_id(span: trace.Span, incoming_traceparent: str | None = None) -> str:
    if incoming_traceparent:
        parts = incoming_traceparent.split("-")
        if len(parts) >= 2 and len(parts[1]) == 32:
            return parts[1]
    ctx = span.get_span_context()
    if ctx and ctx.trace_id:
        return f"{ctx.trace_id:032x}"
    return uuid4().hex


def _format_span_id(span: trace.Span) -> str:
    ctx = span.get_span_context()
    if ctx and ctx.span_id:
        return f"{ctx.span_id:016x}"
    return uuid4().hex[:16]


async def _assemble_loan_detail(
    repo: OrchestratorRepository,
    loan: LoanApplicationRow,
) -> LoanJourneyDetailResponse:
    vehicle = await repo.get_vehicle(loan.vehicle_id)
    customer = await repo.get_customer(loan.customer_id)
    kyc_doc = await repo.get_kyc_document_by_loan(loan.loan_id)
    audit = await repo.get_underwriting_audit_by_loan(loan.loan_id)
    credit_line = await repo.get_commercial_credit_line_by_loan(loan.loan_id)
    policy = await repo.get_insurance_policy_by_loan(loan.loan_id)
    disb = await repo.get_disbursement_by_loan(loan.loan_id)

    is_synthetic = bool(customer.is_ai_generated) if customer else False
    return LoanJourneyDetailResponse(
        loan_id=str(loan.loan_id),
        customer_id=str(loan.customer_id),
        vehicle_id=loan.vehicle_id,
        utility_type=loan.utility_type,
        loan_amount_inr=float(loan.loan_amount_inr),
        down_payment_inr=float(loan.down_payment_inr),
        tenure_months=loan.tenure_months,
        interest_rate_pct=float(loan.interest_rate_pct),
        emi_inr=float(loan.emi_inr),
        loan_purpose_text=loan.loan_purpose_text,
        employment_remarks=loan.employment_remarks,
        applicant_notes=loan.applicant_notes,
        status=loan.status,
        journey_id=loan.journey_id,
        trace_id=loan.trace_id or uuid4().hex,
        total_latency_ms=loan.total_latency_ms or 0,
        is_synthetic=is_synthetic,
        created_at=loan.created_at.isoformat(),
        updated_at=loan.updated_at.isoformat(),
        vehicle=vehicle.model_dump(mode="json") if vehicle else None,
        customer=customer.model_dump(mode="json") if customer else None,
        kyc_document=kyc_doc.model_dump(mode="json") if kyc_doc else None,
        underwriting_audit=audit.model_dump(mode="json") if audit else None,
        commercial_credit_line=credit_line.model_dump(mode="json") if credit_line else None,
        insurance_policy=policy.model_dump(mode="json") if policy else None,
        disbursement=disb.model_dump(mode="json") if disb else None,
    )


def _build_public_router(state: OrchestratorRuntimeState) -> APIRouter:
    router = APIRouter(tags=["tenant-public"])

    def _check_not_paused() -> None:
        if state.paused:
            reason = state.pause_reason or "Tenant traffic paused by Sovereign control plane"
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "TENANT_PAUSED",
                    "detail": f"TENANT_PAUSED: {reason}",
                    "reason": reason,
                },
            )

    @router.get(
        "/vehicles",
        response_model=list[VehicleRow],
        summary="List the 8 Cymbal Auto Finance catalog vehicles",
        openapi_extra={"x-godview-probe-safe": True},
    )
    async def list_vehicles() -> list[VehicleRow]:
        _check_not_paused()
        vehicles = await state.repository.list_vehicles()
        if state.settings.cdn_base_url:
            base = state.settings.cdn_base_url.rstrip("/")
            return [
                v.model_copy(
                    update={
                        "cdn_image_url": (
                            f"{base}{v.cdn_image_url}"
                            if v.cdn_image_url.startswith("/")
                            else v.cdn_image_url
                        )
                    }
                )
                for v in vehicles
            ]
        return vehicles

    @router.get(
        "/me",
        response_model=ApplicantMeResponse,
        summary="Return current applicant or synthetic session identity",
        openapi_extra={"x-godview-probe-safe": True},
    )
    async def get_me(request: Request) -> ApplicantMeResponse:
        _check_not_paused()
        raw_email = request.headers.get("X-Goog-Authenticated-User-Email", "").strip()
        if raw_email:
            clean_email = raw_email.split(":", 1)[-1].strip()
            return ApplicantMeResponse(
                principal=clean_email,
                email=clean_email,
                display_name=clean_email.split("@", 1)[0],
                authenticated=True,
                channel="iap",
                env_id=state.settings.env_id,
            )
        return ApplicantMeResponse(
            principal="anonymous",
            email=None,
            display_name="Not signed in (no IAP header)",
            authenticated=False,
            channel="api-gateway",
            env_id=state.settings.env_id,
        )

    @router.post(
        "/loans/apply",
        response_model=LoanJourneyDetailResponse,
        summary="Execute the 5-utility vehicle loan origination journey",
    )
    async def apply_for_loan(
        payload: LoanApplyRequest,
        request: Request,
    ) -> LoanJourneyDetailResponse:
        _check_not_paused()
        started_monotonic = time.perf_counter()

        # 1. Extract incoming W3C traceparent + baggage
        extracted = extract_baggage_context(dict(request.headers))
        is_synthetic = bool(extracted.get("synthetic"))
        incoming_tp = extracted.get("traceparent")

        baggage_journey_id = extracted.get("journey.id")
        journey_id = payload.journey_id or baggage_journey_id or f"jrn-{uuid4().hex[:12]}"

        baggage_customer_id = extracted.get("customer.id")
        customer_id = payload.customer.customer_id
        if customer_id is None and baggage_customer_id:
            try:
                customer_id = UUID(str(baggage_customer_id))
            except ValueError:
                customer_id = uuid4()
        if customer_id is None:
            customer_id = uuid4()

        # 2. Check active chaos faults in repository
        active_faults = await state.repository.list_active_chaos_faults()
        active_fault_types = {f.fault_type for f in active_faults if f.is_active}
        emi_rounding_active = "emi_rounding" in active_fault_types
        insurance_no_policy_write_active = "insurance_no_policy_write" in active_fault_types
        step_order_ins_before_kyc = "step_order_insurance_before_kyc" in active_fault_types

        # 3. Validate vehicle exists
        vehicle = await state.repository.get_vehicle(payload.vehicle_id)
        if vehicle is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown vehicle_id: {payload.vehicle_id}"
            )

        # 4. Calculate exact (or chaos-rounded) reducing-balance EMI
        emi_inr = calculate_emi_inr(
            payload.loan_amount_inr,
            payload.interest_rate_pct,
            payload.tenure_months,
            emi_rounding_bug=emi_rounding_active,
        )

        # 5. Upsert Customer row (X-Synthetic: true -> customers.is_ai_generated = true)
        customer_row = CustomerRow(
            customer_id=customer_id,
            full_name=payload.customer.full_name,
            dob=payload.customer.dob,
            email=payload.customer.email,
            phone=payload.customer.phone,
            city=payload.customer.city,
            employment_type=payload.customer.employment_type,
            employer_name=payload.customer.employer_name,
            monthly_income_inr=payload.customer.monthly_income_inr,
            pan_number=payload.customer.pan_number,
            aadhaar_number=payload.customer.aadhaar_number,
            is_ai_generated=is_synthetic,
            ai_persona_json=payload.customer.ai_persona_json,
        )
        await state.repository.upsert_customer(customer_row)

        tracer = get_tracer(state.settings.service_name)
        loan_id = payload.loan_id or uuid4()

        with tracer.start_as_current_span("journey.vehicle_loan_origination") as root_span:
            trace_id = payload.trace_id or _format_trace_id(root_span, incoming_tp)
            root_span_id = _format_span_id(root_span)

            loan_row = LoanApplicationRow(
                loan_id=loan_id,
                customer_id=customer_id,
                vehicle_id=payload.vehicle_id,
                utility_type=payload.utility_type,
                loan_amount_inr=payload.loan_amount_inr,
                down_payment_inr=payload.down_payment_inr,
                tenure_months=payload.tenure_months,
                interest_rate_pct=payload.interest_rate_pct,
                emi_inr=emi_inr,
                loan_purpose_text=payload.loan_purpose_text,
                employment_remarks=payload.employment_remarks,
                applicant_notes=payload.applicant_notes,
                status="KYC_PENDING",
                journey_id=journey_id,
                trace_id=trace_id,
            )
            await state.repository.create_loan_application(loan_row)

            current_status: LoanStatus = "KYC_PENDING"

            def _outbound_headers() -> dict[str, str]:
                active_span = trace.get_current_span()
                tp = _build_traceparent(active_span, incoming_tp)
                return inject_baggage_headers(
                    journey_id=journey_id,
                    customer_id=str(customer_id),
                    synthetic=is_synthetic,
                    traceparent=tp,
                )

            async def _run_insurance_step(status_before: LoanStatus) -> LoanStatus:
                step_start = time.perf_counter()
                with tracer.start_as_current_span("step.motor_insurance.issue") as span:
                    ins_payload = {
                        "loan_id": str(loan_id),
                        "customer_id": str(customer_id),
                        "loan_status": "APPROVED",
                        "vehicle_id": payload.vehicle_id,
                        "vehicle_category": vehicle.category,
                        "loan_amount_inr": float(payload.loan_amount_inr),
                        "coverage_inr": float(vehicle.ex_showroom_inr),
                        "journey_id": journey_id,
                        "trace_id": trace_id,
                        "insurance_no_policy_write": insurance_no_policy_write_active,
                    }
                    ins_res = await state.downstreams.issue_insurance(
                        ins_payload, _outbound_headers()
                    )
                    dur_ms = round((time.perf_counter() - step_start) * 1000.0, 2)
                    policy_written = bool(
                        ins_res.get("policy_written", not insurance_no_policy_write_active)
                    )
                    if policy_written and not insurance_no_policy_write_active:
                        policy_id_val = ins_res.get("policy_id")
                        policy_row = InsurancePolicyRow(
                            policy_id=UUID(str(policy_id_val)) if policy_id_val else uuid4(),
                            customer_id=customer_id,
                            loan_id=loan_id,
                            policy_type=str(
                                ins_res.get("policy_type", "COMPREHENSIVE_MOTOR_3Y")
                            ),
                            fasttag_id=str(
                                ins_res.get("fasttag_id", f"FT-CYM-{str(loan_id)[:8].upper()}")
                            ),
                            premium_inr=Decimal(
                                str(ins_res.get("premium_inr", payload.loan_amount_inr * Decimal("0.025")))
                            ),
                            coverage_inr=Decimal(
                                str(ins_res.get("coverage_inr", vehicle.ex_showroom_inr))
                            ),
                            status=str(ins_res.get("status", "ACTIVE")),
                            trace_id=trace_id,
                        )
                        await state.repository.save_insurance_policy(policy_row)
                        set_business_span_attributes(
                            span,
                            journey_id=journey_id,
                            customer_id=str(customer_id),
                            loan_id=str(loan_id),
                            entity="insurance_policy",
                            entity_id=str(policy_row.policy_id),
                            status_from=status_before,
                            status_to="INSURED",
                            event_type="insurance.issued",
                            decision="ISSUED",
                            amount_inr=float(policy_row.premium_inr),
                            synthetic=is_synthetic,
                        )
                        state.repository.record_trace_span(
                            trace_id,
                            TraceSpanViewModel(
                                span_id=_format_span_id(span),
                                parent_span_id=root_span_id,
                                service="motor-insurance-svc",
                                operation="POST /internal/insurance/issue",
                                event_type="insurance.issued",
                                started_at=utc_now().isoformat(),
                                duration_ms=dur_ms,
                                http_status=200,
                                status_from=status_before,
                                status_to="INSURED",
                                decision="ISSUED",
                                amount_inr=float(policy_row.premium_inr),
                            ),
                        )
                        state.publish_event(
                            {
                                "event_id": f"evt-{uuid4().hex[:10]}",
                                "event_time": utc_now().isoformat(),
                                "journey_id": journey_id,
                                "trace_id": trace_id,
                                "loan_id": str(loan_id),
                                "customer_id": str(customer_id),
                                "service": "motor-insurance-svc",
                                "operation": "POST /internal/insurance/issue",
                                "event_type": "insurance.issued",
                                "status_from": status_before,
                                "status_to": "INSURED",
                                "decision": "ISSUED",
                                "amount_inr": float(policy_row.premium_inr),
                                "duration_ms": int(dur_ms),
                                "is_synthetic": is_synthetic,
                            }
                        )
                    await state.repository.update_loan_status(loan_id, "INSURED")
                    return "INSURED"

            # Chaos hook UC-08: step_order_insurance_before_kyc runs INS before KYC!
            if step_order_ins_before_kyc:
                await state.repository.update_loan_status(loan_id, "APPROVED")
                current_status = await _run_insurance_step(current_status)

            # Step 1: KYC & Model Armor (Utility 1)
            kyc_start = time.perf_counter()
            with tracer.start_as_current_span("step.kyc_model_armor.verify") as kyc_span:
                kyc_payload = {
                    "loan_id": str(loan_id),
                    "customer_id": str(customer_id),
                    "kyc_mode": payload.kyc_mode,
                    "pan_number": payload.customer.pan_number,
                    "aadhaar_number": payload.customer.aadhaar_number,
                    "pan_gcs_uri": payload.pan_gcs_uri,
                    "aadhaar_gcs_uri": payload.aadhaar_gcs_uri,
                    "pan_image_model": payload.pan_image_model,
                    "aadhaar_image_model": payload.aadhaar_image_model,
                    "loan_purpose_text": payload.loan_purpose_text,
                    "employment_remarks": payload.employment_remarks,
                    "applicant_notes": payload.applicant_notes,
                    "journey_id": journey_id,
                    "trace_id": trace_id,
                }
                kyc_res = await state.downstreams.verify_kyc(kyc_payload, _outbound_headers())
                kyc_dur_ms = round((time.perf_counter() - kyc_start) * 1000.0, 2)

                blocked = bool(
                    kyc_res.get("blocked")
                    or kyc_res.get("status") == "MODEL_ARMOR_BLOCKED"
                    or kyc_res.get("model_armor_text_status") == "BLOCKED"
                )
                kyc_doc_row = KycDocumentRow(
                    kyc_id=UUID(str(kyc_res["kyc_id"])) if kyc_res.get("kyc_id") else uuid4(),
                    customer_id=customer_id,
                    loan_id=loan_id,
                    kyc_mode=payload.kyc_mode,
                    pan_gcs_uri=kyc_res.get("pan_gcs_uri") or payload.pan_gcs_uri,
                    aadhaar_gcs_uri=kyc_res.get("aadhaar_gcs_uri") or payload.aadhaar_gcs_uri,
                    pan_image_model=kyc_res.get("pan_image_model") or payload.pan_image_model,
                    aadhaar_image_model=kyc_res.get("aadhaar_image_model")
                    or payload.aadhaar_image_model,
                    pan_verified=bool(kyc_res.get("pan_verified", not blocked)),
                    aadhaar_verified=bool(kyc_res.get("aadhaar_verified", not blocked)),
                    model_armor_text_status=str(
                        kyc_res.get(
                            "model_armor_text_status", "BLOCKED" if blocked else "SANITIZED"
                        )
                    ),
                    model_armor_findings_json=kyc_res.get("model_armor_findings_json"),
                    verified_at=utc_now(),
                )
                await state.repository.upsert_kyc_document(kyc_doc_row)

                next_kyc_status: LoanStatus = (
                    "MODEL_ARMOR_BLOCKED" if blocked else "KYC_VERIFIED"
                )
                kyc_event_type = "armor.blocked" if blocked else "kyc.verified"
                set_business_span_attributes(
                    kyc_span,
                    journey_id=journey_id,
                    customer_id=str(customer_id),
                    loan_id=str(loan_id),
                    entity="kyc_document",
                    entity_id=str(kyc_doc_row.kyc_id),
                    status_from=current_status,
                    status_to=next_kyc_status,
                    event_type=kyc_event_type,
                    decision="BLOCKED" if blocked else "VERIFIED",
                    synthetic=is_synthetic,
                )
                state.repository.record_trace_span(
                    trace_id,
                    TraceSpanViewModel(
                        span_id=_format_span_id(kyc_span),
                        parent_span_id=root_span_id,
                        service="kyc-model-armor-svc",
                        operation="POST /internal/kyc/verify",
                        event_type=kyc_event_type,
                        started_at=utc_now().isoformat(),
                        duration_ms=kyc_dur_ms,
                        http_status=200,
                        status_from=current_status,
                        status_to=next_kyc_status,
                        decision="BLOCKED" if blocked else "VERIFIED",
                    ),
                )
                state.publish_event(
                    {
                        "event_id": f"evt-{uuid4().hex[:10]}",
                        "event_time": utc_now().isoformat(),
                        "journey_id": journey_id,
                        "trace_id": trace_id,
                        "loan_id": str(loan_id),
                        "customer_id": str(customer_id),
                        "service": "kyc-model-armor-svc",
                        "operation": "POST /internal/kyc/verify",
                        "event_type": kyc_event_type,
                        "status_from": current_status,
                        "status_to": next_kyc_status,
                        "decision": "BLOCKED" if blocked else "VERIFIED",
                        "duration_ms": int(kyc_dur_ms),
                        "is_synthetic": is_synthetic,
                    }
                )
                current_status = next_kyc_status
                await state.repository.update_loan_status(loan_id, current_status)

                if blocked:
                    total_ms = int((time.perf_counter() - started_monotonic) * 1000)
                    updated_loan = await state.repository.update_loan_status(
                        loan_id, current_status, total_latency_ms=total_ms
                    )
                    return await _assemble_loan_detail(
                        state.repository, updated_loan or loan_row
                    )

            # Step 2: Retail Underwriting & LOS (Utility 2)
            los_start = time.perf_counter()
            with tracer.start_as_current_span("step.underwriting_los.evaluate") as los_span:
                los_payload = {
                    "loan_id": str(loan_id),
                    "customer_id": str(customer_id),
                    "pan_number": payload.customer.pan_number,
                    "vehicle_id": payload.vehicle_id,
                    "utility_type": payload.utility_type,
                    "loan_amount_inr": float(payload.loan_amount_inr),
                    "down_payment_inr": float(payload.down_payment_inr),
                    "ex_showroom_inr": float(vehicle.ex_showroom_inr),
                    "tenure_months": payload.tenure_months,
                    "interest_rate_pct": float(payload.interest_rate_pct),
                    "emi_inr": float(emi_inr),
                    "monthly_income_inr": float(payload.customer.monthly_income_inr),
                    "existing_obligations_inr": float(payload.existing_obligations_inr),
                    "existing_emi_inr": float(payload.existing_obligations_inr),
                    "synthetic": is_synthetic,
                    "journey_id": journey_id,
                    "trace_id": trace_id,
                }
                los_res = await state.downstreams.evaluate_underwriting(
                    los_payload, _outbound_headers()
                )
                los_dur_ms = round((time.perf_counter() - los_start) * 1000.0, 2)

                decision_str = str(los_res.get("decision", "APPROVED")).upper()
                los_decision: LoanStatus = (
                    "APPROVED" if decision_str == "APPROVED" else "REJECTED"
                )
                audit_row = UnderwritingAuditRow(
                    audit_id=UUID(str(los_res["audit_id"]))
                    if los_res.get("audit_id")
                    else uuid4(),
                    loan_id=loan_id,
                    cibil_score=int(los_res.get("cibil_score", 762)),
                    cibil_bureau_ref=str(
                        los_res.get("cibil_bureau_ref", f"CIBIL-REF-{str(loan_id)[:8].upper()}")
                    ),
                    foir_pct=Decimal(str(los_res.get("foir_pct", "24.50"))),
                    ltv_pct=Decimal(str(los_res.get("ltv_pct", "78.00"))),
                    rules_version=str(los_res.get("rules_version", "v4.0.0")),
                    gke_pod_name=los_res.get("gke_pod_name"),
                    gce_vm_name=los_res.get("gce_vm_name"),
                    decision="APPROVED" if los_decision == "APPROVED" else "REJECTED",
                    rejection_reason=los_res.get("rejection_reason"),
                )
                await state.repository.save_underwriting_audit(audit_row)

                set_business_span_attributes(
                    los_span,
                    journey_id=journey_id,
                    customer_id=str(customer_id),
                    loan_id=str(loan_id),
                    entity="loan_application",
                    entity_id=str(loan_id),
                    status_from=current_status,
                    status_to=los_decision,
                    event_type="loan.decided",
                    decision=los_decision,
                    foir_pct=float(audit_row.foir_pct),
                    cibil_score=audit_row.cibil_score,
                    emi_inr=float(emi_inr),
                    amount_inr=float(payload.loan_amount_inr),
                    synthetic=is_synthetic,
                )
                state.repository.record_trace_span(
                    trace_id,
                    TraceSpanViewModel(
                        span_id=_format_span_id(los_span),
                        parent_span_id=root_span_id,
                        service="underwriting-los-svc",
                        operation="POST /internal/underwriting/evaluate",
                        event_type="loan.decided",
                        started_at=utc_now().isoformat(),
                        duration_ms=los_dur_ms,
                        http_status=200,
                        status_from=current_status,
                        status_to=los_decision,
                        decision=los_decision,
                        foir_pct=float(audit_row.foir_pct),
                        cibil_score=audit_row.cibil_score,
                        emi_inr=float(emi_inr),
                        amount_inr=float(payload.loan_amount_inr),
                        gke_pod_name=audit_row.gke_pod_name,
                        gce_vm_name=audit_row.gce_vm_name,
                        rules_version=audit_row.rules_version,
                    ),
                )
                state.publish_event(
                    {
                        "event_id": f"evt-{uuid4().hex[:10]}",
                        "event_time": utc_now().isoformat(),
                        "journey_id": journey_id,
                        "trace_id": trace_id,
                        "loan_id": str(loan_id),
                        "customer_id": str(customer_id),
                        "service": "underwriting-los-svc",
                        "operation": "POST /internal/underwriting/evaluate",
                        "event_type": "loan.decided",
                        "status_from": current_status,
                        "status_to": los_decision,
                        "decision": los_decision,
                        "foir_pct": float(audit_row.foir_pct),
                        "cibil_score": audit_row.cibil_score,
                        "emi_inr": float(emi_inr),
                        "amount_inr": float(payload.loan_amount_inr),
                        "duration_ms": int(los_dur_ms),
                        "is_synthetic": is_synthetic,
                    }
                )
                current_status = los_decision
                await state.repository.update_loan_status(loan_id, current_status)

                if los_decision == "REJECTED":
                    total_ms = int((time.perf_counter() - started_monotonic) * 1000)
                    updated_loan = await state.repository.update_loan_status(
                        loan_id, current_status, total_latency_ms=total_ms
                    )
                    return await _assemble_loan_detail(
                        state.repository, updated_loan or loan_row
                    )

            # Step 3 (Conditional): Commercial Fleet Credit Line (Utility 3)
            if payload.utility_type == "COMMERCIAL_FLEET_TOPUP":
                fleet_start = time.perf_counter()
                with tracer.start_as_current_span("step.fleet_credit.evaluate") as flt_span:
                    veh_cat = (
                        vehicle.category
                        if vehicle.category in ("TWO_WHEELER", "COMMERCIAL_FLEET")
                        else "COMMERCIAL_FLEET"
                    )
                    flt_payload = {
                        "loan_id": str(loan_id),
                        "customer_id": str(customer_id),
                        "vehicle_category": veh_cat,
                        "fleet_size": payload.fleet_size or 5,
                        "loan_amount_inr": float(payload.loan_amount_inr),
                        "requested_limit_inr": float(
                            payload.requested_limit_inr or payload.loan_amount_inr
                        ),
                        "cibil_score": audit_row.cibil_score,
                        "foir_pct": float(audit_row.foir_pct),
                        "journey_id": journey_id,
                        "trace_id": trace_id,
                    }
                    flt_res = await state.downstreams.evaluate_fleet_credit(
                        flt_payload, _outbound_headers()
                    )
                    flt_dur_ms = round((time.perf_counter() - fleet_start) * 1000.0, 2)
                    line_row = CommercialCreditLineRow(
                        credit_line_id=UUID(str(flt_res["credit_line_id"]))
                        if flt_res.get("credit_line_id")
                        else uuid4(),
                        customer_id=customer_id,
                        loan_id=loan_id,
                        vehicle_category=veh_cat,  # type: ignore[arg-type]
                        fleet_size=int(flt_res.get("fleet_size", payload.fleet_size or 5)),
                        requested_limit_inr=Decimal(
                            str(
                                flt_res.get(
                                    "requested_limit_inr",
                                    payload.requested_limit_inr or payload.loan_amount_inr,
                                )
                            )
                        ),
                        sanctioned_limit_inr=Decimal(
                            str(flt_res.get("sanctioned_limit_inr", payload.loan_amount_inr))
                        ),
                        utilisation_pct=Decimal(str(flt_res.get("utilisation_pct", "65.00"))),
                        decision=str(flt_res.get("decision", "APPROVED")),
                        status=str(
                            flt_res.get(
                                "status",
                                "SANCTIONED"
                                if str(flt_res.get("decision", "APPROVED")).upper() == "APPROVED"
                                else "REJECTED",
                            )
                        ),
                        trace_id=trace_id,
                    )
                    await state.repository.save_commercial_credit_line(line_row)
                    set_business_span_attributes(
                        flt_span,
                        journey_id=journey_id,
                        customer_id=str(customer_id),
                        loan_id=str(loan_id),
                        entity="credit_line",
                        entity_id=str(line_row.credit_line_id),
                        status_from=current_status,
                        status_to=line_row.decision,
                        event_type="credit_line.decided",
                        decision=line_row.decision,
                        amount_inr=float(line_row.sanctioned_limit_inr),
                        synthetic=is_synthetic,
                    )
                    state.repository.record_trace_span(
                        trace_id,
                        TraceSpanViewModel(
                            span_id=_format_span_id(flt_span),
                            parent_span_id=root_span_id,
                            service="commercial-fleet-credit-svc",
                            operation="POST /internal/fleet-credit/evaluate",
                            event_type="credit_line.decided",
                            started_at=utc_now().isoformat(),
                            duration_ms=flt_dur_ms,
                            http_status=200,
                            status_from=current_status,
                            status_to=line_row.decision,
                            decision=line_row.decision,
                            amount_inr=float(line_row.sanctioned_limit_inr),
                        ),
                    )
                    if line_row.decision.upper() == "REJECTED":
                        current_status = "REJECTED"
                        total_ms = int((time.perf_counter() - started_monotonic) * 1000)
                        updated_loan = await state.repository.update_loan_status(
                            loan_id, current_status, total_latency_ms=total_ms
                        )
                        return await _assemble_loan_detail(
                            state.repository, updated_loan or loan_row
                        )

            # Step 4: Motor Insurance & FastTag (Utility 4) — unless already run before KYC
            if not step_order_ins_before_kyc:
                current_status = await _run_insurance_step(current_status)

            # Step 5: Core Banking CIBIL & Disbursement (Utility 5 on port 8085)
            cbs_start = time.perf_counter()
            with tracer.start_as_current_span("step.core_banking_cbs.disburse") as cbs_span:
                cbs_payload = {
                    "loan_id": str(loan_id),
                    "customer_id": str(customer_id),
                    "pan_number": payload.customer.pan_number,
                    "disbursed_amount_inr": float(payload.loan_amount_inr),
                    "loan_amount_inr": float(payload.loan_amount_inr),
                    "dealer_account_no": payload.dealer_account_no,
                    "journey_id": journey_id,
                    "trace_id": trace_id,
                    "is_replay": False,
                }
                try:
                    cbs_res = await state.downstreams.disburse_loan(
                        cbs_payload, _outbound_headers()
                    )
                    cbs_dur_ms = round((time.perf_counter() - cbs_start) * 1000.0, 2)
                    existing_disb = await state.repository.get_disbursement_by_loan(loan_id)
                    disb_row = DisbursementRow(
                        disbursement_id=UUID(str(cbs_res["disbursement_id"]))
                        if cbs_res.get("disbursement_id")
                        else (existing_disb.disbursement_id if existing_disb else uuid4()),
                        loan_id=loan_id,
                        sanction_letter_no=str(
                            cbs_res.get(
                                "sanction_letter_no",
                                existing_disb.sanction_letter_no
                                if existing_disb
                                else f"SL-CYM-{str(loan_id)[:8].upper()}",
                            )
                        ),
                        cbs_utr_number=str(
                            cbs_res.get(
                                "cbs_utr_number",
                                f"UTR{str(loan_id).replace('-', '')[:12].upper()}",
                            )
                        ),
                        nach_mandate_id=str(
                            cbs_res.get(
                                "nach_mandate_id",
                                existing_disb.nach_mandate_id
                                if existing_disb
                                else f"NACH-CYM-{str(loan_id)[:8].upper()}",
                            )
                        ),
                        disbursed_amount_inr=Decimal(
                            str(cbs_res.get("disbursed_amount_inr", payload.loan_amount_inr))
                        ),
                        dealer_account_no=str(
                            cbs_res.get("dealer_account_no", payload.dealer_account_no)
                        ),
                        status="DISBURSED",
                        attempts=int(cbs_res.get("attempts", 1)),
                        disbursed_at=utc_now(),
                    )
                    await state.repository.save_disbursement(disb_row)
                    set_business_span_attributes(
                        cbs_span,
                        journey_id=journey_id,
                        customer_id=str(customer_id),
                        loan_id=str(loan_id),
                        entity="disbursement",
                        entity_id=str(disb_row.disbursement_id),
                        status_from=current_status,
                        status_to="DISBURSED",
                        event_type="disbursement.settled",
                        decision="DISBURSED",
                        cibil_score=audit_row.cibil_score,
                        amount_inr=float(disb_row.disbursed_amount_inr),
                        synthetic=is_synthetic,
                    )
                    state.repository.record_trace_span(
                        trace_id,
                        TraceSpanViewModel(
                            span_id=_format_span_id(cbs_span),
                            parent_span_id=root_span_id,
                            service="core-banking-cibil-vm",
                            operation="POST /internal/cibil-cbs/disburse",
                            event_type="disbursement.settled",
                            started_at=utc_now().isoformat(),
                            duration_ms=cbs_dur_ms,
                            http_status=200,
                            status_from=current_status,
                            status_to="DISBURSED",
                            decision="DISBURSED",
                            cibil_score=audit_row.cibil_score,
                            amount_inr=float(disb_row.disbursed_amount_inr),
                        ),
                    )
                    state.publish_event(
                        {
                            "event_id": f"evt-{uuid4().hex[:10]}",
                            "event_time": utc_now().isoformat(),
                            "journey_id": journey_id,
                            "trace_id": trace_id,
                            "loan_id": str(loan_id),
                            "customer_id": str(customer_id),
                            "service": "core-banking-cibil-vm",
                            "operation": "POST /internal/cibil-cbs/disburse",
                            "event_type": "disbursement.settled",
                            "status_from": current_status,
                            "status_to": "DISBURSED",
                            "decision": "DISBURSED",
                            "cibil_score": audit_row.cibil_score,
                            "amount_inr": float(disb_row.disbursed_amount_inr),
                            "duration_ms": int(cbs_dur_ms),
                            "is_synthetic": is_synthetic,
                        }
                    )
                    current_status = "DISBURSED"
                except DownstreamServiceError as exc:
                    cbs_dur_ms = round((time.perf_counter() - cbs_start) * 1000.0, 2)
                    existing_disb = await state.repository.get_disbursement_by_loan(loan_id)
                    err_disb_id: UUID | None = None
                    if isinstance(exc.detail, str) and exc.detail.strip().startswith("{"):
                        try:
                            err_obj = json.loads(exc.detail)
                            if isinstance(err_obj, dict) and err_obj.get("disbursement_id"):
                                err_disb_id = UUID(str(err_obj["disbursement_id"]))
                        except (ValueError, TypeError):
                            err_disb_id = None
                    failed_disb = DisbursementRow(
                        disbursement_id=(
                            existing_disb.disbursement_id
                            if existing_disb
                            else (err_disb_id or uuid4())
                        ),
                        loan_id=loan_id,
                        sanction_letter_no=(
                            existing_disb.sanction_letter_no
                            if existing_disb
                            else f"SL-CYM-{str(loan_id)[:8].upper()}"
                        ),
                        cbs_utr_number=None,
                        nach_mandate_id=(
                            existing_disb.nach_mandate_id
                            if existing_disb
                            else f"NACH-CYM-{str(loan_id)[:8].upper()}"
                        ),
                        disbursed_amount_inr=payload.loan_amount_inr,
                        dealer_account_no=payload.dealer_account_no,
                        status="FAILED",
                        attempts=existing_disb.attempts if existing_disb else 1,
                        disbursed_at=None,
                    )
                    await state.repository.save_disbursement(failed_disb)
                    state.repository.record_trace_span(
                        trace_id,
                        TraceSpanViewModel(
                            span_id=_format_span_id(cbs_span),
                            parent_span_id=root_span_id,
                            service="core-banking-cibil-vm",
                            operation="POST /internal/cibil-cbs/disburse",
                            event_type=None,
                            started_at=utc_now().isoformat(),
                            duration_ms=cbs_dur_ms,
                            http_status=exc.status_code,
                            status_from=current_status,
                            status_to="DISBURSEMENT_PENDING",
                            decision="FAILED",
                            amount_inr=float(payload.loan_amount_inr),
                        ),
                    )
                    current_status = "DISBURSEMENT_PENDING"

            total_ms = int((time.perf_counter() - started_monotonic) * 1000)
            final_loan = await state.repository.update_loan_status(
                loan_id, current_status, total_latency_ms=total_ms
            )
            return await _assemble_loan_detail(state.repository, final_loan or loan_row)

    @router.get(
        "/loans",
        response_model=LoanListResponse,
        summary="List loan journeys with optional status, utility_type, and synthetic filters",
    )
    async def list_loans(
        status: str | None = Query(default=None),
        utility_type: str | None = Query(default=None),
        synthetic: bool | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> LoanListResponse:
        _check_not_paused()
        rows, total = await state.repository.list_loan_applications(
            status=status,
            utility_type=utility_type,
            synthetic=synthetic,
            limit=limit,
            offset=offset,
        )
        items = [await _assemble_loan_detail(state.repository, r) for r in rows]
        return LoanListResponse(
            items=items,
            loans=items,
            total=total,
            limit=limit,
            offset=offset,
            next_cursor=str(offset + limit) if (offset + limit) < total else None,
        )

    @router.get(
        "/loans/{loan_id}",
        response_model=LoanJourneyDetailResponse,
        summary="Get full journey state and 5-utility records for a single loan_id",
    )
    async def get_loan(loan_id: UUID) -> LoanJourneyDetailResponse:
        _check_not_paused()
        loan = await state.repository.get_loan_application(loan_id)
        if loan is None:
            raise HTTPException(status_code=404, detail=f"Loan {loan_id} not found")
        return await _assemble_loan_detail(state.repository, loan)

    @router.get(
        "/kyc-documents/{customer_id}/{doc_type}",
        response_model=KycSignedDocumentResponse,
        summary="Return a signed specimen card URL for IMAGE journeys (404 on TEXT_ONLY)",
    )
    async def get_kyc_document_signed_url(
        customer_id: UUID,
        doc_type: str,
    ) -> KycSignedDocumentResponse:
        _check_not_paused()
        norm_type = doc_type.strip().lower()
        if norm_type not in ("pan", "aadhaar"):
            raise HTTPException(
                status_code=400, detail="doc_type must be 'pan' or 'aadhaar'"
            )
        doc = await state.repository.get_latest_kyc_document_for_customer(customer_id)
        if doc is None or doc.kyc_mode == "TEXT_ONLY":
            raise HTTPException(
                status_code=404,
                detail="KYC specimen image not available for TEXT_ONLY or unknown customer",
            )
        gcs_uri = (
            doc.pan_gcs_uri if norm_type == "pan" else doc.aadhaar_gcs_uri
        ) or f"gs://{state.settings.kyc_vault_bucket}/specimens/{customer_id}/{norm_type}.png"
        image_model = (
            doc.pan_image_model if norm_type == "pan" else doc.aadhaar_image_model
        ) or MODEL_TABLE["specimen_image"].model_id
        expires_at = (utc_now() + timedelta(minutes=15)).isoformat()
        object_path = gcs_uri.removeprefix(f"gs://{state.settings.kyc_vault_bucket}/")
        signed_url = (
            f"https://storage.googleapis.com/{state.settings.kyc_vault_bucket}/"
            f"{object_path}?X-Goog-Algorithm=GOOG4-RSA-SHA256&X-Goog-Expires=900"
        )
        return KycSignedDocumentResponse(
            kyc_id=str(doc.kyc_id),
            customer_id=str(customer_id),
            loan_id=str(doc.loan_id) if doc.loan_id else None,
            doc_type=norm_type,  # type: ignore[arg-type]
            kyc_mode=doc.kyc_mode,
            gcs_uri=gcs_uri,
            signed_url=signed_url,
            expires_at=expires_at,
            image_model=image_model,
            watermark_text="SPECIMEN · NOT A GOVERNMENT DOCUMENT",
            model_armor_scanned=False,
        )

    @router.get(
        "/observability/topology",
        response_model=ObservabilityTopologyResponse,
        summary="Return live 5-utility tenant topology and golden signals",
        openapi_extra={"x-godview-probe-safe": True},
    )
    async def get_observability_topology() -> ObservabilityTopologyResponse:
        _check_not_paused()
        active_faults = await state.repository.list_active_chaos_faults()
        by_target: dict[str, list[str]] = {}
        for f in active_faults:
            by_target.setdefault(f.target_service, []).append(f.fault_type)

        def _node_status(svc: str) -> str:
            faults = by_target.get(svc, [])
            if any(ft in ("gke_oom_503", "gce_vm_latency_504", "cloudsql_lock_contention") for ft in faults):
                return "critical"
            if faults:
                return "degraded"
            return "healthy"

        nodes = [
            TopologyNodeModel(
                id="god-view-loan-api-gw",
                name="API Gateway (god-view-loan-api-gw)",
                kind="api_gateway",
                utility_number=None,
                utility_label="Edge Ingress",
                status="healthy",
                p95_ms=18.0,
                error_rate_pct=0.0,
                requests_per_min=42.0,
                active_faults=[],
            ),
            TopologyNodeModel(
                id="loan-orchestrator-api",
                name="loan-orchestrator-api",
                kind="cloud_run",
                utility_number=0,
                utility_label="Journey Coordinator",
                status=_node_status("loan-orchestrator-api"),  # type: ignore[arg-type]
                p95_ms=145.0,
                error_rate_pct=0.1,
                requests_per_min=42.0,
                active_faults=by_target.get("loan-orchestrator-api", []),
            ),
            TopologyNodeModel(
                id="kyc-model-armor-svc",
                name="kyc-model-armor-svc",
                kind="gke_workload",
                utility_number=1,
                utility_label="Utility 1: KYC & Model Armor",
                status=_node_status("kyc-model-armor-svc"),  # type: ignore[arg-type]
                p95_ms=48.0,
                error_rate_pct=0.0,
                requests_per_min=42.0,
                active_faults=by_target.get("kyc-model-armor-svc", []),
            ),
            TopologyNodeModel(
                id="underwriting-los-svc",
                name="underwriting-los-svc",
                kind="gke_workload",
                utility_number=2,
                utility_label="Utility 2: Retail Underwriting & LOS",
                status=_node_status("underwriting-los-svc"),  # type: ignore[arg-type]
                p95_ms=54.0,
                error_rate_pct=0.0,
                requests_per_min=38.0,
                active_faults=by_target.get("underwriting-los-svc", []),
            ),
            TopologyNodeModel(
                id="commercial-fleet-credit-svc",
                name="commercial-fleet-credit-svc",
                kind="cloud_run",
                utility_number=3,
                utility_label="Utility 3: Commercial Fleet Credit",
                status=_node_status("commercial-fleet-credit-svc"),  # type: ignore[arg-type]
                p95_ms=39.0,
                error_rate_pct=0.0,
                requests_per_min=11.0,
                active_faults=by_target.get("commercial-fleet-credit-svc", []),
            ),
            TopologyNodeModel(
                id="motor-insurance-svc",
                name="motor-insurance-svc",
                kind="cloud_run",
                utility_number=4,
                utility_label="Utility 4: Motor Insurance & FastTag",
                status=_node_status("motor-insurance-svc"),  # type: ignore[arg-type]
                p95_ms=34.0,
                error_rate_pct=0.0,
                requests_per_min=32.0,
                active_faults=by_target.get("motor-insurance-svc", []),
            ),
            TopologyNodeModel(
                id="core-banking-cibil-vm",
                name="core-banking-cibil-vm",
                kind="gce_vm",
                utility_number=5,
                utility_label="Utility 5: Core Banking CIBIL & CBS",
                status=_node_status("core-banking-cibil-vm"),  # type: ignore[arg-type]
                p95_ms=65.0,
                error_rate_pct=0.0,
                requests_per_min=32.0,
                active_faults=by_target.get("core-banking-cibil-vm", []),
            ),
            TopologyNodeModel(
                id="god-view-pg",
                name="Cloud SQL (godview_lms)",
                kind="cloud_sql",
                utility_number=None,
                utility_label="9 Business Tables",
                status=_node_status("god-view-pg"),  # type: ignore[arg-type]
                p95_ms=8.5,
                error_rate_pct=0.0,
                requests_per_min=180.0,
                active_faults=by_target.get("god-view-pg", []),
            ),
        ]
        edges = [
            TopologyEdgeModel(
                src="god-view-loan-api-gw",
                dst="loan-orchestrator-api",
                protocol="HTTP/REST",
                call_rate_per_min=42.0,
                p95_ms=145.0,
                error_rate_pct=0.1,
            ),
            TopologyEdgeModel(
                src="loan-orchestrator-api",
                dst="kyc-model-armor-svc",
                protocol="W3C-OTel-HTTP",
                call_rate_per_min=42.0,
                p95_ms=48.0,
                error_rate_pct=0.0,
            ),
            TopologyEdgeModel(
                src="loan-orchestrator-api",
                dst="underwriting-los-svc",
                protocol="W3C-OTel-HTTP",
                call_rate_per_min=38.0,
                p95_ms=54.0,
                error_rate_pct=0.0,
            ),
            TopologyEdgeModel(
                src="loan-orchestrator-api",
                dst="commercial-fleet-credit-svc",
                protocol="W3C-OTel-HTTP",
                call_rate_per_min=11.0,
                p95_ms=39.0,
                error_rate_pct=0.0,
            ),
            TopologyEdgeModel(
                src="loan-orchestrator-api",
                dst="motor-insurance-svc",
                protocol="W3C-OTel-HTTP",
                call_rate_per_min=32.0,
                p95_ms=34.0,
                error_rate_pct=0.0,
            ),
            TopologyEdgeModel(
                src="loan-orchestrator-api",
                dst="core-banking-cibil-vm",
                protocol="W3C-OTel-HTTP",
                call_rate_per_min=32.0,
                p95_ms=65.0,
                error_rate_pct=0.0,
            ),
            TopologyEdgeModel(
                src="loan-orchestrator-api",
                dst="god-view-pg",
                protocol="SQL/TCP",
                call_rate_per_min=180.0,
                p95_ms=8.5,
                error_rate_pct=0.0,
            ),
        ]
        return ObservabilityTopologyResponse(
            env_id=state.settings.env_id,
            journey_name="vehicle_loan_origination",
            intended_sequence="KYC < LOS < INS < CBS",
            nodes=nodes,
            edges=edges,
            updated_at=utc_now().isoformat(),
        )

    @router.get(
        "/observability/traces/{trace_id}",
        response_model=ObservabilityTraceResponse,
        summary="Return correlated W3C trace spans and conformance status for a journey trace_id",
    )
    async def get_observability_trace(trace_id: str) -> ObservabilityTraceResponse:
        _check_not_paused()
        loan = await state.repository.get_loan_by_trace_id(trace_id)
        if loan is None:
            raise HTTPException(
                status_code=404, detail=f"Trace {trace_id} not found"
            )
        customer = await state.repository.get_customer(loan.customer_id)
        is_synthetic = bool(customer.is_ai_generated) if customer else False
        spans = state.repository.get_recorded_trace_spans(trace_id)
        if not spans:
            root_id = (loan.trace_id or trace_id)[:16]
            reconstructed: list[TraceSpanViewModel] = []
            kyc_doc = await state.repository.get_kyc_document_by_loan(loan.loan_id)
            audit = await state.repository.get_underwriting_audit_by_loan(loan.loan_id)
            credit_line = await state.repository.get_commercial_credit_line_by_loan(loan.loan_id)
            policy = await state.repository.get_insurance_policy_by_loan(loan.loan_id)
            disb = await state.repository.get_disbursement_by_loan(loan.loan_id)

            if kyc_doc is not None:
                blocked = (
                    kyc_doc.model_armor_text_status == "BLOCKED"
                    or not kyc_doc.pan_verified
                )
                reconstructed.append(
                    TraceSpanViewModel(
                        span_id=kyc_doc.kyc_id.hex[:16],
                        parent_span_id=root_id,
                        service="kyc-model-armor-svc",
                        operation="POST /internal/kyc/verify",
                        event_type="armor.blocked" if blocked else "kyc.verified",
                        started_at=(kyc_doc.verified_at or loan.created_at).isoformat(),
                        duration_ms=25.0,
                        http_status=200,
                        status_from="KYC_PENDING",
                        status_to="MODEL_ARMOR_BLOCKED" if blocked else "KYC_VERIFIED",
                        decision="BLOCKED" if blocked else "VERIFIED",
                    )
                )
            if audit is not None:
                reconstructed.append(
                    TraceSpanViewModel(
                        span_id=audit.audit_id.hex[:16],
                        parent_span_id=root_id,
                        service="underwriting-los-svc",
                        operation="POST /internal/underwriting/evaluate",
                        event_type="loan.decided",
                        started_at=audit.evaluated_at.isoformat(),
                        duration_ms=35.0,
                        http_status=200,
                        status_from="KYC_VERIFIED",
                        status_to=audit.decision,
                        decision=audit.decision,
                        foir_pct=float(audit.foir_pct),
                        cibil_score=audit.cibil_score,
                        emi_inr=float(loan.emi_inr),
                        amount_inr=float(loan.loan_amount_inr),
                        gke_pod_name=audit.gke_pod_name,
                        gce_vm_name=audit.gce_vm_name,
                        rules_version=audit.rules_version,
                    )
                )
            if credit_line is not None:
                reconstructed.append(
                    TraceSpanViewModel(
                        span_id=credit_line.credit_line_id.hex[:16],
                        parent_span_id=root_id,
                        service="commercial-fleet-credit-svc",
                        operation="POST /internal/fleet-credit/evaluate",
                        event_type="credit_line.decided",
                        started_at=credit_line.evaluated_at.isoformat(),
                        duration_ms=20.0,
                        http_status=200,
                        status_from="APPROVED",
                        status_to=credit_line.decision,
                        decision=credit_line.decision,
                        amount_inr=float(credit_line.sanctioned_limit_inr),
                    )
                )
            if policy is not None:
                reconstructed.append(
                    TraceSpanViewModel(
                        span_id=policy.policy_id.hex[:16],
                        parent_span_id=root_id,
                        service="motor-insurance-svc",
                        operation="POST /internal/insurance/issue",
                        event_type="insurance.issued",
                        started_at=policy.issued_at.isoformat(),
                        duration_ms=20.0,
                        http_status=200,
                        status_from="APPROVED",
                        status_to="INSURED",
                        decision="ISSUED",
                        amount_inr=float(policy.premium_inr),
                    )
                )
            if disb is not None:
                is_failed = disb.status in ("FAILED", "PENDING")
                reconstructed.append(
                    TraceSpanViewModel(
                        span_id=disb.disbursement_id.hex[:16],
                        parent_span_id=root_id,
                        service="core-banking-cibil-vm",
                        operation="POST /internal/cibil-cbs/disburse",
                        event_type=None if is_failed else "disbursement.settled",
                        started_at=(disb.disbursed_at or loan.updated_at).isoformat(),
                        duration_ms=40.0,
                        http_status=504 if is_failed else 200,
                        status_from="INSURED" if policy is not None else "APPROVED",
                        status_to="DISBURSEMENT_PENDING" if is_failed else "DISBURSED",
                        decision="FAILED" if is_failed else disb.status,
                        cibil_score=audit.cibil_score if audit is not None else None,
                        amount_inr=float(disb.disbursed_amount_inr),
                    )
                )
            reconstructed.sort(key=lambda s: s.started_at)
            spans = reconstructed

        step_names: list[str] = []
        for s in spans:
            if "kyc" in s.service:
                step_names.append("KYC")
            elif "underwriting" in s.service:
                step_names.append("LOS")
            elif "fleet-credit" in s.service:
                step_names.append("FLEET_CREDIT")
            elif "insurance" in s.service:
                step_names.append("INS")
            elif "cibil" in s.service:
                step_names.append("CBS")

        observed_seq = " -> ".join(step_names) if step_names else "KYC -> LOS -> INS -> CBS"
        conformance = "CONFORMANT"
        if step_names and step_names[0] == "INS":
            conformance = "REORDERED"
        elif loan.status == "DISBURSED" and "INS" not in step_names:
            conformance = "SKIPPED"
        elif loan.status == "DISBURSEMENT_PENDING":
            conformance = "STALLED"

        return ObservabilityTraceResponse(
            trace_id=trace_id,
            journey_id=loan.journey_id,
            loan_id=str(loan.loan_id),
            customer_id=str(loan.customer_id),
            is_synthetic=is_synthetic,
            conformance=conformance,  # type: ignore[arg-type]
            observed_sequence=observed_seq,
            total_duration_ms=float(loan.total_latency_ms or 120),
            spans=spans,
        )

    @router.get(
        "/telemetry/stream",
        summary="Server-Sent Events (SSE) stream of journey step events with 15 s heartbeat",
    )
    async def stream_telemetry(
        request: Request,
        journey_id: str | None = Query(
            default=None,
            description="Optional journey_id filter so customer portals only receive their own journey events",
        ),
        loan_id: str | None = Query(
            default=None,
            description="Optional loan_id filter",
        ),
        max_events: int | None = Query(default=None, ge=1, le=100),
    ) -> StreamingResponse:
        _check_not_paused()

        def _matches_filter(evt: dict[str, Any]) -> bool:
            if journey_id is not None:
                evt_jrn = str(evt.get("journey_id") or evt.get("journey.id") or "")
                if evt_jrn != journey_id:
                    return False
            if loan_id is not None:
                evt_loan = str(evt.get("loan_id") or evt.get("loan.id") or "")
                if evt_loan != loan_id:
                    return False
            return True

        async def _event_generator() -> AsyncIterator[str]:
            sent = 0
            # Emit recent buffered events first so newly connected UIs get immediate state
            snapshot = [e for e in list(state.recent_events) if _matches_filter(e)][-10:]
            for evt in snapshot:
                yield f"event: step\ndata: {json.dumps(evt)}\n\n"
                sent += 1
                if max_events is not None and sent >= max_events:
                    return

            # Emit an initial heartbeat frame if no events buffered and max_events is bounded
            if max_events is not None and sent == 0:
                hb = {
                    "event_type": "heartbeat",
                    "timestamp": utc_now().isoformat(),
                    "env_id": state.settings.env_id,
                }
                yield f"event: heartbeat\ndata: {json.dumps(hb)}\n\n"
                sent += 1
                if sent >= max_events:
                    return

            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
            state.sse_Queues.add(queue)
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        evt = await asyncio.wait_for(
                            queue.get(), timeout=state.settings.sse_heartbeat_seconds
                        )
                        if not _matches_filter(evt):
                            continue
                        yield f"event: step\ndata: {json.dumps(evt)}\n\n"
                    except TimeoutError:
                        hb = {
                            "event_type": "heartbeat",
                            "timestamp": utc_now().isoformat(),
                            "env_id": state.settings.env_id,
                        }
                        yield f"event: heartbeat\ndata: {json.dumps(hb)}\n\n"
                    sent += 1
                    if max_events is not None and sent >= max_events:
                        break
            finally:
                state.sse_Queues.discard(queue)

        return StreamingResponse(
            _event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router


def _build_admin_router(state: OrchestratorRuntimeState) -> APIRouter:
    router = APIRouter(prefix="/admin/v1", tags=["tenant-admin"])

    @router.post(
        "/disbursements/{loan_id}:replay",
        response_model=DisbursementReplayResponse,
        summary="Idempotently replay a stuck/failed CBS disbursement by loan_id (L4)",
        openapi_extra={
            "x-godview-admin-action": "biz.replay_disbursement",
            "x-godview-reversal": {
                "capability": "biz.compensate_journey",
                "endpoint": "POST /admin/v1/journeys/{journey_id}:compensate",
            },
        },
    )
    async def replay_disbursement(loan_id: UUID) -> DisbursementReplayResponse:
        loan = await state.repository.get_loan_application(loan_id)
        if loan is None:
            raise HTTPException(status_code=404, detail=f"Loan {loan_id} not found")

        existing = await state.repository.get_disbursement_by_loan(loan_id)
        if existing and existing.status in ("DISBURSED", "REPLAYED"):
            return DisbursementReplayResponse(
                loan_id=str(loan_id),
                loan_status=loan.status,
                idempotent_replay=True,
                disbursement=existing.model_dump(mode="json"),
            )

        customer = await state.repository.get_customer(loan.customer_id)
        is_synthetic = bool(customer.is_ai_generated) if customer else False
        tracer = get_tracer(state.settings.service_name)

        with tracer.start_as_current_span("admin.replay_disbursement") as span:
            hdrs = inject_baggage_headers(
                journey_id=loan.journey_id,
                customer_id=str(loan.customer_id),
                synthetic=is_synthetic,
                traceparent=_build_traceparent(span),
            )
            cbs_payload = {
                "loan_id": str(loan_id),
                "customer_id": str(loan.customer_id),
                "pan_number": customer.pan_number if customer else "ABCDE1234Z",
                "disbursed_amount_inr": float(loan.loan_amount_inr),
                "loan_amount_inr": float(loan.loan_amount_inr),
                "dealer_account_no": existing.dealer_account_no
                if existing
                else "CYMBAL-DLR-0001",
                "journey_id": loan.journey_id,
                "trace_id": loan.trace_id or _format_trace_id(span),
                "is_replay": True,
            }
            cbs_res = await state.downstreams.disburse_loan(cbs_payload, hdrs)
            idempotent_flag = bool(cbs_res.get("idempotent_replay", False))
            replayed_row = DisbursementRow(
                disbursement_id=UUID(str(cbs_res["disbursement_id"]))
                if cbs_res.get("disbursement_id")
                else (existing.disbursement_id if existing else uuid4()),
                loan_id=loan_id,
                sanction_letter_no=str(
                    cbs_res.get("sanction_letter_no", f"SL-CYM-{str(loan_id)[:8].upper()}")
                ),
                cbs_utr_number=str(
                    cbs_res.get(
                        "cbs_utr_number",
                        f"UTR{str(loan_id).replace('-', '')[:12].upper()}",
                    )
                ),
                nach_mandate_id=str(
                    cbs_res.get("nach_mandate_id", f"NACH-CYM-{str(loan_id)[:8].upper()}")
                ),
                disbursed_amount_inr=Decimal(
                    str(cbs_res.get("disbursed_amount_inr", loan.loan_amount_inr))
                ),
                dealer_account_no=str(
                    cbs_res.get("dealer_account_no", "CYMBAL-DLR-0001")
                ),
                status="REPLAYED",
                attempts=int(
                    cbs_res.get("attempts", (existing.attempts + 1) if existing else 1)
                ),
                replay_of=existing.disbursement_id if existing else None,
                disbursed_at=utc_now(),
            )
            await state.repository.save_disbursement(replayed_row)
            await state.repository.update_loan_status(loan_id, "DISBURSED")
            set_business_span_attributes(
                span,
                journey_id=loan.journey_id,
                customer_id=str(loan.customer_id),
                loan_id=str(loan_id),
                entity="disbursement",
                entity_id=str(replayed_row.disbursement_id),
                status_from=loan.status,
                status_to="DISBURSED",
                event_type="disbursement.settled",
                decision="REPLAYED",
                amount_inr=float(replayed_row.disbursed_amount_inr),
                synthetic=is_synthetic,
            )
            return DisbursementReplayResponse(
                loan_id=str(loan_id),
                loan_status="DISBURSED",
                idempotent_replay=idempotent_flag,
                disbursement=replayed_row.model_dump(mode="json"),
            )

    @router.post(
        "/loans/{loan_id}:re-evaluate",
        response_model=LoanReevaluateResponse,
        summary="Re-run underwriting evaluation for an application with current rules (L4)",
        openapi_extra={
            "x-godview-admin-action": "biz.reevaluate_application",
            "x-godview-reversal": {
                "capability": "biz.compensate_journey",
                "endpoint": "POST /admin/v1/journeys/{journey_id}:compensate",
            },
        },
    )
    async def reevaluate_loan(loan_id: UUID) -> LoanReevaluateResponse:
        loan = await state.repository.get_loan_application(loan_id)
        if loan is None:
            raise HTTPException(status_code=404, detail=f"Loan {loan_id} not found")
        customer = await state.repository.get_customer(loan.customer_id)
        vehicle = await state.repository.get_vehicle(loan.vehicle_id)
        is_synthetic = bool(customer.is_ai_generated) if customer else False

        active_faults = await state.repository.list_active_chaos_faults()
        emi_rounding_active = any(
            f.fault_type == "emi_rounding" and f.is_active for f in active_faults
        )
        recalculated_emi = calculate_emi_inr(
            loan.loan_amount_inr,
            loan.interest_rate_pct,
            loan.tenure_months,
            emi_rounding_bug=emi_rounding_active,
        )

        hdrs = inject_baggage_headers(
            journey_id=loan.journey_id,
            customer_id=str(loan.customer_id),
            synthetic=is_synthetic,
            traceparent=f"00-{loan.trace_id or uuid4().hex}-{uuid4().hex[:16]}-01",
        )
        los_payload = {
            "loan_id": str(loan_id),
            "customer_id": str(loan.customer_id),
            "pan_number": customer.pan_number if customer else "ABCDE1234Z",
            "vehicle_id": loan.vehicle_id,
            "utility_type": loan.utility_type,
            "loan_amount_inr": float(loan.loan_amount_inr),
            "down_payment_inr": float(loan.down_payment_inr),
            "ex_showroom_inr": float(
                vehicle.ex_showroom_inr if vehicle else loan.loan_amount_inr
            ),
            "tenure_months": loan.tenure_months,
            "interest_rate_pct": float(loan.interest_rate_pct),
            "emi_inr": float(recalculated_emi),
            "monthly_income_inr": float(
                customer.monthly_income_inr if customer else Decimal("145000")
            ),
            "existing_obligations_inr": 0.0,
            "existing_emi_inr": 0.0,
            "synthetic": is_synthetic,
            "journey_id": loan.journey_id,
            "trace_id": loan.trace_id,
        }
        los_res = await state.downstreams.evaluate_underwriting(los_payload, hdrs)
        decision_str = str(los_res.get("decision", "APPROVED")).upper()
        new_status: LoanStatus = (
            "APPROVED" if decision_str == "APPROVED" else "REJECTED"
        )
        audit_row = UnderwritingAuditRow(
            audit_id=UUID(str(los_res["audit_id"]))
            if los_res.get("audit_id")
            else uuid4(),
            loan_id=loan_id,
            cibil_score=int(los_res.get("cibil_score", 762)),
            cibil_bureau_ref=str(
                los_res.get("cibil_bureau_ref", f"CIBIL-REF-{str(loan_id)[:8].upper()}")
            ),
            foir_pct=Decimal(str(los_res.get("foir_pct", "24.50"))),
            ltv_pct=Decimal(str(los_res.get("ltv_pct", "78.00"))),
            rules_version=str(los_res.get("rules_version", "v4.0.0")),
            gke_pod_name=los_res.get("gke_pod_name"),
            gce_vm_name=los_res.get("gce_vm_name"),
            decision="APPROVED" if new_status == "APPROVED" else "REJECTED",
            rejection_reason=los_res.get("rejection_reason"),
        )
        await state.repository.save_underwriting_audit(audit_row)
        await state.repository.update_loan_status(
            loan_id, new_status, emi_inr=recalculated_emi
        )
        return LoanReevaluateResponse(
            loan_id=str(loan_id),
            previous_status=loan.status,
            new_status=new_status,
            emi_inr=float(recalculated_emi),
            underwriting_audit=audit_row.model_dump(mode="json"),
        )

    @router.post(
        "/journeys/{journey_id}:compensate",
        response_model=JourneyCompensateResponse,
        summary="Compensate a journey by cancelling policy and reversing loan state (L4)",
        openapi_extra={
            "x-godview-admin-action": "biz.compensate_journey",
            "x-godview-reversal": {
                "capability": "biz.reevaluate_application",
                "endpoint": "POST /admin/v1/loans/{loan_id}:re-evaluate",
            },
        },
    )
    async def compensate_journey(journey_id: str) -> JourneyCompensateResponse:
        loan = await state.repository.get_loan_by_journey_id(journey_id)
        if loan is None:
            raise HTTPException(
                status_code=404, detail=f"Journey {journey_id} not found"
            )
        cancelled_policy = await state.repository.cancel_insurance_policy_by_loan(
            loan.loan_id
        )
        existing_disb = await state.repository.get_disbursement_by_loan(loan.loan_id)
        disb_reversed = False
        if existing_disb is not None:
            await state.repository.save_disbursement(
                existing_disb.model_copy(update={"status": "FAILED"})
            )
            disb_reversed = True

        await state.repository.update_loan_status(loan.loan_id, "REJECTED")
        return JourneyCompensateResponse(
            journey_id=journey_id,
            loan_id=str(loan.loan_id),
            previous_status=loan.status,
            loan_status="REJECTED",
            insurance_cancelled=cancelled_policy is not None,
            disbursement_reversed=disb_reversed,
            compensated=True,
            compensated_at=utc_now().isoformat(),
        )

    @router.get(
        "/disbursements",
        response_model=StuckDisbursementsResponse,
        summary="List stuck or failed disbursements in the saga replay queue (L0)",
        openapi_extra={
            "x-godview-probe-safe": True,
        },
    )
    async def list_stuck_disbursements(
        status: str = Query(default="PENDING|FAILED"),
    ) -> StuckDisbursementsResponse:
        statuses = [
            s.strip().upper()
            for part in status.split("|")
            for s in part.split(",")
            if s.strip()
        ]
        rows = await state.repository.list_disbursements_by_statuses(statuses)
        return StuckDisbursementsResponse(
            status_filter=statuses,
            count=len(rows),
            disbursements=[r.model_dump(mode="json") for r in rows],
        )

    @router.post(
        "/tenant/state",
        response_model=TenantStateResponse,
        summary="Set tenant pause state (503 TENANT_PAUSED at ingress while paused)",
        openapi_extra={
            "x-godview-admin-action": "tenant.set_paused",
            "x-godview-reversal": {
                "capability": "tenant.set_paused",
                "endpoint": "POST /admin/v1/tenant/state",
                "args": {"paused": False},
            },
        },
    )
    async def set_tenant_state(body: TenantStateRequest) -> TenantStateResponse:
        state.paused = body.paused
        state.pause_reason = body.reason
        state.pause_updated_at = utc_now()
        return TenantStateResponse(
            paused=state.paused,
            reason=state.pause_reason,
            updated_at=state.pause_updated_at.isoformat(),
        )

    @router.post(
        "/tenant/pause",
        response_model=TenantStateResponse,
        summary="Pause tenant traffic (answers 503 TENANT_PAUSED at ingress)",
        openapi_extra={
            "x-godview-admin-action": "tenant.set_paused",
            "x-godview-reversal": {
                "capability": "tenant.set_paused",
                "endpoint": "POST /admin/v1/tenant/resume",
            },
        },
    )
    async def pause_tenant(
        body: TenantStateRequest | None = None,
    ) -> TenantStateResponse:
        state.paused = True if body is None else body.paused
        state.pause_reason = (
            body.reason
            if (body and body.reason)
            else "Paused via /admin/v1/tenant/pause"
        )
        state.pause_updated_at = utc_now()
        return TenantStateResponse(
            paused=state.paused,
            reason=state.pause_reason,
            updated_at=state.pause_updated_at.isoformat(),
        )

    @router.post(
        "/tenant/resume",
        response_model=TenantStateResponse,
        summary="Resume tenant traffic after pause or kill switch",
        openapi_extra={
            "x-godview-admin-action": "tenant.set_paused",
            "x-godview-reversal": {
                "capability": "tenant.set_paused",
                "endpoint": "POST /admin/v1/tenant/pause",
            },
        },
    )
    async def resume_tenant() -> TenantStateResponse:
        state.paused = False
        state.pause_reason = None
        state.pause_updated_at = utc_now()
        return TenantStateResponse(
            paused=state.paused,
            reason=state.pause_reason,
            updated_at=state.pause_updated_at.isoformat(),
        )

    @router.post(
        "/chaos/faults",
        response_model=ChaosFaultStateRow,
        summary="Activate an opt-in chaos drill fault on godview_managed=true Cymbal resources",
        openapi_extra={
            "x-godview-admin-action": "chaos.activate",
            "x-godview-reversal": {
                "capability": "chaos.clear",
                "endpoint": "DELETE /admin/v1/chaos/faults/{fault_id}",
            },
        },
    )
    async def activate_chaos_fault(
        body: ChaosFaultActivateRequest,
    ) -> ChaosFaultStateRow:
        if body.godview_managed.strip().lower() != "true" or (
            state.settings.godview_managed.strip().lower() != "true"
        ):
            raise HTTPException(
                status_code=403,
                detail="Chaos faults may only be activated on godview_managed='true' resources (I-07)",
            )
        now = utc_now()
        fault = ChaosFaultStateRow(
            fault_id=body.fault_id,
            fault_class=body.fault_class,
            fault_type=body.fault_type,
            target_service=body.target_service,
            is_active=True,
            parameters_json=body.parameters_json,
            triggered_by=body.triggered_by,
            activated_at=now,
            expires_at=now + timedelta(seconds=body.ttl_seconds),
        )
        saved = await state.repository.upsert_chaos_fault(fault)
        if fault.target_service.strip().lower() in (
            "kyc-model-armor-svc",
            "underwriting-los-svc",
        ):
            try:
                await state.downstreams.activate_chaos_fault(
                    fault.target_service,
                    {
                        "fault_id": fault.fault_id,
                        "fault_type": fault.fault_type,
                        "duration_seconds": body.ttl_seconds,
                        "parameters_json": fault.parameters_json or {},
                        "triggered_by": fault.triggered_by or "loan-orchestrator-api",
                    },
                )
            except DownstreamServiceError as exc:
                await state.repository.clear_chaos_fault(fault.fault_id)
                raise HTTPException(
                    status_code=exc.status_code, detail=exc.detail
                ) from exc
        return saved

    @router.delete(
        "/chaos/faults/{fault_id}",
        response_model=ChaosFaultStateRow,
        summary="Clear an active chaos drill fault",
        openapi_extra={
            "x-godview-admin-action": "chaos.clear",
            "x-godview-reversal": {
                "capability": "chaos.activate",
                "endpoint": "POST /admin/v1/chaos/faults",
            },
        },
    )
    async def clear_chaos_fault(fault_id: str) -> ChaosFaultStateRow:
        cleared = await state.repository.clear_chaos_fault(fault_id)
        if cleared is None:
            raise HTTPException(status_code=404, detail=f"Fault {fault_id} not found")
        if cleared.target_service.strip().lower() in (
            "kyc-model-armor-svc",
            "underwriting-los-svc",
        ):
            try:
                await state.downstreams.clear_chaos_fault(
                    cleared.target_service, cleared.fault_type
                )
            except DownstreamServiceError as exc:
                raise HTTPException(
                    status_code=exc.status_code, detail=exc.detail
                ) from exc
        return cleared

    return router


def create_app(
    settings: OrchestratorSettings | None = None,
    repository: OrchestratorRepository | None = None,
    downstreams: DownstreamServiceBundle | None = None,
) -> FastAPI:
    """Create and wire the FastAPI application for loan-orchestrator-api."""
    resolved_settings = settings or OrchestratorSettings()
    env_mode = os.getenv("GODVIEW_ENV", "").strip().lower()
    if repository is not None:
        resolved_repo = repository
    elif resolved_settings.database_url and not resolved_settings.use_in_memory_db:
        resolved_repo = PostgresOrchestratorRepository(resolved_settings.database_url)
    elif env_mode == "hermetic" or resolved_settings.use_in_memory_db:
        resolved_repo = InMemoryOrchestratorRepository.from_schema_sql()
    else:
        raise RuntimeError(
            "DATABASE_URL or DB_HOST is required unless GODVIEW_ENV=hermetic "
            "or USE_IN_MEMORY_DB=true (Build Decision §B6 fail-loudly)"
        )

    if downstreams is not None:
        resolved_downstreams = downstreams
    elif (
        isinstance(resolved_repo, InMemoryOrchestratorRepository)
        and not resolved_settings.database_url
        and (env_mode == "hermetic" or resolved_settings.use_in_memory_db)
    ):
        resolved_downstreams = MockDownstreamServiceBundle(repo=resolved_repo)
    else:
        resolved_downstreams = HttpDownstreamServiceBundle(resolved_settings)

    state = OrchestratorRuntimeState(
        settings=resolved_settings,
        repository=resolved_repo,
        downstreams=resolved_downstreams,
    )

    _configure_exporters = getattr(_otel_mod, "configure_exporters", None)
    if callable(_configure_exporters):
        _configure_exporters(resolved_settings.service_name)

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        _lifespan_configure = getattr(_otel_mod, "configure_exporters", None)
        if callable(_lifespan_configure):
            _lifespan_configure(resolved_settings.service_name)
        await state.repository.initialize_schema()
        yield

    app = FastAPI(
        title="Cymbal Auto Finance — Loan Orchestrator API",
        version="0.1.0",
        description=(
            "Journey coordinator for the 5-utility Cymbal Auto Finance vehicle loan "
            "origination platform (KYC -> LOS -> Fleet Credit -> Insurance -> Core Banking CBS)."
        ),
        lifespan=_lifespan,
    )
    app.state.orchestrator = state

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(_req: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(DownstreamServiceError)
    async def _downstream_service_error_handler(
        _req: Request, exc: DownstreamServiceError
    ) -> JSONResponse:
        status_code = exc.status_code if 400 <= exc.status_code < 600 else 502
        logger.warning(
            "Downstream utility %s returned HTTP %s: %s",
            exc.step,
            exc.status_code,
            exc.detail,
        )
        return JSONResponse(
            status_code=status_code,
            content={
                "code": "DOWNSTREAM_SERVICE_ERROR",
                "step": exc.step,
                "status_code": exc.status_code,
                "detail": exc.detail,
            },
        )

    @app.middleware("http")
    async def _prometheus_metrics_middleware(request: Request, call_next: Any) -> Response:
        start = time.perf_counter()
        response: Response = await call_next(request)
        duration = time.perf_counter() - start
        HTTP_REQUESTS_TOTAL.labels(
            service=resolved_settings.service_name,
            method=request.method,
            route=request.url.path,
            status=str(response.status_code),
        ).inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(
            service=resolved_settings.service_name,
            method=request.method,
            route=request.url.path,
        ).observe(duration)
        return response

    @app.get("/healthz", tags=["internal"])
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": resolved_settings.service_name,
            "paused": state.paused,
            "timestamp": utc_now().isoformat(),
        }

    @app.get("/metrics", tags=["internal"])
    async def metrics() -> Response:
        payload_bytes, content_type = render_prometheus_metrics()
        return Response(content=payload_bytes, media_type=content_type)

    # Build Decision A18: Mount public router under BOTH /v1 and /api/v1
    public_router = _build_public_router(state)
    app.include_router(public_router, prefix="/v1")
    app.include_router(public_router, prefix="/api/v1")

    # Admin API under /admin/v1 (TDD §3.3)
    admin_router = _build_admin_router(state)
    app.include_router(admin_router)

    return app


class _LazyASGIApp:
    """Lazy ASGI proxy so importing `app.main` does not instantiate `create_app()` prematurely."""

    def __init__(self) -> None:
        self._instance: FastAPI | None = None

    def _get(self) -> FastAPI:
        if self._instance is None:
            self._instance = create_app()
        return self._instance

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        await self._get()(scope, receive, send)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get(), name)


app: Any = _LazyASGIApp()
