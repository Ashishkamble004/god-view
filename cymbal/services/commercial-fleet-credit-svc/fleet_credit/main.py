"""FastAPI application for Utility 3: commercial-fleet-credit-svc (Cloud Run port 8080)."""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, Request, Response
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

from fleet_credit.models import (
    FleetCreditEvaluateRequest,
    FleetCreditEvaluateResponse,
)
from fleet_credit.repository import (
    FleetCreditRepositoryProtocol,
    create_repository_from_env,
)
from fleet_credit.service import FleetCreditService

SERVICE_NAME = "commercial-fleet-credit-svc"


def _extract_trace_id(traceparent: str | None) -> str | None:
    if not traceparent:
        return None
    parts = traceparent.strip().split("-")
    if len(parts) >= 2 and len(parts[1]) == 32:
        return parts[1]
    return traceparent


def create_app(repository: FleetCreditRepositoryProtocol | None = None) -> FastAPI:
    getattr(_otel, "configure_exporters", lambda *a, **k: None)(SERVICE_NAME)
    repo = repository if repository is not None else create_repository_from_env()
    svc = FleetCreditService(repo)
    tracer = get_tracer(SERVICE_NAME)

    application = FastAPI(
        title="Cymbal Auto Finance — Commercial Fleet & Two-Wheeler Credit Line Service (Utility 3)",
        version="4.0.0",
        description="Working-capital commercial fleet credit lines and two-wheeler micro-loans on Cloud Run port 8080.",
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
        "/internal/fleet-credit/evaluate",
        response_model=FleetCreditEvaluateResponse,
    )
    async def evaluate_fleet_credit(
        req: FleetCreditEvaluateRequest,
        request: Request,
        response: Response,
    ) -> FleetCreditEvaluateResponse:
        bag_ctx = extract_baggage_context(request.headers)
        journey_id = req.journey_id or bag_ctx.get("journey.id")
        customer_id = str(req.customer_id) if req.customer_id else bag_ctx.get("customer.id")
        loan_id = str(req.loan_id)
        synthetic = bool(bag_ctx.get("synthetic", False))
        traceparent = bag_ctx.get("traceparent")
        trace_id = _extract_trace_id(traceparent) or req.trace_id

        with tracer.start_as_current_span("fleet_credit.evaluate") as span:
            if not trace_id:
                span_ctx = span.get_span_context()
                if span_ctx and span_ctx.trace_id:
                    trace_id = f"{span_ctx.trace_id:032x}"

            result = svc.evaluate(req, trace_id=trace_id)
            attrs = set_business_span_attributes(
                span,
                journey_id=journey_id,
                customer_id=customer_id,
                loan_id=loan_id,
                decision=result.decision,
                cibil_score=req.cibil_score,
                status_from="APPROVED",
                status_to=result.status,
                event_type="credit_line.decided",
                amount_inr=float(
                    result.sanctioned_limit_inr
                    if result.decision == "APPROVED"
                    else result.requested_limit_inr
                ),
                entity="credit_line",
                entity_id=str(result.credit_line_id),
                synthetic=synthetic,
            )
            application.state.recorded_spans.append(
                {"name": "fleet_credit.evaluate", "attributes": attrs}
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
