"""OTel → BigQuery Storage Write API exporter for godview_ledger (TDD §2.2 & Decision A13).

Implements:
- Default stream (`projects/<project>/datasets/godview_ledger/tables/<table>/streams/_default`)
  at-least-once semantics with deduplication by `event_id` / `transition_id`.
- Extraction of the 14 canonical business span attributes from TDD §2.2 into `events`
  and `state_transitions`.
- Real-time journey stitching by `journey.id` into `journeys` with process-mining
  conformance classification (`CONFORMANT`, `SKIPPED`, `REORDERED`, `LOOPED`, `STALLED`).
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from godview_shared.contracts import (
    JourneyStep,
    LedgerEventRow,
    LedgerEventSource,
    LedgerJourneyRow,
    StateTransitionEntity,
    StateTransitionRow,
)
from godview_shared.fakes import InMemoryBigQuery
from godview_shared.otel import BUSINESS_SPAN_ATTRIBUTES
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from godview.ledger.process_mining import (
    DEFAULT_INTENDED_ORDER,
    canonical_step_name,
    classify_journey_conformance,
)

_PRIMARY_KEY_BY_TABLE: dict[str, str] = {
    "events": "event_id",
    "state_transitions": "transition_id",
    "journeys": "journey_id",
}


class StorageWriteStreamProtocol(Protocol):
    """Protocol abstracting the BigQuery Storage Write API default stream (`_default`)."""

    @property
    def stream_name(self) -> str:
        """Return the canonical BigQuery Storage Write API default stream resource path."""
        ...

    def append_rows(self, table: str, rows: Iterable[dict[str, Any]]) -> int:
        """Append serialized row dicts to the table's default stream with deduplication."""
        ...


class InMemoryStorageWriteStream:
    """BigQuery Storage Write API default stream backed by InMemoryBigQuery (Decision A13)."""

    def __init__(
        self,
        bigquery: InMemoryBigQuery,
        *,
        project_id: str = "env-project",
        dataset_id: str = "godview_ledger",
    ) -> None:
        self._bigquery = bigquery
        self._project_id = project_id
        self._dataset_id = dataset_id

    @property
    def stream_name(self) -> str:
        return f"projects/{self._project_id}/datasets/{self._dataset_id}/tables/*/streams/_default"

    def table_stream_name(self, table: str) -> str:
        return f"projects/{self._project_id}/datasets/{self._dataset_id}/tables/{table}/streams/_default"

    def append_rows(self, table: str, rows: Iterable[dict[str, Any]]) -> int:
        return self._bigquery.insert_rows(table, rows)


