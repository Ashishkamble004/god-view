"""FastAPI application for Utility 1: kyc-model-armor-svc (GKE Autopilot, port 8080).

Endpoints (TDD §3.2):
- POST /internal/kyc/verify
- GET /metrics
- GET /healthz
- POST /internal/chaos/faults, DELETE /internal/chaos/faults/{fault_type}, GET /internal/chaos/faults
  (Protected by GKE NetworkPolicy to VPC subnet `10.10.0.0/20` and Google OIDC ID token
  verification for `godview-runtime-sa` / `godview-act-sa`).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from godview_shared import otel as otel_mod
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
from .models import (
    ChaosFaultActivateRequest,
    KycMode,
    KycVerifyRequest,
    KycVerifyResponse,
)
from .scanner import (
    DEFAULT_MODEL_ARMOR_TEMPLATE_ID,
    ModelArmorRestScanner,
    TextScannerProtocol,
    mask_aadhaar,
    mask_pan,
    validate_identity_numbers,
    validate_specimen_image_metadata,
)

logger = logging.getLogger("kyc-model-armor-svc")

SERVICE_NAME = "kyc-model-armor-svc"

KYC_VERDICTS_TOTAL = Counter(
    "cymbal_kyc_verdicts_total",
    "Total KYC verification verdicts by event_type, kyc_mode, and synthetic flag",
    labelnames=("event_type", "kyc_mode", "synthetic"),
    registry=METRICS_REGISTRY,
)


def create_app(
    *,
    scanner: TextScannerProtocol | None = None,
    chaos_manager: ChaosFaultManager | None = None,
    span_recorder: list[dict[str, Any]] | None = None,
) -> FastAPI:
    getattr(otel_mod, "configure_exporters", lambda *a, **k: None)(SERVICE_NAME)

    app = FastAPI(
        title="Cymbal Auto Finance — KYC & Model Armor Guardrail Utility",
        version="4.0.0",
        description="Utility 1 on GKE Autopilot: validates PAN/Aadhaar, checks specimen PNG headers only, and scans applicant text fields with Model Armor cymbal-text-guard.",
    )
    active_scanner: TextScannerProtocol = scanner or ModelArmorRestScanner()
    active_chaos = chaos_manager or ChaosFaultManager(service_name=SERVICE_NAME)
    tracer = get_tracer(SERVICE_NAME)

    app.state.scanner = active_scanner
    app.state.chaos_manager = active_chaos
    app.state.span_recorder = span_recorder

    @app.middleware("http")
    async def prometheus_http_middleware(request: Request, call_next: Any) -> Response:
        t0 = time.monotonic()
        route = request.url.path
        method = request.method
        response: Response = await call_next(request)
        elapsed = time.monotonic() - t0
        HTTP_REQUESTS_TOTAL.labels(
            service=SERVICE_NAME,
            method=method,
            route=route,
            status=str(response.status_code),
        ).inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(
            service=SERVICE_NAME,
            method=method,
            route=route,
        ).observe(elapsed)
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        template_id = getattr(
            active_scanner,
            "template_id",
            os.environ.get("MODEL_ARMOR_TEMPLATE_ID", DEFAULT_MODEL_ARMOR_TEMPLATE_ID),
        )
        return {
            "status": "ok",
            "service": SERVICE_NAME,
            "namespace": os.environ.get("POD_NAMESPACE", "cymbal"),
            "pod_name": os.environ.get("HOSTNAME", "kyc-model-armor-svc-local"),
            "model_armor_template_id": template_id,
            "active_chaos_faults": active_chaos.list_active(),
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
            fault = active_chaos.activate(
                req.fault_type,
                duration_seconds=req.duration_seconds,
                parameters_json=req.parameters_json,
                triggered_by=req.triggered_by,
                fault_id=req.fault_id,
            )
        except ChaosLockAcquisitionError as exc:
            return JSONResponse(
                status_code=500,
                content={
                    "error_code": "CHAOS_LOCK_ACQUISITION_FAILED",
                    "fault_type": req.fault_type,
                    "status": "failed",
                    "is_active": False,
                    "detail": str(exc),
                },
            )
        return {
            "fault_id": fault.fault_id,
            "fault_type": fault.fault_type,
            "target_service": SERVICE_NAME,
            "status": fault.status,
            "is_active": True,
            "activated_at": fault.activated_at.isoformat(),
            "expires_at": fault.expires_at.isoformat(),
        }

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
    async def clear_chaos_fault(
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

        cleared = active_chaos.clear(fault_type)
        return {
            "fault_type": fault_type,
            "target_service": SERVICE_NAME,
            "cleared": cleared,
            "is_active": False,
        }

    @app.get("/internal/chaos/faults")
    async def list_chaos_faults() -> dict[str, Any]:
        return {
            "service": SERVICE_NAME,
            "active_faults": active_chaos.list_active(),
        }

    @app.post("/internal/kyc/verify", response_model=KycVerifyResponse)
    @app.post("/api/internal/kyc/verify", response_model=KycVerifyResponse, include_in_schema=False)
    async def verify_kyc(
        payload: KycVerifyRequest, request: Request
    ) -> Response | KycVerifyResponse:
        header_fault = request.headers.get("x-chaos-fault")
        if active_chaos.is_active(
            "gke_oom_503",
            header_fault=header_fault,
            body_fault=payload.chaos_fault,
        ):
            pod_name = os.environ.get("HOSTNAME", "kyc-model-armor-svc-pod-0")
            logger.error(
                "GKE Pod OOM / 503 exhaustion triggered on %s (pod=%s namespace=cymbal)",
                SERVICE_NAME,
                pod_name,
            )
            return JSONResponse(
                status_code=503,
                content={
                    "error_code": "GKE_OOM_503",
                    "service": SERVICE_NAME,
                    "namespace": os.environ.get("POD_NAMESPACE", "cymbal"),
                    "pod_name": pod_name,
                    "detail": "Container memory exhaustion / 503 unavailability (UC-15 chaos drill)",
                },
            )

        baggage_ctx = extract_baggage_context(dict(request.headers))
        journey_id = (
            payload.journey_id or baggage_ctx.get("journey.id") or f"jrn-{uuid4().hex[:12]}"
        )
        customer_id = str(payload.customer_id or baggage_ctx.get("customer.id") or uuid4())
        loan_id = str(payload.loan_id or uuid4())
        is_synthetic = bool(
            payload.synthetic
            or baggage_ctx.get("synthetic")
            or str(request.headers.get("x-synthetic", "")).lower() in ("true", "1", "yes")
        )

        masked_pan_str = mask_pan(payload.pan_number)
        masked_aadhaar_str = mask_aadhaar(payload.aadhaar_number)

        pan_ok, aadhaar_ok, id_errors = validate_identity_numbers(
            payload.pan_number,
            payload.aadhaar_number,
        )
        if not (pan_ok and aadhaar_ok):
            logger.warning(
                "KYC identity format validation failed for customer=%s pan=%s aadhaar=%s errors=%s",
                customer_id,
                masked_pan_str,
                masked_aadhaar_str,
                id_errors,
            )
            raise HTTPException(
                status_code=422,
                detail={
                    "error_code": "INVALID_IDENTITY_FORMAT",
                    "errors": id_errors,
                    "masked_pan": masked_pan_str,
                    "masked_aadhaar": masked_aadhaar_str,
                },
            )

        # SDD §8.1 / UC-25: Specimen PNGs in IMAGE mode undergo ONLY metadata & PNG magic header checks.
        # Image bytes are NEVER sent to Model Armor or any model.
        image_check = validate_specimen_image_metadata(
            kyc_mode=payload.kyc_mode,
            pan_gcs_uri=payload.pan_gcs_uri,
            aadhaar_gcs_uri=payload.aadhaar_gcs_uri,
            pan_image_bytes_b64=payload.pan_image_bytes_b64,
            aadhaar_image_bytes_b64=payload.aadhaar_image_bytes_b64,
        )
        if not image_check["valid"]:
            logger.warning(
                "Specimen image metadata/PNG header validation failed for customer=%s errors=%s",
                customer_id,
                image_check["errors"],
            )
            raise HTTPException(
                status_code=422,
                detail={
                    "error_code": "INVALID_SPECIMEN_PNG_METADATA",
                    "errors": image_check["errors"],
                    "image_check": image_check,
                },
            )

        with tracer.start_as_current_span("kyc.verify") as span:
            # Strictly pass ONLY the 3 text fields to the scanner (SDD §8.1)
            verdict = active_scanner.scan_text_fields(
                loan_purpose_text=payload.loan_purpose_text,
                employment_remarks=payload.employment_remarks,
                applicant_notes=payload.applicant_notes,
            )

            if verdict.blocked:
                event_type = "armor.blocked"
                loan_status = "MODEL_ARMOR_BLOCKED"
                text_status = "MODEL_ARMOR_BLOCKED"
            else:
                event_type = "kyc.verified"
                loan_status = "KYC_VERIFIED"
                text_status = "CLEAN"

            findings_json: dict[str, Any] = {
                **verdict.findings,
                "kyc_mode": str(payload.kyc_mode),
                "image_metadata_check": {
                    "pan_header_valid": image_check["pan_header_valid"],
                    "aadhaar_header_valid": image_check["aadhaar_header_valid"],
                    "images_sent_to_model_armor": False,
                },
            }

            span_attrs = set_business_span_attributes(
                span,
                journey_id=journey_id,
                customer_id=customer_id,
                loan_id=loan_id,
                status_from="KYC_PENDING",
                status_to=loan_status,
                event_type=event_type,
                entity="kyc_document",
                entity_id=str(payload.kyc_id or loan_id),
                synthetic=is_synthetic,
                extra={
                    "kyc.mode": str(payload.kyc_mode),
                    "model_armor.text_status": text_status,
                    "model_armor.blocked": verdict.blocked,
                },
            )
            if span_recorder is not None:
                span_recorder.append(span_attrs)

            KYC_VERDICTS_TOTAL.labels(
                event_type=event_type,
                kyc_mode=str(payload.kyc_mode),
                synthetic="true" if is_synthetic else "false",
            ).inc()

            kyc_id = str(payload.kyc_id or uuid4())
            now = datetime.now(UTC)

            logger.info(
                "KYC verdict completed journey_id=%s customer_id=%s loan_id=%s mode=%s pan=%s aadhaar=%s status=%s event_type=%s",
                journey_id,
                customer_id,
                loan_id,
                payload.kyc_mode,
                masked_pan_str,
                masked_aadhaar_str,
                loan_status,
                event_type,
            )

            return KycVerifyResponse(
                kyc_id=kyc_id,
                customer_id=customer_id,
                loan_id=loan_id,
                journey_id=journey_id,
                kyc_mode=KycMode(payload.kyc_mode),
                pan_gcs_uri=payload.pan_gcs_uri,
                aadhaar_gcs_uri=payload.aadhaar_gcs_uri,
                pan_image_model=payload.pan_image_model,
                aadhaar_image_model=payload.aadhaar_image_model,
                pan_verified=pan_ok and bool(image_check["pan_header_valid"]),
                aadhaar_verified=aadhaar_ok and bool(image_check["aadhaar_header_valid"]),
                model_armor_text_status=text_status,
                model_armor_findings_json=findings_json,
                verified_at=now,
                status=loan_status,
                event_type=event_type,
                blocked=verdict.blocked,
                masked_pan=masked_pan_str,
                masked_aadhaar=masked_aadhaar_str,
            )

    return app


app = create_app()
