"""Fictional Parcel Logistics Microservices for Unknown-Environment E2E.

Services:
1. Intake service: POST /v1/packages-{suffix}/intake, GET /v1/packages-{suffix}/{package_id}, GET /healthz
2. Route service: POST /v1/packages-{suffix}/route, GET /v1/packages-{suffix}/routes/{route_id}, GET /healthz
3. Dispatch service: POST /v1/packages-{suffix}/dispatch, GET /v1/packages-{suffix}/dispatches/{dispatch_id}, GET /healthz
4. Proof service: POST /v1/packages-{suffix}/proof, GET /v1/packages-{suffix}/proofs/{proof_id}, GET /healthz

Includes:
- OpenAPI specs automatically served at /openapi.json
- Synthetic probe-safe indicators
- Configurable fault injection (404 route, 5xx burst)
- OTLP telemetry reporting helper
- Dynamic randomisation support per test run
"""

from __future__ import annotations

import socket
import threading
import time
import uuid
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

# Global fault state dictionary: service_name -> fault_type (e.g. "404", "500")
_ACTIVE_FAULTS: dict[str, str | None] = {}


def set_service_fault(service_name: str, fault_type: str | None) -> None:
    """Inject or clear faults on a service (e.g. '404' or '500')."""
    _ACTIVE_FAULTS[service_name] = fault_type


def get_service_fault(service_name: str) -> str | None:
    return _ACTIVE_FAULTS.get(service_name)


# --- Request/Response Models ---


class ParcelIntakeRequest(BaseModel):
    parcel_id: str = Field(default_factory=lambda: f"PCL-{uuid.uuid4().hex[:6].upper()}")
    origin: str = "Hub-North"
    destination: str = "Depot-West"
    weight_kg: float = 2.5
    recipient: str = "Logistics Customer"


class ParcelIntakeResponse(BaseModel):
    parcel_id: str
    status: str = "RECEIVED"
    intake_timestamp: float = Field(default_factory=time.time)
    hub_id: str = "HUB-CENTRAL"


class TransitRouteRequest(BaseModel):
    parcel_id: str
    origin: str
    destination: str


class TransitRouteResponse(BaseModel):
    route_id: str = Field(default_factory=lambda: f"RT-{uuid.uuid4().hex[:6].upper()}")
    parcel_id: str
    carrier_code: str = "EXPRESS_GROUND"
    estimated_transit_hours: float = 5.0
    waypoints: list[str] = Field(default_factory=lambda: ["HUB-CENTRAL", "DEPOT-WEST"])


class CourierDispatchRequest(BaseModel):
    route_id: str
    courier_id: str = "CR-101"


class CourierDispatchResponse(BaseModel):
    dispatch_id: str = Field(default_factory=lambda: f"DSP-{uuid.uuid4().hex[:6].upper()}")
    route_id: str
    courier_id: str
    dispatch_status: str = "OUT_FOR_DELIVERY"
    dispatched_at: float = Field(default_factory=time.time)


class DeliveryProofRequest(BaseModel):
    parcel_id: str
    recipient_name: str = "Jane Doe"
    signature_hash: str = "sig_hash_valid_8823"


class DeliveryProofResponse(BaseModel):
    proof_id: str = Field(default_factory=lambda: f"PRF-{uuid.uuid4().hex[:6].upper()}")
    parcel_id: str
    verified: bool = True
    delivery_status: str = "DELIVERED"
    delivered_at: float = Field(default_factory=time.time)


# --- App Factories ---


