"""FastAPI application for Utility 5: core-banking-cibil-vm (port 8085)."""

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

from cibil_cbs.models import (
    DisburseRequest,
    DisburseResponse,
    ScoreRequest,
    ScoreResponse,
)
from cibil_cbs.repository import (
    CbsRepositoryProtocol,
    create_repository_from_env,
)
from cibil_cbs.service import CbsGatewayTimeoutError, CibilCbsService

SERVICE_NAME = "core-banking-cibil-vm"


def _apply_propagation_headers(
    response: Response,
    baggage_ctx: dict[str, Any],
    *,
    journey_id: str | None = None,
    customer_id: str | None = None,
) -> None:
    eff_journey = journey_id or baggage_ctx.get("journey.id")
    eff_customer = customer_id or baggage_ctx.get("customer.id")
    eff_syn = bool(baggage_ctx.get("synthetic", False))
    eff_tp = baggage_ctx.get("traceparent")
    outbound = inject_baggage_headers(
        journey_id=str(eff_journey) if eff_journey else None,
        customer_id=str(eff_customer) if eff_customer else None,
        synthetic=eff_syn,
        traceparent=eff_tp,
    )
    for k, v in outbound.items():
        response.headers[k] = v


def create_app(repository: CbsRepositoryProtocol | None = None) -> FastAPI:
    getattr(_otel, "configure_exporters", lambda *a, **k: None)(SERVICE_NAME)
    repo = repository if repository is not None else create_repository_from_env()
    svc = CibilCbsService(repo)
    tracer = get_tracer(SERVICE_NAME)

    application = FastAPI(
        title="Cymbal Auto Finance — Core Banking & CIBIL VM Service (Utility 5)",
        version="4.0.0",
        description="Credit bureau CIBIL pull (300-900), sanction letter, NACH e-Mandate, and idempotent CBS UTR settlement on GCE VM port 8085.",
    )
    application.state.repository = repo
    application.state.service = svc
    application.state.recorded_spans = []

    @application.middleware("http")
    async def prometheus_and_baggage_middleware(request: Request, call_next: Any) -> Response:
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
        return {"status": "ok", "service": SERVICE_NAME, "port": 8085}

    @application.get("/metrics")
    async def metrics() -> Response:
        payload, content_type = render_prometheus_metrics()
        return Response(content=payload, media_type=content_type)

    @application.post("/internal/cibil-cbs/score", response_model=ScoreResponse)
    async def evaluate_cibil_score(
        req: ScoreRequest,
        request: Request,
        response: Response,
    ) -> ScoreResponse:
        bag_ctx = extract_baggage_context(request.headers)
        chaos_header = request.headers.get("x-chaos-fault") or request.headers.get("x-chaos-flags")
        journey_id = req.journey_id or bag_ctx.get("journey.id")
        customer_id = str(req.customer_id) if req.customer_id else bag_ctx.get("customer.id")
        loan_id = str(req.loan_id) if req.loan_id else None
        synthetic = bool(bag_ctx.get("synthetic", False))

        with tracer.start_as_current_span("cibil.score") as span:
            result = svc.evaluate_score(req, chaos_header=chaos_header)
            attrs = set_business_span_attributes(
                span,
                journey_id=journey_id,
                customer_id=customer_id,
                loan_id=loan_id,
                cibil_score=result.cibil_score,
                event_type="cibil.scored",
                entity="underwriting_audit",
                entity_id=result.cibil_bureau_ref,
                synthetic=synthetic,
            )
            application.state.recorded_spans.append({"name": "cibil.score", "attributes": attrs})

        _apply_propagation_headers(
            response, bag_ctx, journey_id=journey_id, customer_id=customer_id
        )
        return result

    async def _handle_disburse(
        req: DisburseRequest,
        request: Request,
        response: Response,
        span_name: str,
    ) -> DisburseResponse | JSONResponse:
        bag_ctx = extract_baggage_context(request.headers)
        chaos_header = request.headers.get("x-chaos-fault") or request.headers.get("x-chaos-flags")
        journey_id = req.journey_id or bag_ctx.get("journey.id")
        customer_id = str(req.customer_id) if req.customer_id else bag_ctx.get("customer.id")
        loan_id = str(req.loan_id)
        synthetic = bool(bag_ctx.get("synthetic", False))

        with tracer.start_as_current_span(span_name) as span:
            try:
                result, prev_status = await svc.disburse(req, chaos_header=chaos_header)
            except CbsGatewayTimeoutError as exc:
                attrs = set_business_span_attributes(
                    span,
                    journey_id=journey_id,
                    customer_id=customer_id,
                    loan_id=loan_id,
                    decision="FAILED",
                    status_from="PENDING",
                    status_to=exc.row.status.value,
                    event_type="disbursement.timeout",
                    amount_inr=float(exc.row.disbursed_amount_inr),
                    entity="disbursement",
                    entity_id=str(exc.row.disbursement_id),
                    synthetic=synthetic,
                )
                application.state.recorded_spans.append({"name": span_name, "attributes": attrs})
                err_resp = JSONResponse(
                    status_code=504,
                    content={
                        "detail": exc.message,
                        "loan_id": str(exc.row.loan_id),
                        "disbursement_id": str(exc.row.disbursement_id),
                        "status": exc.row.status.value,
                        "attempts": exc.row.attempts,
                    },
                )
                _apply_propagation_headers(
                    err_resp, bag_ctx, journey_id=journey_id, customer_id=customer_id
                )
                return err_resp

            attrs = set_business_span_attributes(
                span,
                journey_id=journey_id,
                customer_id=customer_id,
                loan_id=loan_id,
                decision="DISBURSED",
                cibil_score=result.cibil_score,
                status_from=prev_status,
                status_to=result.status.value,
                event_type="disbursement.settled",
                amount_inr=float(result.disbursed_amount_inr),
                entity="disbursement",
                entity_id=str(result.disbursement_id),
                synthetic=synthetic,
            )
            application.state.recorded_spans.append({"name": span_name, "attributes": attrs})

        _apply_propagation_headers(
            response, bag_ctx, journey_id=journey_id, customer_id=customer_id
        )
        return result

    @application.post("/internal/cibil-cbs/disburse", response_model=DisburseResponse)
    async def disburse_loan(
        req: DisburseRequest,
        request: Request,
        response: Response,
    ) -> DisburseResponse | JSONResponse:
        return await _handle_disburse(req, request, response, "cibil_cbs.disburse")

    @application.post("/internal/cibil-cbs/score-and-disburse", response_model=DisburseResponse)
    async def score_and_disburse_loan(
        req: DisburseRequest,
        request: Request,
        response: Response,
    ) -> DisburseResponse | JSONResponse:
        return await _handle_disburse(req, request, response, "cibil_cbs.score_and_disburse")

    @application.get("/internal/cibil-cbs/disbursements")
    async def list_disbursements(status: str | None = None) -> dict[str, Any]:
        statuses = [s.strip() for s in status.split(",") if s.strip()] if status else None
        rows = repo.list_disbursements(statuses=statuses)
        return {"disbursements": [r.model_dump(mode="json") for r in rows]}

    @application.get("/internal/cibil-cbs/disbursements/{loan_id}")
    async def get_disbursement(loan_id: str) -> Response:
        row = repo.get_by_loan_id(loan_id)
        if row is None:
            return JSONResponse(status_code=404, content={"detail": f"No disbursement for loan_id={loan_id}"})
        return JSONResponse(status_code=200, content=row.model_dump(mode="json"))

    return application


def _create_default_app() -> FastAPI:
    return create_app()


app = _create_default_app()
