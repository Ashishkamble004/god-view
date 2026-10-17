"""Fictional Parcel Logistics Microservices for Unknown-Environment E2E.

Services:
1. parcel-intake-svc: POST /v1/parcels/intake, GET /v1/parcels/{parcel_id}, GET /healthz
2. parcel-route-svc: POST /v1/parcels/route, GET /v1/parcels/routes/{route_id}, GET /healthz
3. parcel-dispatch-svc: POST /v1/parcels/dispatch, GET /v1/parcels/dispatches/{dispatch_id}, GET /healthz
4. parcel-proof-svc: POST /v1/parcels/proof, GET /v1/parcels/proofs/{proof_id}, GET /healthz

Includes:
- OpenAPI specs automatically served at /openapi.json
- Synthetic probe-safe indicators
- Configurable fault injection (404 route, 5xx burst)
- OTLP telemetry reporting helper
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
_ACTIVE_FAULTS: dict[str, str | None] = {
    "parcel-intake-svc": None,
    "parcel-route-svc": None,
    "parcel-dispatch-svc": None,
    "parcel-proof-svc": None,
}


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


def create_intake_app() -> FastAPI:
    """Create parcel-intake-svc FastAPI application."""
    app = FastAPI(
        title="parcel-intake-svc",
        version="1.0.0",
        description="Fictional Parcel Logistics Intake Service",
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "parcel-intake-svc"}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics() -> str:
        return "# TYPE http_requests_total counter\nhttp_requests_total{service=\"parcel-intake-svc\"} 42\n"

    @app.post(
        "/v1/parcels/intake",
        response_model=ParcelIntakeResponse,
        summary="Intake new parcel shipment",
        operation_id="intakeParcelShipment",
    )
    def intake_parcel(req: ParcelIntakeRequest) -> ParcelIntakeResponse:
        fault = get_service_fault("parcel-intake-svc")
        if fault == "500":
            raise HTTPException(status_code=500, detail="Injected parcel intake service failure")
        if fault == "404":
            raise HTTPException(status_code=404, detail="Injected parcel intake route not found")
        return ParcelIntakeResponse(
            parcel_id=req.parcel_id,
            status="RECEIVED",
            hub_id="HUB-CENTRAL",
        )

    @app.get(
        "/v1/parcels/{parcel_id}",
        summary="Get parcel intake status",
        operation_id="getParcelStatus",
    )
    def get_parcel(parcel_id: str) -> dict[str, Any]:
        fault = get_service_fault("parcel-intake-svc")
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


def create_route_app() -> FastAPI:
    """Create parcel-route-svc FastAPI application."""
    app = FastAPI(
        title="parcel-route-svc",
        version="1.0.0",
        description="Fictional Parcel Logistics Route Optimization Service",
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "parcel-route-svc"}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics() -> str:
        return "# TYPE http_requests_total counter\nhttp_requests_total{service=\"parcel-route-svc\"} 29\n"

    @app.post(
        "/v1/parcels/route",
        response_model=TransitRouteResponse,
        summary="Calculate optimal parcel route",
        operation_id="calculateParcelRoute",
    )
    def calculate_route(req: TransitRouteRequest) -> TransitRouteResponse:
        fault = get_service_fault("parcel-route-svc")
        if fault == "500":
            raise HTTPException(status_code=500, detail="Injected routing engine 500 burst")
        if fault == "404":
            raise HTTPException(status_code=404, detail="Injected route not found")
        return TransitRouteResponse(
            parcel_id=req.parcel_id,
            carrier_code="EXPRESS_GROUND",
            estimated_transit_hours=4.5,
        )

    @app.get(
        "/v1/parcels/routes/{route_id}",
        summary="Get parcel transit route details",
        operation_id="getTransitRoute",
    )
    def get_route(route_id: str) -> dict[str, Any]:
        fault = get_service_fault("parcel-route-svc")
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


def create_dispatch_app() -> FastAPI:
    """Create parcel-dispatch-svc FastAPI application."""
    app = FastAPI(
        title="parcel-dispatch-svc",
        version="1.0.0",
        description="Fictional Parcel Logistics Courier Dispatch Service",
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "parcel-dispatch-svc"}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics() -> str:
        return "# TYPE http_requests_total counter\nhttp_requests_total{service=\"parcel-dispatch-svc\"} 18\n"

    @app.post(
        "/v1/parcels/dispatch",
        response_model=CourierDispatchResponse,
        summary="Dispatch parcel with courier carrier",
        operation_id="dispatchParcelCarrier",
    )
    def dispatch_parcel(req: CourierDispatchRequest) -> CourierDispatchResponse:
        fault = get_service_fault("parcel-dispatch-svc")
        if fault == "500":
            raise HTTPException(status_code=500, detail="Injected courier dispatch error")
        return CourierDispatchResponse(
            route_id=req.route_id,
            courier_id=req.courier_id,
            dispatch_status="OUT_FOR_DELIVERY",
        )

    @app.get(
        "/v1/parcels/dispatches/{dispatch_id}",
        summary="Get courier dispatch status",
        operation_id="getDispatchStatus",
    )
    def get_dispatch(dispatch_id: str) -> dict[str, Any]:
        return {
            "dispatch_id": dispatch_id,
            "courier_id": "CR-101",
            "dispatch_status": "OUT_FOR_DELIVERY",
        }

    return app


def create_proof_app() -> FastAPI:
    """Create parcel-proof-svc FastAPI application."""
    app = FastAPI(
        title="parcel-proof-svc",
        version="1.0.0",
        description="Fictional Parcel Logistics Proof of Delivery Service",
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "parcel-proof-svc"}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics() -> str:
        return "# TYPE http_requests_total counter\nhttp_requests_total{service=\"parcel-proof-svc\"} 15\n"

    @app.post(
        "/v1/parcels/proof",
        response_model=DeliveryProofResponse,
        summary="Record digital proof of parcel delivery",
        operation_id="recordDeliveryProof",
    )
    def record_proof(req: DeliveryProofRequest) -> DeliveryProofResponse:
        fault = get_service_fault("parcel-proof-svc")
        if fault == "500":
            raise HTTPException(status_code=500, detail="Injected delivery proof write error")
        return DeliveryProofResponse(
            parcel_id=req.parcel_id,
            verified=True,
            delivery_status="DELIVERED",
        )

    @app.get(
        "/v1/parcels/proofs/{proof_id}",
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


def start_parcel_stack() -> dict[str, RunningService]:
    """Start all 4 parcel logistics services on free ephemeral ports."""
    services = {
        "parcel-intake-svc": RunningService("parcel-intake-svc", create_intake_app(), find_free_port()),
        "parcel-route-svc": RunningService("parcel-route-svc", create_route_app(), find_free_port()),
        "parcel-dispatch-svc": RunningService("parcel-dispatch-svc", create_dispatch_app(), find_free_port()),
        "parcel-proof-svc": RunningService("parcel-proof-svc", create_proof_app(), find_free_port()),
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

    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "parcel-intake-svc"}}
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_intake_id,
                                "name": "POST /v1/parcels/intake",
                                "startTimeUnixNano": str(now_ns),
                                "endTimeUnixNano": str(now_ns + 50_000_000),
                                "attributes": [
                                    {"key": "http.method", "value": {"stringValue": "POST"}},
                                    {"key": "http.route", "value": {"stringValue": "/v1/parcels/intake"}},
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
                        {"key": "service.name", "value": {"stringValue": "parcel-route-svc"}}
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_route_id,
                                "parentSpanId": span_intake_id,
                                "name": "POST /v1/parcels/route",
                                "startTimeUnixNano": str(now_ns + 60_000_000),
                                "endTimeUnixNano": str(now_ns + 100_000_000),
                                "attributes": [
                                    {"key": "http.method", "value": {"stringValue": "POST"}},
                                    {"key": "http.route", "value": {"stringValue": "/v1/parcels/route"}},
                                    {"key": "http.status_code", "value": {"intValue": 200}},
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
                        {"key": "service.name", "value": {"stringValue": "parcel-dispatch-svc"}}
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_dispatch_id,
                                "parentSpanId": span_route_id,
                                "name": "POST /v1/parcels/dispatch",
                                "startTimeUnixNano": str(now_ns + 110_000_000),
                                "endTimeUnixNano": str(now_ns + 140_000_000),
                                "attributes": [
                                    {"key": "http.method", "value": {"stringValue": "POST"}},
                                    {"key": "http.route", "value": {"stringValue": "/v1/parcels/dispatch"}},
                                    {"key": "http.status_code", "value": {"intValue": 200}},
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
                        {"key": "service.name", "value": {"stringValue": "parcel-proof-svc"}}
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_proof_id,
                                "parentSpanId": span_dispatch_id,
                                "name": "POST /v1/parcels/proof",
                                "startTimeUnixNano": str(now_ns + 150_000_000),
                                "endTimeUnixNano": str(now_ns + 180_000_000),
                                "attributes": [
                                    {"key": "http.method", "value": {"stringValue": "POST"}},
                                    {"key": "http.route", "value": {"stringValue": "/v1/parcels/proof"}},
                                    {"key": "http.status_code", "value": {"intValue": 200}},
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
