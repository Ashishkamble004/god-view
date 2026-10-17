"""FastAPI application for Utility 4: motor-insurance-svc (Cloud Run port 8080)."""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from godview_shared import otel as _otel
from godview_shared.otel import (
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
    extract_baggage_context,
    get_tracer,
    inject_baggage_headers,
    render_prometheus_metrics,
    set_business_span_attributes,
)

from motor_insurance.models import (
    InsuranceIssueRequest,
    InsuranceIssueResponse,
)
from motor_insurance.repository import (
    InsuranceRepositoryProtocol,
    create_repository_from_env,
)
from motor_insurance.service import (
    LoanNotApprovedForInsuranceError,
    LoanNotFoundForInsuranceError,
    MotorInsuranceService,
)

SERVICE_NAME = "motor-insurance-svc"


def _extract_trace_id(traceparent: str | None) -> str | None:
    if not traceparent:
        return None
    parts = traceparent.strip().split("-")
    if len(parts) >= 2 and len(parts[1]) == 32:
        return parts[1]
    return traceparent


def create_app(repository: InsuranceRepositoryProtocol | None = None) -> FastAPI:
    getattr(_otel, "configure_exporters", lambda *a, **k: None)(SERVICE_NAME)
    repo = repository if repository is not None else create_repository_from_env()
    svc = MotorInsuranceService(repo)
    tracer = get_tracer(SERVICE_NAME)

    application = FastAPI(
        title="Cymbal Auto Finance — Motor Insurance & FastTag Add-On Service (Utility 4)",
        version="4.0.0",
        description="Zero-depreciation motor insurance & FastTag wallet linkage on Cloud Run port 8080.",
    )
    application.state.repository = repo
    application.state.service = svc
    application.state.recorded_spans = []

    @application.middleware("http")
    async def prometheus_middleware(request: Request, call_next: Any) -> Response:
        start = time.perf_counter()
        response: Response = await call_next(request)
        elapsed = time.perf_counter() - start
        route = request.url.path
        HTTP_REQUESTS_TOTAL.labels(
            service=SERVICE_NAME,
            method=request.method,
            route=route,
            status=str(response.status_code),
        ).inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(
            service=SERVICE_NAME,
            method=request.method,
            route=route,
        ).observe(elapsed)
        return response

    @application.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "service": SERVICE_NAME, "port": 8080}

    @application.get("/metrics")
    async def metrics() -> Response:
        payload, content_type = render_prometheus_metrics()
        return Response(content=payload, media_type=content_type)

    @application.post(
        "/internal/insurance/issue",
        response_model=InsuranceIssueResponse,
    )
    async def issue_insurance_policy(
        req: InsuranceIssueRequest,
        request: Request,
        response: Response,
    ) -> InsuranceIssueResponse | JSONResponse:
        bag_ctx = extract_baggage_context(request.headers)
        chaos_header = request.headers.get("x-chaos-fault") or request.headers.get("x-chaos-flags")
        journey_id = req.journey_id or bag_ctx.get("journey.id")
        customer_id = str(req.customer_id) if req.customer_id else bag_ctx.get("customer.id")
        loan_id = str(req.loan_id)
        synthetic = bool(bag_ctx.get("synthetic", False))
        traceparent = bag_ctx.get("traceparent")
        trace_id = _extract_trace_id(traceparent) or req.trace_id

        with tracer.start_as_current_span("motor_insurance.issue") as span:
            if not trace_id:
                span_ctx = span.get_span_context()
                if span_ctx and span_ctx.trace_id:
                    trace_id = f"{span_ctx.trace_id:032x}"

            try:
                result, prev_loan_status, policy_persisted = svc.issue_policy(
                    req, trace_id=trace_id, chaos_header=chaos_header
                )
            except LoanNotFoundForInsuranceError as exc:
                return JSONResponse(
                    status_code=409,
                    content={"code": "LOAN_NOT_FOUND", "detail": str(exc)},
                )
            except LoanNotApprovedForInsuranceError as exc:
                attrs = set_business_span_attributes(
                    span,
                    journey_id=journey_id,
                    customer_id=customer_id,
                    loan_id=loan_id,
                    decision="REJECTED",
                    status_from=exc.observed_status,
                    status_to=exc.observed_status,
                    event_type="insurance.rejected",
                    entity="insurance_policy",
                    synthetic=synthetic,
                )
                application.state.recorded_spans.append(
                    {"name": "motor_insurance.issue", "attributes": attrs}
                )
                return JSONResponse(
                    status_code=409,
                    content={"code": "LOAN_NOT_APPROVED", "detail": str(exc)},
                )

            if policy_persisted:
                attrs = set_business_span_attributes(
                    span,
                    journey_id=journey_id,
                    customer_id=customer_id,
                    loan_id=loan_id,
                    decision="ISSUED",
                    status_from=prev_loan_status,
                    status_to="INSURED",
                    event_type="insurance.issued",
                    amount_inr=float(result.premium_inr),
                    entity="insurance_policy",
                    entity_id=str(result.policy_id),
                    synthetic=synthetic,
                )
            else:
                # UC-05 silent-failure drill: infra view green (HTTP 200 ACTIVE), no policy row written,
                # and no business event emitted on the span (drill marker stays only in chaos_fault_states).
                attrs = {}
            application.state.recorded_spans.append(
                {"name": "motor_insurance.issue", "attributes": attrs}
            )

        outbound = inject_baggage_headers(
            journey_id=str(journey_id) if journey_id else None,
            customer_id=str(customer_id) if customer_id else None,
            synthetic=synthetic,
            traceparent=traceparent,
        )
        for k, v in outbound.items():
            response.headers[k] = v

        return result

    return application


def _create_default_app() -> FastAPI:
    return create_app()


app = _create_default_app()