def create_intake_app(*, suffix: str = "", route_service_url: str | None = None) -> FastAPI:
    """Create parcel intake FastAPI application."""
    svc_name = f"svc-pkg-intake-{suffix}" if suffix else "parcel-intake-svc"
    prefix = f"/v1/packages-{suffix}" if suffix else "/v1/parcels"
    app = FastAPI(
        title=svc_name,
        version="1.0.0",
        description="Fictional Parcel Logistics Intake Service",
    )

    @app.get("/")
    def root() -> dict[str, str]:
        fault = get_service_fault(svc_name)
        if fault == "404":
            raise HTTPException(status_code=404, detail="Injected parcel intake route not found")
        if fault == "500":
            raise HTTPException(status_code=500, detail="Injected parcel intake service failure")
        return {"status": "ok", "service": svc_name}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        fault = get_service_fault(svc_name)
        if fault == "404":
            raise HTTPException(status_code=404, detail="Injected parcel intake route not found")
        if fault == "500":
            raise HTTPException(status_code=500, detail="Injected parcel intake service failure")
        return {"status": "ok", "service": svc_name}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics() -> str:
        return f'# TYPE http_requests_total counter\nhttp_requests_total{{service="{svc_name}"}} 42\n'

    @app.post(
        f"{prefix}/intake",
        response_model=ParcelIntakeResponse,
        summary="Intake new parcel shipment",
        operation_id="intakeParcelShipment",
    )
    def intake_parcel(req: ParcelIntakeRequest) -> ParcelIntakeResponse:
        fault = get_service_fault(svc_name)
        if fault == "500":
            raise HTTPException(status_code=500, detail="Injected parcel intake service failure")
        if fault == "404":
            raise HTTPException(status_code=404, detail="Injected parcel intake route not found")

        # Cascade call to route service over HTTP
        if route_service_url:
            try:
                route_endpoint = f"{route_service_url.rstrip('/')}{prefix}/route"
                httpx.post(
                    route_endpoint,
                    json={"parcel_id": req.parcel_id, "origin": req.origin, "destination": req.destination},
                    timeout=2.0,
                )
            except Exception:
                pass

        return ParcelIntakeResponse(
            parcel_id=req.parcel_id,
            status="RECEIVED",
            hub_id="HUB-CENTRAL",
        )

    @app.get(
        f"{prefix}/{{parcel_id}}",
        summary="Get parcel intake status",
        operation_id="getParcelStatus",
    )
    def get_parcel(parcel_id: str) -> dict[str, Any]:
        fault = get_service_fault(svc_name)
        if fault == "404":
            raise HTTPException(status_code=404, detail=f"Parcel {parcel_id} not found (fault injected)")
        if fault == "500":
            raise HTTPException(status_code=500, detail="Internal error retrieving parcel")
        return {
            "parcel_id": parcel_id,
            "status": "IN_TRANSIT",
            "weight_kg": 2.5,
            "current_location": "HUB-CENTRAL",
        }

    return app


def create_route_app(*, suffix: str = "", dispatch_service_url: str | None = None) -> FastAPI:
    """Create parcel route optimization FastAPI application."""
    svc_name = f"svc-pkg-route-{suffix}" if suffix else "parcel-route-svc"
    prefix = f"/v1/packages-{suffix}" if suffix else "/v1/parcels"
    app = FastAPI(
        title=svc_name,
        version="1.0.0",
        description="Fictional Parcel Logistics Route Optimization Service",
    )

    @app.get("/")
    def root() -> dict[str, str]:
        fault = get_service_fault(svc_name)
        if fault == "404":
            raise HTTPException(status_code=404, detail="Injected parcel intake route not found")
        if fault == "500":
            raise HTTPException(status_code=500, detail="Injected parcel intake service failure")
        return {"status": "ok", "service": svc_name}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        fault = get_service_fault(svc_name)
        if fault == "404":
            raise HTTPException(status_code=404, detail="Injected parcel intake route not found")
        if fault == "500":
            raise HTTPException(status_code=500, detail="Injected parcel intake service failure")
        return {"status": "ok", "service": svc_name}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics() -> str:
        return f'# TYPE http_requests_total counter\nhttp_requests_total{{service="{svc_name}"}} 29\n'

    @app.post(
        f"{prefix}/route",
        response_model=TransitRouteResponse,
        summary="Calculate optimal parcel route",
        operation_id="calculateParcelRoute",
    )
    def calculate_route(req: TransitRouteRequest) -> TransitRouteResponse:
        fault = get_service_fault(svc_name)
        if fault == "500":
            raise HTTPException(status_code=500, detail="Injected routing engine 500 burst")
        if fault == "404":
            raise HTTPException(status_code=404, detail="Injected route not found")

        # Cascade call to dispatch service over HTTP
        if dispatch_service_url:
            try:
                dispatch_endpoint = f"{dispatch_service_url.rstrip('/')}{prefix}/dispatch"
                httpx.post(
                    dispatch_endpoint,
                    json={"route_id": f"RT-{req.parcel_id}", "courier_id": "CR-101"},
                    timeout=2.0,
                )
            except Exception:
                pass

        return TransitRouteResponse(
            parcel_id=req.parcel_id,
            carrier_code="EXPRESS_GROUND",
            estimated_transit_hours=4.5,
        )

    @app.get(
        f"{prefix}/routes/{{route_id}}",
        summary="Get parcel transit route details",
        operation_id="getTransitRoute",
    )
    def get_route(route_id: str) -> dict[str, Any]:
        fault = get_service_fault(svc_name)
        if fault == "404":
            raise HTTPException(status_code=404, detail=f"Route {route_id} not found")
        if fault == "500":
            raise HTTPException(status_code=500, detail="Routing database connection failure")
        return {
            "route_id": route_id,
            "carrier_code": "EXPRESS_GROUND",
            "status": "ACTIVE",
            "waypoints": ["HUB-CENTRAL", "DEPOT-WEST"],
        }

    return app


