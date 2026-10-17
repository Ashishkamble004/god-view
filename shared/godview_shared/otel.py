"""OpenTelemetry tracing, W3C baggage propagation, business span attributes, and Prometheus /metrics exposition.

Enforces TDD I-04 (never read telemetry from the patient), I-07 (synthetic marker),
and §2.2 / §3.2 (business span attributes).
"""

from __future__ import annotations

import importlib
import logging
import os
import time
import urllib.parse
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

logger = logging.getLogger(__name__)

BAGGAGE_KEYS: tuple[str, ...] = ("journey.id", "customer.id", "synthetic")

BUSINESS_SPAN_ATTRIBUTES: tuple[str, ...] = (
    "journey.id",
    "customer.id",
    "decision",
    "status_from",
    "status_to",
    "event_type",
    "entity",
    "entity_id",
    "synthetic",
)

SYNTHETIC_HEADER: str = "X-Synthetic"
TRACEPARENT_HEADER: str = "traceparent"
BAGGAGE_HEADER: str = "baggage"

_TRACER_PROVIDERS: dict[str, TracerProvider] = {}
_CONFIGURED_ENDPOINTS: set[tuple[str, str]] = set()
METRICS_REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS_TOTAL = Counter(
    "godview_http_requests_total",
    "Total HTTP requests processed",
    labelnames=("service", "method", "route", "status"),
    registry=METRICS_REGISTRY,
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "godview_http_request_duration_seconds",
    "HTTP request latency in seconds",
    labelnames=("service", "method", "route"),
    registry=METRICS_REGISTRY,
)


def get_tracer(service_name: str) -> trace.Tracer:
    """Return an OpenTelemetry tracer configured for service_name."""
    if service_name not in _TRACER_PROVIDERS:
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        _TRACER_PROVIDERS[service_name] = provider
    return _TRACER_PROVIDERS[service_name].get_tracer(service_name)


def _attr_to_otlp_value(val: Any) -> dict[str, Any]:
    if isinstance(val, bool):
        return {"boolValue": val}
    if isinstance(val, int):
        return {"intValue": str(val)}
    if isinstance(val, float):
        return {"doubleValue": val}
    return {"stringValue": str(val)}


def _otlp_value_to_python(val_obj: Mapping[str, Any]) -> Any:
    if "boolValue" in val_obj:
        return bool(val_obj["boolValue"])
    if "intValue" in val_obj:
        return int(val_obj["intValue"])
    if "doubleValue" in val_obj:
        return float(val_obj["doubleValue"])
    if "stringValue" in val_obj:
        return str(val_obj["stringValue"])
    return None


def _ns_to_iso(ns: int | None) -> str:
    if not ns:
        return datetime.now(tz=UTC).isoformat()
    return datetime.fromtimestamp(ns / 1_000_000_000, tz=UTC).isoformat()


class OtlpHttpSpanExporter(SpanExporter):
    """OTLP/HTTP JSON SpanExporter posting spans to the Sovereign `/v1/traces` receiver (#120/#125)."""

    def __init__(
        self,
        endpoint: str,
        *,
        service_name: str = "unknown-service",
        timeout_s: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        raw = endpoint.rstrip("/")
        if not raw.endswith("/traces"):
            raw = f"{raw}/v1/traces"
        self.endpoint = raw
        self.service_name = service_name
        self._timeout_s = timeout_s
        self._transport = transport
        self._shutdown = False
        self._cached_id_token: str | None = None
        self._token_expires_at: float = 0.0

    def _get_oidc_headers(self) -> dict[str, str]:
        """Fetch and cache a Google OIDC identity token when exporting to an https:// Cloud Run endpoint."""
        if self._transport is not None or not self.endpoint.startswith("https://"):
            return {}
        now = time.monotonic()
        if self._cached_id_token is not None and now < self._token_expires_at:
            return (
                {"Authorization": f"Bearer {self._cached_id_token}"}
                if self._cached_id_token
                else {}
            )
        parsed = urllib.parse.urlsplit(self.endpoint)
        audience = f"{parsed.scheme}://{parsed.netloc}"
        metadata_url = (
            "http://metadata.google.internal/computeMetadata/v1/instance/"
            f"service-accounts/default/identity?audience={urllib.parse.quote(audience, safe='')}"
        )
        try:
            with httpx.Client(timeout=2.0) as client:
                resp = client.get(metadata_url, headers={"Metadata-Flavor": "Google"})
                if resp.status_code == 200 and resp.text.strip():
                    self._cached_id_token = resp.text.strip()
                    self._token_expires_at = now + 3000.0
                    return {"Authorization": f"Bearer {self._cached_id_token}"}
        except httpx.HTTPError:
            pass
        self._cached_id_token = ""
        self._token_expires_at = now + 60.0
        return {}

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        if self._shutdown:
            return SpanExportResult.FAILURE
        if not spans:
            return SpanExportResult.SUCCESS

        otlp_spans: list[dict[str, Any]] = []
        flat_spans: list[dict[str, Any]] = []
        for span in spans:
            ctx = span.get_span_context()
            trace_id = f"{ctx.trace_id:032x}" if ctx and ctx.trace_id else ""
            span_id = f"{ctx.span_id:016x}" if ctx and ctx.span_id else ""
            attrs = dict(span.attributes or {})
            res_attrs = dict(span.resource.attributes) if span.resource else {}
            svc = str(
                res_attrs.get("service.name") or attrs.get("service.name") or self.service_name
            )
            start_ns = span.start_time or int(datetime.now(tz=UTC).timestamp() * 1_000_000_000)
            end_ns = span.end_time or start_ns
            otlp_attrs = [{"key": k, "value": _attr_to_otlp_value(v)} for k, v in attrs.items()]
            otlp_spans.append(
                {
                    "traceId": trace_id,
                    "spanId": span_id,
                    "name": span.name,
                    "startTimeUnixNano": str(start_ns),
                    "endTimeUnixNano": str(end_ns),
                    "attributes": otlp_attrs,
                }
            )
            flat_spans.append(
                {
                    "trace_id": trace_id or None,
                    "span_id": span_id or None,
                    "service": svc,
                    "operation": span.name,
                    "start_time": _ns_to_iso(start_ns),
                    "end_time": _ns_to_iso(end_ns),
                    "attributes": attrs,
                }
            )

        payload = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {
                                "key": "service.name",
                                "value": {"stringValue": self.service_name},
                            }
                        ]
                    },
                    "scopeSpans": [{"spans": otlp_spans}],
                }
            ],
            "spans": flat_spans,
        }
        try:
            req_headers = self._get_oidc_headers()
            with httpx.Client(timeout=self._timeout_s, transport=self._transport) as client:
                resp = client.post(self.endpoint, json=payload, headers=req_headers)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.debug("OTLP/HTTP span export to %s failed: %s", self.endpoint, exc)
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self._shutdown = True

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return not self._shutdown


