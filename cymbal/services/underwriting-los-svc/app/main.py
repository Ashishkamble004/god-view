"""FastAPI application for Utility 2: underwriting-los-svc (GKE Autopilot).

Endpoints (TDD §3.2):
- POST /internal/underwriting/evaluate
- GET  /metrics
- GET  /healthz
- POST /internal/chaos/faults
- DELETE /internal/chaos/faults/{fault_type}
- GET  /internal/chaos/faults
  (Protected by GKE NetworkPolicy to VPC subnet `10.10.0.0/20` and Google OIDC ID token
  verification for `godview-runtime-sa` / `godview-act-sa`).
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from godview_shared import otel as otel_mod
from godview_shared.contracts import TENANT_BUSINESS_TABLES
from godview_shared.otel import (
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
    METRICS_REGISTRY,
    extract_baggage_context,
    get_tracer,
    render_prometheus_metrics,
    set_business_span_attributes,
)
from prometheus_client import Counter

from .chaos import (
    ChaosAuthError,
    ChaosFaultManager,
    ChaosLockAcquisitionError,
    verify_chaos_caller,
)
from .cibil_client import CibilScoreClient
from .models import (
    ChaosFaultActivateRequest,
    UnderwritingEvaluateRequest,
    UnderwritingEvaluateResponse,
)
from .rules_engine import (
    MAX_FOIR_PCT,
    MIN_CIBIL_SCORE,
    RULES_VERSION,
    evaluate_underwriting_rules,
)

logger = logging.getLogger("underwriting-los-svc")

LOS_DECISION_TOTAL = Counter(
    "cymbal_underwriting_decisions_total",
    "Total underwriting decisions emitted by underwriting-los-svc",
    labelnames=("decision", "synthetic"),
    registry=METRICS_REGISTRY,
)


def _mask_pan(pan: str) -> str:
    """Mask PAN so raw PII never appears in logs (UC-13)."""
    cleaned = pan.strip().upper()
    if len(cleaned) <= 4:
        return "******"
    return f"******{cleaned[-4:]}"


def create_app(
    *,
    cibil_client: CibilScoreClient | None = None,
    chaos_manager: ChaosFaultManager | None = None,
    span_recorder: list[dict[str, Any]] | None = None,
) -> FastAPI:
    """Create and configure the underwriting-los-svc FastAPI application."""
    getattr(otel_mod, "configure_exporters", lambda *a, **k: None)("underwriting-los-svc")

    app = FastAPI(
        title="Cymbal Underwriting LOS Service",
        version="0.1.0",
        description="Utility 2: Deterministic EMI/FOIR/LTV & CIBIL Underwriting Engine on GKE Autopilot.",
    )

    active_cibil = cibil_client or CibilScoreClient()
    active_chaos = chaos_manager or ChaosFaultManager(service_name="underwriting-los-svc")

    app.state.cibil_client = active_cibil
    app.state.chaos_manager = active_chaos
    app.state.span_recorder = span_recorder

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "underwriting-los-svc",
            "rules_version": RULES_VERSION,
            "min_cibil_score": MIN_CIBIL_SCORE,
            "max_foir_pct": float(MAX_FOIR_PCT),
            "audit_table": "underwriting_audits"
            if "underwriting_audits" in TENANT_BUSINESS_TABLES
            else "underwriting_audits",
            "active_faults": active_chaos.active_faults(),
        }

    @app.get("/metrics")
    async def metrics() -> Response:
        payload, content_type = render_prometheus_metrics()
        return Response(content=payload, media_type=content_type)

    @app.post(
        "/internal/chaos/faults",
        response_model=None,
        openapi_extra={
            "x-godview-admin-action": "chaos.activate",
            "x-godview-reversal": {
                "capability": "chaos.clear",
                "endpoint": "DELETE /internal/chaos/faults/{fault_type}",
            },
        },
    )
    async def activate_chaos_fault(
        req: ChaosFaultActivateRequest,
        request: Request,
    ) -> JSONResponse | dict[str, Any]:
        try:
            verify_chaos_caller(
                dict(request.headers),
                token_verifier=active_chaos.token_verifier,
            )
        except ChaosAuthError as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"error_code": "CHAOS_UNAUTHORIZED", "detail": str(exc)},
            )

        try:
            return active_chaos.activate(
                req.fault_type,
                req.duration_seconds,
                req.parameters_json,
                fault_id=req.fault_id,
                triggered_by=req.triggered_by,
            )
        except ChaosLockAcquisitionError as exc:
            return JSONResponse(
                status_code=500,
                content={
                    "error_code": "CHAOS_LOCK_ACQUISITION_FAILED",
                    "fault_type": req.fault_type,
                    "status": "failed",
                    "active": False,
                    "detail": str(exc),
                },
            )

    @app.delete(
        "/internal/chaos/faults/{fault_type}",
        response_model=None,
        openapi_extra={
            "x-godview-admin-action": "chaos.clear",
            "x-godview-reversal": {
                "capability": "chaos.activate",
                "endpoint": "POST /internal/chaos/faults",
            },
        },
    )
    async def deactivate_chaos_fault(
        fault_type: str,
        request: Request,
    ) -> JSONResponse | dict[str, Any]:
        try:
            verify_chaos_caller(
                dict(request.headers),
                token_verifier=active_chaos.token_verifier,
            )
        except ChaosAuthError as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"error_code": "CHAOS_UNAUTHORIZED", "detail": str(exc)},
            )

        return active_chaos.deactivate(fault_type)

    @app.get("/internal/chaos/faults")
    async def list_chaos_faults() -> dict[str, Any]:
        return {
            "service": "underwriting-los-svc",
            "active_faults": active_chaos.active_faults(),
        }

    @app.post("/internal/underwriting/evaluate", response_model=UnderwritingEvaluateResponse)
    @app.post(
        "/api/internal/underwriting/evaluate",
        response_model=UnderwritingEvaluateResponse,
        include_in_schema=False,
    )
    async def evaluate_underwriting(
        payload: UnderwritingEvaluateRequest,
        request: Request,
    ) -> JSONResponse | UnderwritingEvaluateResponse:
        start_time = time.perf_counter()
        headers_dict = dict(request.headers)
        baggage_ctx = extract_baggage_context(headers_dict)
        has_header_or_baggage_synthetic = bool(baggage_ctx.get("synthetic"))

        journey_id = payload.journey_id or baggage_ctx.get("journey.id") or str(uuid.uuid4())
        customer_id = payload.customer_id or baggage_ctx.get("customer.id") or "cust-unknown"
        is_synthetic = bool(payload.synthetic or has_header_or_baggage_synthetic)
        pod_name = os.environ.get("HOSTNAME", "underwriting-los-svc-pod")

        # UC-15: GKE Pod OOM / 503 Exhaustion chaos fault hook
        header_fault = headers_dict.get("x-chaos-fault")
        if active_chaos.is_active(
            "gke_oom_503",
            header_fault=header_fault,
            body_fault=payload.chaos_fault,
        ):
            logger.error(
                "UC-15 chaos fault gke_oom_503 active on underwriting-los-svc",
                extra={
                    "service": "underwriting-los-svc",
                    "fault_type": "gke_oom_503",
                    "gke_pod_name": pod_name,
                    "gke_namespace": "cymbal",
                    "journey.id": journey_id,
                    "customer.id": customer_id,
                    "loan.id": payload.loan_id,
                    "synthetic": is_synthetic,
                },
            )
            HTTP_REQUESTS_TOTAL.labels(
                service="underwriting-los-svc",
                method="POST",
                route="/internal/underwriting/evaluate",
                status="503",
            ).inc()
            return JSONResponse(
                status_code=503,
                content={
                    "error_code": "GKE_OOM_503",
                    "fault_type": "gke_oom_503",
                    "service": "underwriting-los-svc",
                    "gke_pod_name": pod_name,
                    "gke_namespace": "cymbal",
                    "message": "Pod memory limit (512Mi) exceeded during underwriting evaluation (UC-15).",
                },
            )

        # Finding #2: Caller-supplied cibil_score override is ONLY permitted when X-Synthetic / baggage synthetic=true
        if payload.cibil_score is not None and not has_header_or_baggage_synthetic:
            return JSONResponse(
                status_code=422,
                content={
                    "error_code": "SYNTHETIC_HEADER_REQUIRED_FOR_CIBIL_OVERRIDE",
                    "detail": (
                        "Caller-supplied cibil_score override is only permitted for synthetic probes "
                        "carrying X-Synthetic: true or W3C baggage synthetic=true."
                    ),
                },
            )

        tracer = get_tracer("underwriting-los-svc")
        with tracer.start_as_current_span("underwriting.evaluate") as span:
            if payload.cibil_score is not None:
                cibil_score = int(payload.cibil_score)
                cibil_bureau_ref = payload.cibil_bureau_ref or f"CIBIL-REF-{payload.loan_id[:8]}"
                gce_vm_name = "core-banking-cibil-vm"
            else:
                cbs_res = active_cibil.fetch_score(
                    loan_id=payload.loan_id,
                    pan_number=payload.pan_number,
                    customer_id=customer_id,
                    journey_id=journey_id,
                    monthly_income_inr=payload.monthly_income_inr,
                    loan_amount_inr=payload.loan_amount_inr,
                    synthetic=is_synthetic,
                    inbound_headers=headers_dict,
                )
                cibil_score = cbs_res.cibil_score
                cibil_bureau_ref = cbs_res.cibil_bureau_ref
                gce_vm_name = cbs_res.gce_vm_name

            eval_res = evaluate_underwriting_rules(
                loan_amount_inr=payload.loan_amount_inr,
                down_payment_inr=payload.down_payment_inr,
                ex_showroom_inr=payload.ex_showroom_inr,
                tenure_months=payload.tenure_months,
                interest_rate_pct=payload.interest_rate_pct,
                monthly_income_inr=payload.monthly_income_inr,
                existing_emi_inr=payload.existing_emi_inr,
                cibil_score=cibil_score,
            )

            status_to = "APPROVED" if eval_res.decision == "APPROVED" else "REJECTED"
            span_attrs = set_business_span_attributes(
                span,
                journey_id=str(journey_id),
                customer_id=str(customer_id),
                loan_id=str(payload.loan_id),
                decision=eval_res.decision,
                foir_pct=float(eval_res.foir_pct),
                cibil_score=int(eval_res.cibil_score),
                status_from="KYC_VERIFIED",
                status_to=status_to,
                event_type="loan.decided",
                emi_inr=float(eval_res.emi_inr),
                amount_inr=float(payload.loan_amount_inr),
                entity="loan_application",
                entity_id=str(payload.loan_id),
                synthetic=is_synthetic,
                extra={
                    "ltv_pct": float(eval_res.ltv_pct),
                    "rules_version": eval_res.rules_version,
                    "gke_pod_name": pod_name,
                    "gce_vm_name": gce_vm_name,
                    "gke_namespace": "cymbal",
                },
            )
            if span_recorder is not None:
                span_recorder.append(span_attrs)

            masked_pan = _mask_pan(payload.pan_number)
            logger.info(
                "Underwriting evaluation completed: decision=%s cibil=%d foir=%.2f%% emi=%.2f pan=%s",
                eval_res.decision,
                eval_res.cibil_score,
                float(eval_res.foir_pct),
                float(eval_res.emi_inr),
                masked_pan,
                extra=span_attrs,
            )

            elapsed = time.perf_counter() - start_time
            LOS_DECISION_TOTAL.labels(
                decision=eval_res.decision,
                synthetic=str(is_synthetic).lower(),
            ).inc()
            HTTP_REQUESTS_TOTAL.labels(
                service="underwriting-los-svc",
                method="POST",
                route="/internal/underwriting/evaluate",
                status="200",
            ).inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(
                service="underwriting-los-svc",
                method="POST",
                route="/internal/underwriting/evaluate",
            ).observe(elapsed)

            return UnderwritingEvaluateResponse(
                audit_id=str(uuid.uuid4()),
                loan_id=payload.loan_id,
                status=eval_res.decision,  # type: ignore[arg-type]
                decision=eval_res.decision,  # type: ignore[arg-type]
                emi_inr=float(eval_res.emi_inr),
                foir_pct=float(eval_res.foir_pct),
                ltv_pct=float(eval_res.ltv_pct),
                cibil_score=eval_res.cibil_score,
                cibil_bureau_ref=cibil_bureau_ref,
                rules_version=eval_res.rules_version,
                gke_pod_name=pod_name,
                gce_vm_name=gce_vm_name,
                rejection_reason=eval_res.rejection_reason,
                event_type="loan.decided",
                evaluated_at=datetime.now(UTC),
            )

    return app


app = create_app()