def create_dispatch_app(*, suffix: str = "", proof_service_url: str | None = None) -> FastAPI:
    """Create courier dispatch FastAPI application."""
    svc_name = f"svc-pkg-dispatch-{suffix}" if suffix else "parcel-dispatch-svc"
    prefix = f"/v1/packages-{suffix}" if suffix else "/v1/parcels"
    app = FastAPI(
        title=svc_name,
        version="1.0.0",
        description="Fictional Parcel Logistics Courier Dispatch Service",
    )

    @app.get("/")
    def root() -> dict[str, str]:
        fault = get_service_fault(svc_name)
        if fault == "404":
            raise HTTPException(status_code=404, detail="Injected parcel intake route not found")
        if fault == "500":
            raise HTTPException(status_code=500, detail="Injected parcel intake service failure")
        return {"status": "ok", "service": svc_name}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        fault = get_service_fault(svc_name)
        if fault == "404":
            raise HTTPException(status_code=404, detail="Injected parcel intake route not found")
        if fault == "500":
            raise HTTPException(status_code=500, detail="Injected parcel intake service failure")
        return {"status": "ok", "service": svc_name}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics() -> str:
        return f'# TYPE http_requests_total counter\nhttp_requests_total{{service="{svc_name}"}} 18\n'

    @app.post(
        f"{prefix}/dispatch",
        response_model=CourierDispatchResponse,
        summary="Dispatch parcel with courier carrier",
        operation_id="dispatchParcelCarrier",
    )
    def dispatch_parcel(req: CourierDispatchRequest) -> CourierDispatchResponse:
        fault = get_service_fault(svc_name)
        if fault == "500":
            raise HTTPException(status_code=500, detail="Injected courier dispatch error")

        # Cascade call to proof service over HTTP
        if proof_service_url:
            try:
                proof_endpoint = f"{proof_service_url.rstrip('/')}{prefix}/proof"
                httpx.post(
                    proof_endpoint,
                    json={"parcel_id": "PCL-101", "recipient_name": "Test Recipient", "signature_hash": "hash_valid_8823"},
                    timeout=2.0,
                )
            except Exception:
                pass

        return CourierDispatchResponse(
            route_id=req.route_id,
            courier_id=req.courier_id,
            dispatch_status="OUT_FOR_DELIVERY",
        )

    @app.get(
        f"{prefix}/dispatches/{{dispatch_id}}",
        summary="Get courier dispatch status",
        operation_id="getCourierDispatch",
    )
    def get_dispatch(dispatch_id: str) -> dict[str, Any]:
        return {
            "dispatch_id": dispatch_id,
            "courier_id": "CR-101",
            "status": "DISPATCHED",
        }

    return app