def _fetch_gcp_access_token() -> str:
    """Acquire an OAuth2 access token from env or the GCE/Cloud Run metadata server without vendor SDK imports."""
    env_tok = os.environ.get("GODVIEW_GCP_ACCESS_TOKEN")
    if env_tok:
        return env_tok.strip()
    req = urllib.request.Request(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:  # noqa: S310
        payload = json.loads(resp.read().decode("utf-8"))
        token = str(payload.get("access_token") or "")
        if not token:
            raise RuntimeError("Empty access_token from GCE metadata server")
        return token


class GcpRestStorageWriteStream:
    """Live GCP BigQuery default-stream writer using REST `insertAll` with `insertId` deduplication."""

    def __init__(
        self,
        *,
        project_id: str | None = None,
        dataset_id: str = "godview_ledger",
        token_provider: Callable[[], str] | None = None,
    ) -> None:
        resolved_project = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT") or ""
        if not resolved_project:
            raise ValueError(
                "GOOGLE_CLOUD_PROJECT or project_id is required for GcpRestStorageWriteStream"
            )
        self._project_id = resolved_project
        self._dataset_id = dataset_id
        self._token_provider = token_provider or _fetch_gcp_access_token
        self._seen_insert_ids: dict[str, set[str]] = {
            "events": set(),
            "state_transitions": set(),
            "journeys": set(),
            "kpi_rollups": set(),
        }

    @property
    def stream_name(self) -> str:
        return f"projects/{self._project_id}/datasets/{self._dataset_id}/tables/*/streams/_default"

    def table_stream_name(self, table: str) -> str:
        return f"projects/{self._project_id}/datasets/{self._dataset_id}/tables/{table}/streams/_default"

    def append_rows(self, table: str, rows: Iterable[dict[str, Any]]) -> int:
        pk_field = _PRIMARY_KEY_BY_TABLE.get(table)
        seen = self._seen_insert_ids.setdefault(table, set())
        batch_entries: list[dict[str, Any]] = []
        for row in rows:
            serialized = json.loads(json.dumps(row, default=str))
            insert_id = str(serialized.get(pk_field) or "") if pk_field else ""
            if insert_id:
                if insert_id in seen:
                    continue
                seen.add(insert_id)
                batch_entries.append({"insertId": insert_id, "json": serialized})
            else:
                batch_entries.append({"json": serialized})

        if not batch_entries:
            return 0

        token = self._token_provider()
        url = (
            f"https://bigquery.googleapis.com/bigquery/v2/projects/{self._project_id}"
            f"/datasets/{self._dataset_id}/tables/{table}/insertAll"
        )
        body = json.dumps(
            {
                "kind": "bigquery#tableDataInsertAllRequest",
                "skipInvalidRows": False,
                "ignoreUnknownValues": False,
                "rows": batch_entries,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10.0) as resp:  # noqa: S310
            resp_data = json.loads(resp.read().decode("utf-8"))
            insert_errors = resp_data.get("insertErrors") or []
            if insert_errors:
                raise RuntimeError(f"BigQuery insertAll failed for {table}: {insert_errors}")
        return len(batch_entries)


@dataclass(frozen=True)
class ExportBatchResult:
    """Counts of deduplicated rows written across the ledger tables for one export batch."""

    events_written: int
    transitions_written: int
    journeys_updated: int


@dataclass
class _JourneyAccumulator:
    journey_id: str
    journey_name: str
    env_id: str
    customer_id: str = "unknown"
    started_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    ended_at: datetime | None = None
    step_events: list[dict[str, Any]] = field(default_factory=list)
    final_status: str = "IN_PROGRESS"
    total_value: Decimal = Decimal("0")
    is_synthetic: bool = False
    seen_event_ids: set[str] = field(default_factory=set)


def _ns_to_datetime(ns: int | None) -> datetime:
    if not ns:
        return datetime.now(tz=UTC)
    return datetime.fromtimestamp(ns / 1_000_000_000, tz=UTC)


def _deterministic_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _infer_entity(
    raw_entity: str | None,
    event_type: str,
) -> StateTransitionEntity:
    if raw_entity:
        return str(raw_entity)
    if "." in event_type:
        return event_type.split(".", 1)[0]
    return "default_entity"


class LedgerSpanExporter(SpanExporter):
    """Sovereign-side OTel span exporter writing to BigQuery godview_ledger (Decision A13)."""

    def __init__(
        self,
        bigquery: InMemoryBigQuery | None = None,
        *,
        env_id: str = "local-compose",
        default_journey_name: str = "default_journey",
        write_stream: StorageWriteStreamProtocol | None = None,
        intended_order: Sequence[str] = DEFAULT_INTENDED_ORDER,
        stall_threshold_ms: int = 30_000,
    ) -> None:
        self._bigquery = bigquery
        self._env_id = env_id
        self._default_journey_name = default_journey_name
        if write_stream is not None:
            self._write_stream: StorageWriteStreamProtocol = write_stream
        elif bigquery is not None:
            self._write_stream = InMemoryStorageWriteStream(bigquery)
        elif os.environ.get("GODVIEW_ENV", "hermetic") != "hermetic":
            self._write_stream = GcpRestStorageWriteStream()
        else:
            raise ValueError(
                "LedgerSpanExporter requires either bigquery (InMemoryBigQuery) or write_stream in hermetic mode"
            )
        self._intended_order = tuple(intended_order)
        self._stall_threshold_ms = stall_threshold_ms
        self._journeys: dict[str, _JourneyAccumulator] = {}
        self._last_transition_time: dict[tuple[str, str], datetime] = {}
        self._seen_event_ids: set[str] = set()
        self._shutdown = False

    @property
    def write_stream(self) -> StorageWriteStreamProtocol:
        return self._write_stream

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Export a batch of OpenTelemetry ReadableSpan objects to BigQuery."""
        if self._shutdown:
            return SpanExportResult.FAILURE
        span_dicts = [self._readable_span_to_dict(span) for span in spans]
        self.export_span_dicts(span_dicts)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self._shutdown = True

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return not self._shutdown

    def _readable_span_to_dict(self, span: ReadableSpan) -> dict[str, Any]:
        ctx = span.get_span_context()
        trace_id = f"{ctx.trace_id:032x}" if ctx and ctx.trace_id else None
        span_id = f"{ctx.span_id:016x}" if ctx and ctx.span_id else None
        attrs = dict(span.attributes or {})
        res_attrs = dict(span.resource.attributes) if span.resource else {}
        service_name = str(
            res_attrs.get("service.name") or attrs.get("service.name") or "unknown-service"
        )
        start_dt = _ns_to_datetime(span.start_time)
        end_dt = _ns_to_datetime(span.end_time) if span.end_time else start_dt
        event_id = str(
            attrs.get("event_id")
            or _deterministic_id(
                "evt",
                trace_id or "",
                span_id or "",
                service_name,
                span.name,
                start_dt.isoformat(),
            )
        )
        return {
            "event_id": event_id,
            "trace_id": trace_id,
            "span_id": span_id,
            "service": service_name,
            "operation": span.name,
            "start_time": start_dt,
            "end_time": end_dt,
            "attributes": attrs,
        }

    def export_span_dicts(
        self,
        spans: Sequence[Mapping[str, Any]],
    ) -> ExportBatchResult:
        """Normalize span dictionaries into LedgerEventRow, StateTransitionRow, and stitched LedgerJourneyRow."""
        event_rows: list[dict[str, Any]] = []
        transition_rows: list[dict[str, Any]] = []
        touched_journeys: set[str] = set()

        for raw in spans:
            attrs = dict(raw.get("attributes") or {})
            service = str(raw.get("service") or attrs.get("service.name") or "unknown-service")
            operation = str(raw.get("operation") or raw.get("name") or "unknown-op")
            trace_id = raw.get("trace_id")
            span_id = raw.get("span_id")

            start_time = raw.get("start_time") or raw.get("event_time") or datetime.now(tz=UTC)
            if isinstance(start_time, str):
                start_time = datetime.fromisoformat(start_time)
            end_time = raw.get("end_time") or start_time
            if isinstance(end_time, str):
                end_time = datetime.fromisoformat(end_time)

            duration_ms = raw.get("duration_ms")
            if duration_ms is None:
                duration_ms = max(0, int((end_time - start_time).total_seconds() * 1000))

            event_id = str(
                raw.get("event_id")
                or attrs.get("event_id")
                or _deterministic_id(
                    "evt",
                    str(trace_id or ""),
                    str(span_id or ""),
                    service,
                    operation,
                    start_time.isoformat(),
                )
            )

            is_duplicate_event = event_id in self._seen_event_ids
            self._seen_event_ids.add(event_id)

            journey_id = attrs.get("journey.id") or raw.get("journey_id")
            customer_id = attrs.get("customer.id") or raw.get("customer_id")
            decision = attrs.get("decision") or raw.get("decision")
            status_from = (
                attrs.get("state_from")
                or attrs.get("status_from")
                or raw.get("state_from")
                or raw.get("status_from")
            )
            status_to = (
                attrs.get("state_to")
                or attrs.get("status_to")
                or raw.get("state_to")
                or raw.get("status_to")
            )
            event_type = str(attrs.get("event_type") or raw.get("event_type") or operation)
            entity_raw = (
                attrs.get("entity_type")
                or attrs.get("entity")
                or raw.get("entity_type")
                or raw.get("entity")
            )
            entity_id_raw = attrs.get("entity_id") or raw.get("entity_id") or journey_id
            http_status = (
                attrs.get("http.status_code") or attrs.get("http_status") or raw.get("http_status")
            )
            synthetic = bool(
                attrs.get("synthetic") if "synthetic" in attrs else raw.get("is_synthetic", False)
            )

            raw_source = str(attrs.get("source") or raw.get("source") or "otel").lower()
            source: LedgerEventSource = (
                "probe" if raw_source == "probe" else ("cdc" if raw_source == "cdc" else "otel")
            )

            numeric_attrs: dict[str, float] = {}
            extra_attrs: dict[str, Any] = {}
            for k, v in attrs.items():
                if k not in BUSINESS_SPAN_ATTRIBUTES and k != "http.status_code":
                    extra_attrs[k] = v
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    numeric_attrs[k] = float(v)

            if status_from is not None:
                extra_attrs["state_from"] = status_from
            if status_to is not None:
                extra_attrs["state_to"] = status_to

            ev_model = LedgerEventRow(
                event_id=event_id,
                event_time=end_time,
                env_id=self._env_id,
                journey_id=str(journey_id) if journey_id else None,
                trace_id=str(trace_id) if trace_id else None,
                span_id=str(span_id) if span_id else None,
                service=service,
                operation=operation,
                event_type=event_type,
                customer_id=str(customer_id) if customer_id else None,
                entity_type=str(entity_raw) if entity_raw else None,
                entity_id=str(entity_id_raw) if entity_id_raw else None,
                state_from=str(status_from) if status_from else None,
                state_to=str(status_to) if status_to else None,
                decision=str(decision) if decision else None,
                http_status=int(http_status) if http_status is not None else None,
                duration_ms=int(duration_ms),
                is_synthetic=synthetic,
                source=source,
                attributes=extra_attrs,
                numeric_attributes=numeric_attrs,
            )
            raw_dump = ev_model.model_dump()
            valid_cols = {
                "event_id", "event_time", "env_id", "journey_id", "trace_id", "span_id",
                "service", "operation", "event_type", "entity_type", "entity_id", "decision",
                "state_from", "state_to", "numeric_attributes", "currency", "http_status",
                "duration_ms", "is_synthetic", "source", "attributes",
            }
            ev_dict = {k: v for k, v in raw_dump.items() if k in valid_cols}
            event_rows.append(ev_dict)

            # Emit state_transitions row when status_to and journey_id are present (skip dwell mutation on duplicate retries)
            if status_to and journey_id and source in ("otel", "cdc") and not is_duplicate_event:
                j_str = str(journey_id)
                ent = _infer_entity(str(entity_raw) if entity_raw else None, event_type)
                ent_id = str(entity_id_raw or j_str)
                prev_ts = self._last_transition_time.get((j_str, ent))
                dwell = (
                    max(0, int((end_time - prev_ts).total_seconds() * 1000))
                    if prev_ts is not None
                    else int(duration_ms)
                )
                if prev_ts is None or end_time >= prev_ts:
                    self._last_transition_time[(j_str, ent)] = end_time
                transition_id = _deterministic_id("trn", event_id, j_str, str(status_to))
                st_model = StateTransitionRow(
                    transition_id=transition_id,
                    transition_time=end_time,
                    env_id=self._env_id,
                    entity=ent,
                    entity_id=ent_id,
                    journey_id=j_str,
                    status_from=str(status_from) if status_from else None,
                    status_to=str(status_to),
                    state_from=str(status_from) if status_from else None,
                    state_to=str(status_to),
                    actor_service=service,
                    source="cdc" if source == "cdc" else "otel",
                    dwell_ms=dwell,
                    is_synthetic=synthetic,
                    attributes=extra_attrs,
                )
                transition_rows.append(st_model.model_dump())

            # Stitch journey state by journey_id (exclude synthetic boundary probes without a journey)
            if journey_id and source != "probe":
                j_str = str(journey_id)
                acc = self._journeys.get(j_str)
                if acc is None:
                    acc = _JourneyAccumulator(
                        journey_id=j_str,
                        journey_name=str(attrs.get("journey.name") or self._default_journey_name),
                        env_id=self._env_id,
                        customer_id=str(customer_id or "unknown"),
                        started_at=start_time,
                        ended_at=end_time,
                        is_synthetic=synthetic,
                    )
                    self._journeys[j_str] = acc

                if event_id not in acc.seen_event_ids:
                    acc.seen_event_ids.add(event_id)
                    if customer_id:
                        acc.customer_id = str(customer_id)
                    if start_time < acc.started_at:
                        acc.started_at = start_time
                    if acc.ended_at is None or end_time > acc.ended_at:
                        acc.ended_at = end_time
                    acc.is_synthetic = acc.is_synthetic or synthetic
                    val = numeric_attrs.get("value") or numeric_attrs.get("amount")
                    if val is not None and Decimal(str(val)) > acc.total_value:
                        acc.total_value = Decimal(str(val))
                    step_status = str(status_to or decision or "OK")
                    acc.step_events.append(
                        {
                            "service": service,
                            "operation": operation,
                            "event_type": event_type,
                            "at": end_time,
                            "status": step_status,
                            "duration_ms": int(duration_ms),
                        }
                    )
                    touched_journeys.add(j_str)

        events_written = self._write_stream.append_rows("events", event_rows)
        transitions_written = (
            self._write_stream.append_rows("state_transitions", transition_rows)
            if transition_rows
            else 0
        )
        journeys_updated = self._flush_journeys(touched_journeys)
        return ExportBatchResult(
            events_written=events_written,
            transitions_written=transitions_written,
            journeys_updated=journeys_updated,
        )

    def _flush_journeys(self, journey_ids: set[str]) -> int:
        if not journey_ids:
            return 0
        updated_models: list[dict[str, Any]] = []
        for j_id in sorted(journey_ids):
            acc = self._journeys[j_id]
            ordered = sorted(acc.step_events, key=lambda s: s["at"])
            steps: list[JourneyStep] = []
            step_tokens: list[str] = []
            total_dur = 0
            for idx, item in enumerate(ordered, start=1):
                steps.append(
                    JourneyStep(
                        seq=idx,
                        service=item["service"],
                        operation=item["operation"],
                        at=item["at"],
                        status=item["status"],
                        duration_ms=item["duration_ms"],
                    )
                )
                tok = canonical_step_name(
                    event_type=item["event_type"],
                    operation=item["operation"],
                    service=item["service"],
                    status_to=item["status"],
                )
                step_tokens.append(tok)
                total_dur += int(item["duration_ms"])

            # Derive final_status from the chronologically latest step (handles out-of-order span arrival)
            chronological_final_status = ordered[-1]["status"] if ordered else acc.final_status
            acc.final_status = chronological_final_status

            observed_seq = "->".join(step_tokens)
            conformance = classify_journey_conformance(
                step_tokens,
                final_status=chronological_final_status,
                started_at=acc.started_at,
                last_step_at=acc.ended_at,
                now=acc.ended_at,
                stall_threshold_ms=self._stall_threshold_ms,
                intended_order=self._intended_order,
            )
            j_row = LedgerJourneyRow(
                journey_id=acc.journey_id,
                journey_name=acc.journey_name,
                started_at=acc.started_at,
                ended_at=acc.ended_at,
                env_id=acc.env_id,
                customer_id=acc.customer_id,
                steps=steps,
                observed_sequence=observed_seq,
                conformance=conformance,
                final_status=chronological_final_status,
                total_value=acc.total_value,
                total_duration_ms=total_dur,
                is_synthetic=acc.is_synthetic,
                journey_model_version="1.0",
            )
            updated_models.append(j_row.model_dump())

        if isinstance(self._bigquery, InMemoryBigQuery):
            existing_rows = {
                r["journey_id"]: r
                for r in self._bigquery.get_table("journeys")
                if r.get("journey_id") not in journey_ids
            }
            self._bigquery.clear_table("journeys")
            all_rows = list(existing_rows.values()) + updated_models
            return self._write_stream.append_rows("journeys", all_rows)

        return self._write_stream.append_rows("journeys", updated_models)