def parse_otlp_spans(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Parse either flat `spans` dicts or OTLP/HTTP JSON `resourceSpans` into LedgerSpanExporter span dicts."""
    direct = payload.get("spans")
    if isinstance(direct, list) and direct:
        return [dict(item) for item in direct if isinstance(item, Mapping)]

    normalized: list[dict[str, Any]] = []
    resource_spans = payload.get("resourceSpans")
    if not isinstance(resource_spans, list):
        return normalized

    for rs in resource_spans:
        if not isinstance(rs, Mapping):
            continue
        res_service = "unknown-service"
        res_obj = rs.get("resource")
        if isinstance(res_obj, Mapping):
            for ra in res_obj.get("attributes") or []:
                if isinstance(ra, Mapping) and ra.get("key") == "service.name":
                    val_obj = ra.get("value")
                    if isinstance(val_obj, Mapping):
                        parsed_svc = _otlp_value_to_python(val_obj)
                        if parsed_svc:
                            res_service = str(parsed_svc)

        scope_spans = rs.get("scopeSpans") or rs.get("instrumentationLibrarySpans") or []
        if not isinstance(scope_spans, list):
            continue
        for ss in scope_spans:
            if not isinstance(ss, Mapping):
                continue
            for sp in ss.get("spans") or []:
                if not isinstance(sp, Mapping):
                    continue
                attrs: dict[str, Any] = {}
                raw_attrs = sp.get("attributes")
                if isinstance(raw_attrs, list):
                    for attr_item in raw_attrs:
                        if isinstance(attr_item, Mapping) and "key" in attr_item:
                            val_obj = attr_item.get("value")
                            if isinstance(val_obj, Mapping):
                                py_val = _otlp_value_to_python(val_obj)
                                if py_val is not None:
                                    attrs[str(attr_item["key"])] = py_val
                elif isinstance(raw_attrs, Mapping):
                    attrs.update(raw_attrs)

                start_ns = int(sp["startTimeUnixNano"]) if sp.get("startTimeUnixNano") else None
                end_ns = int(sp["endTimeUnixNano"]) if sp.get("endTimeUnixNano") else start_ns
                normalized.append(
                    {
                        "trace_id": sp.get("traceId") or sp.get("trace_id"),
                        "span_id": sp.get("spanId") or sp.get("span_id"),
                        "service": str(
                            sp.get("service") or attrs.get("service.name") or res_service
                        ),
                        "operation": str(sp.get("name") or sp.get("operation") or "unknown-op"),
                        "start_time": sp.get("start_time") or _ns_to_iso(start_ns),
                        "end_time": sp.get("end_time") or _ns_to_iso(end_ns),
                        "attributes": attrs,
                    }
                )
    return normalized


def configure_exporters(
    service_name: str,
    *,
    env: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
    use_simple_processor: bool = False,
) -> TracerProvider:
    """Configure OTLP/HTTP (and optional Cloud Trace in gcp mode) span exporters on the service TracerProvider.

    Strictly a no-op with respect to network exporters when `GODVIEW_ENV=hermetic` (#120 / #125).
    """
    raw_env = env if env is not None else os.environ
    env_mode = raw_env.get("GODVIEW_ENV", "hermetic").strip().lower()
    if service_name not in _TRACER_PROVIDERS:
        _TRACER_PROVIDERS[service_name] = TracerProvider(
            resource=Resource.create({"service.name": service_name})
        )
    provider = _TRACER_PROVIDERS[service_name]

    if env_mode == "hermetic":
        return provider

    otlp_endpoint = raw_env.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if otlp_endpoint and (service_name, otlp_endpoint) not in _CONFIGURED_ENDPOINTS:
        exporter = OtlpHttpSpanExporter(
            otlp_endpoint,
            service_name=service_name,
            transport=transport,
        )
        if use_simple_processor:
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor

            provider.add_span_processor(SimpleSpanProcessor(exporter))
        else:
            provider.add_span_processor(BatchSpanProcessor(exporter))
        _CONFIGURED_ENDPOINTS.add((service_name, otlp_endpoint))

    if env_mode == "gcp" and (service_name, "cloud_trace") not in _CONFIGURED_ENDPOINTS:
        if raw_env.get("GODVIEW_DISABLE_CLOUD_TRACE", "").strip().lower() not in ("1", "true"):
            try:
                ct_mod = importlib.import_module("opentelemetry.exporter.cloud_trace")
                ct_cls = getattr(ct_mod, "CloudTraceSpanExporter", None)
                if ct_cls is not None:
                    provider.add_span_processor(BatchSpanProcessor(ct_cls()))
                    _CONFIGURED_ENDPOINTS.add((service_name, "cloud_trace"))
            except ImportError:
                logger.info(
                    "opentelemetry-exporter-gcp-trace not installed; using OTLP/HTTP export for %s",
                    service_name,
                )

    return provider


def set_business_attributes(
    span: trace.Span,
    **attrs: Any,
) -> dict[str, Any]:
    """Attach generic business attributes to an OpenTelemetry span and return them."""
    applied: dict[str, Any] = {}
    for key, val in attrs.items():
        if val is not None:
            span.set_attribute(key, val)
            applied[key] = val
    return applied


def set_business_span_attributes(
    span: trace.Span,
    **kwargs: Any,
) -> dict[str, Any]:
    """Deprecated shim mapping legacy span attributes into generic business attributes."""
    mapping: dict[str, Any] = {}
    extra = kwargs.pop("extra", None)
    for k, v in kwargs.items():
        if k in ("journey_id", "customer_id") and "." not in k:
            mapping[k.replace("_", ".")] = v
        else:
            mapping[k] = v
    if extra and isinstance(extra, Mapping):
        mapping.update(extra)
    return set_business_attributes(span, **mapping)


def inject_baggage_headers(
    *,
    journey_id: str | None = None,
    customer_id: str | None = None,
    synthetic: bool = False,
    traceparent: str | None = None,
) -> dict[str, str]:
    """Construct outbound W3C traceparent, baggage, and X-Synthetic headers."""
    headers: dict[str, str] = {}
    baggage_items: list[str] = []
    if journey_id:
        baggage_items.append(f"journey.id={journey_id}")
    if customer_id:
        baggage_items.append(f"customer.id={customer_id}")
    baggage_items.append(f"synthetic={'true' if synthetic else 'false'}")
    headers[BAGGAGE_HEADER] = ",".join(baggage_items)
    if synthetic:
        headers[SYNTHETIC_HEADER] = "true"
    if traceparent:
        headers[TRACEPARENT_HEADER] = traceparent
    return headers


def extract_baggage_context(headers: Mapping[str, str]) -> dict[str, Any]:
    """Extract journey.id, customer.id, and synthetic flag from inbound HTTP headers."""
    lower_headers = {k.lower(): v for k, v in headers.items()}
    raw_baggage = lower_headers.get("baggage", "")
    parsed: dict[str, str] = {}
    for pair in raw_baggage.split(","):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            parsed[k.strip()] = v.strip()

    x_syn = lower_headers.get("x-synthetic", "").strip().lower() in ("true", "1", "yes")
    bag_syn = parsed.get("synthetic", "").strip().lower() in ("true", "1", "yes")
    return {
        "journey.id": parsed.get("journey.id"),
        "customer.id": parsed.get("customer.id"),
        "synthetic": bool(x_syn or bag_syn),
        "traceparent": lower_headers.get("traceparent"),
    }


def render_prometheus_metrics(registry: CollectorRegistry | None = None) -> tuple[bytes, str]:
    """Render Prometheus text exposition format payload and content-type."""
    target = registry if registry is not None else METRICS_REGISTRY
    return generate_latest(target), CONTENT_TYPE_LATEST