def create_proof_app(*, suffix: str = "") -> FastAPI:
    """Create delivery proof FastAPI application."""
    svc_name = f"svc-pkg-proof-{suffix}" if suffix else "parcel-proof-svc"
    prefix = f"/v1/packages-{suffix}" if suffix else "/v1/parcels"
    app = FastAPI(
        title=svc_name,
        version="1.0.0",
        description="Fictional Parcel Logistics Proof of Delivery Service",
    )

    @app.get("/")
    def root() -> dict[str, str]:
        fault = get_service_fault(svc_name)
        if fault == "404":
            raise HTTPException(status_code=404, detail="Injected parcel intake route not found")
        if fault == "500":
            raise HTTPException(status_code=500, detail="Injected parcel intake service failure")
        return {"status": "ok", "service": svc_name}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        fault = get_service_fault(svc_name)
        if fault == "404":
            raise HTTPException(status_code=404, detail="Injected parcel intake route not found")
        if fault == "500":
            raise HTTPException(status_code=500, detail="Injected parcel intake service failure")
        return {"status": "ok", "service": svc_name}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics() -> str:
        return f'# TYPE http_requests_total counter\nhttp_requests_total{{service="{svc_name}"}} 12\n'

    @app.post(
        f"{prefix}/proof",
        response_model=DeliveryProofResponse,
        summary="Submit delivery signature proof",
        operation_id="submitDeliveryProof",
    )
    def submit_proof(req: DeliveryProofRequest) -> DeliveryProofResponse:
        fault = get_service_fault(svc_name)
        if fault == "500":
            raise HTTPException(status_code=500, detail="Injected delivery proof signature verification error")
        return DeliveryProofResponse(
            parcel_id=req.parcel_id,
            verified=True,
            delivery_status="DELIVERED",
        )

    @app.get(
        f"{prefix}/proofs/{{proof_id}}",
        summary="Get delivery proof signature",
        operation_id="getDeliveryProof",
    )
    def get_proof(proof_id: str) -> dict[str, Any]:
        return {
            "proof_id": proof_id,
            "verified": True,
            "signature_hash": "sig_valid_8823",
        }

    return app


# --- Background Server Runner ---


