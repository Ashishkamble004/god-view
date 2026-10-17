"""FastAPI application for Cymbal Auto Finance AI Customer Stream Simulator."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from godview_shared import otel as otel_mod
from godview_shared.otel import (
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
    render_prometheus_metrics,
)
from pydantic import BaseModel, Field, field_validator

from app.config import SimulatorConfig
from app.identities import is_checksum_invalid_pan
from app.personas import (
    HermeticVertexTransport,
    LiveVertexRestTransport,
    VertexModelTransport,
    VertexPersonaClient,
)
from app.simulator import (
    DailyJourneyCapExceededError,
    GcsSimulatorStateStore,
    HttpOrchestratorClient,
    InMemoryOrchestratorClient,
    JourneyLaunchResult,
    SimulatorDisabledError,
    SimulatorEngine,
    SimulatorStateStore,
    get_shared_hermetic_state_store,
)
from app.specimen_cards import (
    GcsHttpSpecimenStorage,
    InMemorySpecimenStorage,
    VertexSpecimenCardGenerator,
)


class TickRequest(BaseModel):
    """Optional parameters for a Cloud Scheduler or hermetic test tick."""

    now: datetime | None = None
    random_sample: float | None = Field(default=None, ge=0.0, le=1.0)
    force_window: bool = False


class SimulatorStateRequest(BaseModel):
    """Request body for POST /v1/simulator/state."""

    enabled: bool
    reason: str | None = None


class JourneyBurstRequest(BaseModel):
    """Request body for POST /v1/simulator/journeys."""

    count: int = Field(default=1, ge=1, le=50)
    mode: Literal["normal", "adversarial", "duplicate_pan"] = "normal"
    adversarial: bool = False
    duplicate_pan: bool = False
    pan_number: str | None = None
    now: datetime | None = None

    @field_validator("pan_number")
    @classmethod
    def validate_synthetic_pan(cls, value: str | None) -> str | None:
        if value is not None and not is_checksum_invalid_pan(value):
            raise ValueError(
                f"pan_number {value!r} must be a synthetic checksum-invalid PAN "
                "(invalid 4th-char holder class and mismatched mod-26 check character)"
            )
        return value


def build_default_engine(
    config: SimulatorConfig | None = None,
    *,
    state_store: SimulatorStateStore | None = None,
) -> SimulatorEngine:
    """Construct SimulatorEngine from environment settings."""
    cfg = config or SimulatorConfig.from_env()
    if cfg.env_mode == "hermetic":
        transport: VertexModelTransport = HermeticVertexTransport()
        storage: Any = InMemorySpecimenStorage(bucket_name=cfg.kyc_vault_bucket)
        orch: Any = InMemoryOrchestratorClient(base_url=cfg.api_base_url)
        store: SimulatorStateStore = state_store or get_shared_hermetic_state_store(
            cfg.kyc_vault_bucket,
            cfg.state_object_name,
        )
    else:
        transport = LiveVertexRestTransport(project_id=cfg.gcp_project_id)
        storage = GcsHttpSpecimenStorage(bucket_name=cfg.kyc_vault_bucket)
        orch = HttpOrchestratorClient(base_url=cfg.api_base_url)
        store = state_store or GcsSimulatorStateStore(
            bucket_name=cfg.kyc_vault_bucket,
            object_name=cfg.state_object_name,
        )

    return SimulatorEngine(
        config=cfg,
        persona_client=VertexPersonaClient(transport=transport),
        card_generator=VertexSpecimenCardGenerator(transport=transport, storage=storage),
        orchestrator_client=orch,
        state_store=store,
    )


def _configure_otel_exporters() -> None:
    fn = getattr(otel_mod, "configure_exporters", None)
    if callable(fn):
        fn("ai-customer-simulator")


def create_app(engine: SimulatorEngine | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    _configure_otel_exporters()
    sim_engine = engine if engine is not None else build_default_engine()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        _configure_otel_exporters()
        yield

    app = FastAPI(
        title="Cymbal Auto Finance — AI Customer Stream Simulator",
        version="4.0.0",
        description="Synthetic Indian borrower stream with watermarked specimen KYC cards.",
        lifespan=lifespan,
    )
    app.state.engine = sim_engine

    @app.middleware("http")
    async def prometheus_middleware(request: Request, call_next: Any) -> Response:
        start = time.perf_counter()
        response: Response = await call_next(request)
        elapsed = time.perf_counter() - start
        route = request.url.path
        HTTP_REQUESTS_TOTAL.labels(
            service="ai-customer-simulator",
            method=request.method,
            route=route,
            status=str(response.status_code),
        ).inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(
            service="ai-customer-simulator",
            method=request.method,
            route=route,
        ).observe(elapsed)
        return response

    router = APIRouter()

    def _reject_test_knobs_outside_hermetic(
        *,
        force_window: bool = False,
        now: datetime | None = None,
        random_sample: float | None = None,
    ) -> None:
        if sim_engine.config.env_mode != "hermetic" and (
            force_window or now is not None or random_sample is not None
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "TEST_OVERRIDES_FORBIDDEN_IN_PRODUCTION",
                    "message": "force_window, now, and random_sample are allowed only when GODVIEW_ENV=hermetic",
                },
            )

    @router.post("/tick")
    async def simulator_tick(body: TickRequest | None = None) -> dict[str, Any]:
        req = body or TickRequest()
        _reject_test_knobs_outside_hermetic(
            force_window=req.force_window,
            now=req.now,
            random_sample=req.random_sample,
        )
        try:
            result = await sim_engine.tick(
                now=req.now,
                random_sample=req.random_sample,
                force_window=req.force_window,
            )
        except Exception as exc:
            result = JourneyLaunchResult(
                launched=False,
                skipped_reason="orchestrator_error",
                orchestrator_status=f"ERROR: {str(exc)[:200]}",
                journeys_today=sim_engine.journeys_today,
                image_journeys_today=sim_engine.image_journeys_today,
            )
        return result.model_dump(mode="json")

    @router.post(
        "/state",
        openapi_extra={
            "x-godview-admin-action": "simulator.set_state",
            "x-godview-reversal": {
                "capability": "simulator.set_state",
                "method": "POST",
                "path": "/v1/simulator/state",
                "body": {"enabled": True},
            },
        },
    )
    async def set_simulator_state(body: SimulatorStateRequest) -> dict[str, Any]:
        return sim_engine.set_state(enabled=body.enabled, reason=body.reason)

    @router.post(
        "/journeys",
        openapi_extra={
            "x-godview-admin-action": "simulator.launch_journeys",
            "x-godview-reversal": {
                "capability": "simulator.set_state",
                "method": "POST",
                "path": "/v1/simulator/state",
                "body": {"enabled": False, "reason": "revert_burst"},
            },
        },
    )
    async def trigger_journeys(body: JourneyBurstRequest) -> dict[str, Any]:
        _reject_test_knobs_outside_hermetic(now=body.now)
        mode = body.mode
        if body.adversarial:
            mode = "adversarial"
        elif body.duplicate_pan:
            mode = "duplicate_pan"

        try:
            results = await sim_engine.launch_burst(
                count=body.count,
                mode=mode,
                pan_number=body.pan_number,
                now=body.now,
            )
        except SimulatorDisabledError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "SIMULATOR_DISABLED", "message": str(exc)},
            ) from exc
        except DailyJourneyCapExceededError as exc:
            raise HTTPException(
                status_code=429,
                detail={"code": "DAILY_JOURNEY_CAP_EXCEEDED", "message": str(exc)},
            ) from exc

        return {
            "requested_count": body.count,
            "launched_count": len(results),
            "mode": mode,
            "journeys": [r.model_dump(mode="json") for r in results],
        }

    @router.post(
        "/journeys/adversarial",
        openapi_extra={
            "x-godview-admin-action": "simulator.launch_adversarial_journeys",
            "x-godview-reversal": {
                "capability": "simulator.set_state",
                "method": "POST",
                "path": "/v1/simulator/state",
                "body": {"enabled": False, "reason": "revert_adversarial_burst"},
            },
        },
    )
    async def trigger_adversarial_journeys(
        body: JourneyBurstRequest | None = None,
    ) -> dict[str, Any]:
        req = body or JourneyBurstRequest(count=1, mode="adversarial")
        req.mode = "adversarial"
        return await trigger_journeys(req)

    @router.post(
        "/journeys/duplicate-pan",
        openapi_extra={
            "x-godview-admin-action": "simulator.launch_duplicate_pan_journeys",
            "x-godview-reversal": {
                "capability": "simulator.set_state",
                "method": "POST",
                "path": "/v1/simulator/state",
                "body": {"enabled": False, "reason": "revert_duplicate_pan_burst"},
            },
        },
    )
    async def trigger_duplicate_pan_journeys(
        body: JourneyBurstRequest | None = None,
    ) -> dict[str, Any]:
        req = body or JourneyBurstRequest(count=5, mode="duplicate_pan")
        req.mode = "duplicate_pan"
        return await trigger_journeys(req)

    @router.get("/status")
    async def get_simulator_status() -> dict[str, Any]:
        return sim_engine.status()

    # Double-mount under /v1/simulator and /api/v1/simulator (Decision A18)
    app.include_router(router, prefix="/v1/simulator", tags=["simulator"])
    app.include_router(router, prefix="/api/v1/simulator", tags=["simulator-same-origin"])

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "ai-customer-simulator",
            "enabled": sim_engine.enabled,
        }

    @app.get("/metrics")
    async def metrics() -> Response:
        HTTP_REQUESTS_TOTAL.labels(
            service="ai-customer-simulator",
            method="GET",
            route="/metrics",
            status="200",
        ).inc()
        payload, content_type = render_prometheus_metrics()
        return Response(content=payload, media_type=content_type)

    return app


app = create_app()