def find_free_port() -> int:
    """Allocate an available local ephemeral TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RunningService:
    """Background uvicorn server running a parcel logistics service."""

    def __init__(self, name: str, app: FastAPI, port: int) -> None:
        self.name = name
        self.app = app
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        config = uvicorn.Config(
            self.app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()

    def wait_ready(self, timeout_s: float = 5.0) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                resp = httpx.get(f"{self.base_url}/healthz", timeout=0.5)
                if resp.status_code == 200:
                    return True
            except Exception:
                pass
            time.sleep(0.05)
        return False

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)


def start_parcel_stack(*, suffix: str = "") -> dict[str, RunningService]:
    """Start all 4 parcel logistics services on free ephemeral ports."""
    intake_name = f"svc-pkg-intake-{suffix}" if suffix else "parcel-intake-svc"
    route_name = f"svc-pkg-route-{suffix}" if suffix else "parcel-route-svc"
    dispatch_name = f"svc-pkg-dispatch-{suffix}" if suffix else "parcel-dispatch-svc"
    proof_name = f"svc-pkg-proof-{suffix}" if suffix else "parcel-proof-svc"

    intake_port = find_free_port()
    route_port = find_free_port()
    dispatch_port = find_free_port()
    proof_port = find_free_port()

    proof_url = f"http://127.0.0.1:{proof_port}"
    dispatch_url = f"http://127.0.0.1:{dispatch_port}"
    route_url = f"http://127.0.0.1:{route_port}"

    services = {
        intake_name: RunningService(intake_name, create_intake_app(suffix=suffix, route_service_url=route_url), intake_port),
        route_name: RunningService(route_name, create_route_app(suffix=suffix, dispatch_service_url=dispatch_url), route_port),
        dispatch_name: RunningService(dispatch_name, create_dispatch_app(suffix=suffix, proof_service_url=proof_url), dispatch_port),
        proof_name: RunningService(proof_name, create_proof_app(suffix=suffix), proof_port),
    }
    for svc in services.values():
        svc.start()
    for name, svc in services.items():
        if not svc.wait_ready():
            raise RuntimeError(f"Service {name} failed to become ready on port {svc.port}")
    return services


def stop_parcel_stack(services: dict[str, RunningService]) -> None:
    """Stop all background services."""
    for svc in services.values():
        try:
            svc.stop()
        except Exception:
            pass


# --- OTLP Telemetry Helper ---


def build_parcel_otlp_spans(
    parcel_id: str,
    *,
    suffix: str = "",
    intake_port: int,
    route_port: int,
    dispatch_port: int,
    proof_port: int,
) -> dict[str, Any]:
    """Build OTLP/HTTP JSON spans representing an intake->route->dispatch->proof parcel journey."""
    now_ns = int(time.time() * 1_000_000_000)
    trace_id = uuid.uuid4().hex
    span_intake_id = uuid.uuid4().hex[:16]
    span_route_id = uuid.uuid4().hex[:16]
    span_dispatch_id = uuid.uuid4().hex[:16]
    span_proof_id = uuid.uuid4().hex[:16]

    intake_name = f"svc-pkg-intake-{suffix}" if suffix else "parcel-intake-svc"
    route_name = f"svc-pkg-route-{suffix}" if suffix else "parcel-route-svc"
    dispatch_name = f"svc-pkg-dispatch-{suffix}" if suffix else "parcel-dispatch-svc"
    proof_name = f"svc-pkg-proof-{suffix}" if suffix else "parcel-proof-svc"

    prefix = f"/v1/packages-{suffix}" if suffix else "/v1/parcels"

    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": intake_name}}
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_intake_id,
                                "name": f"POST {prefix}/intake",
                                "startTimeUnixNano": str(now_ns),
                                "endTimeUnixNano": str(now_ns + 50_000_000),
                                "attributes": [
                                    {"key": "http.method", "value": {"stringValue": "POST"}},
                                    {"key": "http.route", "value": {"stringValue": f"{prefix}/intake"}},
                                    {"key": "http.status_code", "value": {"intValue": 200}},
                                    {"key": "parcel.id", "value": {"stringValue": parcel_id}},
                                    {"key": "entity_type", "value": {"stringValue": "parcel"}},
                                    {"key": "entity_id", "value": {"stringValue": parcel_id}},
                                    {"key": "state_from", "value": {"stringValue": "NEW"}},
                                    {"key": "state_to", "value": {"stringValue": "INTAKEN"}},
                                ],
                            }
                        ]
                    }
                ],
            },
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": route_name}}
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_route_id,
                                "parentSpanId": span_intake_id,
                                "name": f"POST {prefix}/route",
                                "startTimeUnixNano": str(now_ns + 55_000_000),
                                "endTimeUnixNano": str(now_ns + 90_000_000),
                                "attributes": [
                                    {"key": "http.method", "value": {"stringValue": "POST"}},
                                    {"key": "http.route", "value": {"stringValue": f"{prefix}/route"}},
                                    {"key": "http.status_code", "value": {"intValue": 200}},
                                    {"key": "parcel.id", "value": {"stringValue": parcel_id}},
                                    {"key": "entity_type", "value": {"stringValue": "parcel"}},
                                    {"key": "entity_id", "value": {"stringValue": parcel_id}},
                                    {"key": "state_from", "value": {"stringValue": "INTAKEN"}},
                                    {"key": "state_to", "value": {"stringValue": "ROUTED"}},
                                ],
                            }
                        ]
                    }
                ],
            },
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": dispatch_name}}
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_dispatch_id,
                                "parentSpanId": span_route_id,
                                "name": f"POST {prefix}/dispatch",
                                "startTimeUnixNano": str(now_ns + 95_000_000),
                                "endTimeUnixNano": str(now_ns + 140_000_000),
                                "attributes": [
                                    {"key": "http.method", "value": {"stringValue": "POST"}},
                                    {"key": "http.route", "value": {"stringValue": f"{prefix}/dispatch"}},
                                    {"key": "http.status_code", "value": {"intValue": 200}},
                                    {"key": "parcel.id", "value": {"stringValue": parcel_id}},
                                    {"key": "entity_type", "value": {"stringValue": "parcel"}},
                                    {"key": "entity_id", "value": {"stringValue": parcel_id}},
                                    {"key": "state_from", "value": {"stringValue": "ROUTED"}},
                                    {"key": "state_to", "value": {"stringValue": "DISPATCHED"}},
                                ],
                            }
                        ]
                    }
                ],
            },
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": proof_name}}
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_proof_id,
                                "parentSpanId": span_dispatch_id,
                                "name": f"POST {prefix}/proof",
                                "startTimeUnixNano": str(now_ns + 145_000_000),
                                "endTimeUnixNano": str(now_ns + 180_000_000),
                                "attributes": [
                                    {"key": "http.method", "value": {"stringValue": "POST"}},
                                    {"key": "http.route", "value": {"stringValue": f"{prefix}/proof"}},
                                    {"key": "http.status_code", "value": {"intValue": 200}},
                                    {"key": "parcel.id", "value": {"stringValue": parcel_id}},
                                    {"key": "entity_type", "value": {"stringValue": "parcel"}},
                                    {"key": "entity_id", "value": {"stringValue": parcel_id}},
                                    {"key": "state_from", "value": {"stringValue": "DISPATCHED"}},
                                    {"key": "state_to", "value": {"stringValue": "DELIVERED"}},
                                ],
                            }
                        ]
                    }
                ],
            },
        ]
    }
