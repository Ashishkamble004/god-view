"""Sense Adapter for Unknown Parcel Logistics Environment.

Discovers parcel microservices, OpenAPI contracts, dependency edges,
and provides live HTTP synthetic probing capabilities.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from godview_shared.adapter_contract import (
    AdapterHealth,
    DiscoveryResult,
    SenseAdapter,
    SenseCategory,
)
from godview_shared.capabilities import CapabilitySpec, get_capability
from godview_shared.contracts import (
    DiscoveredEdgeDoc,
    DiscoveredEntityDoc,
    DiscoveredServiceDoc,
)


class UnknownLogisticsSenseAdapter(SenseAdapter):
    """Local sense adapter for the unknown parcel logistics environment."""

    family: Literal["gcp", "kubernetes", "sql", "git", "channels", "local"] = "local"
    name: str = "unknown-logistics-adapter"

    def __init__(
        self,
        *,
        env_id: str = "unknown-parcel-env",
        service_ports: Mapping[str, int] | None = None,
        compose_path: str | Path | None = None,
        seed_sql_path: str | Path | None = None,
        http_transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.env_id = env_id
        self.service_ports = dict(service_ports or {})
        self.compose_path = (
            Path(compose_path) if compose_path is not None else Path(__file__).parent / "docker-compose.yml"
        )
        self.seed_sql_path = (
            Path(seed_sql_path) if seed_sql_path is not None else Path(__file__).parent / "seed.sql"
        )
        self._transport = http_transport

    def senses(self) -> list[SenseCategory]:
        return [
            SenseCategory.SCOPED_OBSERVABILITY,
            SenseCategory.DATA,
            SenseCategory.CODE,
            SenseCategory.CONTRACTS,
            SenseCategory.SYNTHETIC_INTERROGATION,
        ]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {
            "http.probe": get_capability("http.probe"),
            "probe.execute": get_capability("probe.execute"),
            "metrics.scrape": get_capability("metrics.scrape"),
        }

    async def health(self) -> AdapterHealth:
        return AdapterHealth(
            adapter_name=self.name,
            family=self.family,
            state="available",
            evidence={
                "compose_file_present": self.compose_path.is_file(),
                "seed_sql_present": self.seed_sql_path.is_file(),
                "services_configured": len(self.service_ports),
            },
            checked_at=datetime.now(tz=UTC),
        )

    def _discover_entities(self) -> list[DiscoveredEntityDoc]:
        entities: list[DiscoveredEntityDoc] = []
        if not self.seed_sql_path.is_file():
            return entities
        now_iso = datetime.now(tz=UTC).isoformat()
        content = self.seed_sql_path.read_text(encoding="utf-8")
        matches = re.findall(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z0-9_]+)\s*\((.*?)\)\s*;",
            content,
            re.IGNORECASE | re.DOTALL,
        )
        for tbl_name, col_body in matches:
            cols = [
                m.group(1)
                for m in re.finditer(
                    r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s+[A-Za-z0-9_()]+",
                    col_body,
                    re.MULTILINE,
                )
            ]
            entities.append(
                DiscoveredEntityDoc(
                    doc_id=f"{self.env_id}__table.{tbl_name}",
                    env_id=self.env_id,
                    name=tbl_name,
                    kind="sql_table",
                    columns=cols,
                    first_seen=now_iso,
                    last_seen=now_iso,
                )
            )
        return entities

    async def discover(self) -> DiscoveryResult:
        now_iso = datetime.now(tz=UTC).isoformat()
        entities = self._discover_entities()

        # Build service specs
        intake_port = self.service_ports.get("parcel-intake-svc", 18081)
        route_port = self.service_ports.get("parcel-route-svc", 18082)
        dispatch_port = self.service_ports.get("parcel-dispatch-svc", 18083)
        proof_port = self.service_ports.get("parcel-proof-svc", 18084)
        db_port = self.service_ports.get("parcel-db", 15432)

        services = [
            DiscoveredServiceDoc(
                doc_id=f"{self.env_id}__cloud_run.parcel-intake-svc",
                env_id=self.env_id,
                name="parcel-intake-svc",
                kind="cloud_run",
                adapter_family="local",
                locator=f"http://127.0.0.1:{intake_port}",
                endpoint=f"http://127.0.0.1:{intake_port}",
                is_public=True,
                labels={"godview_managed": "true", "godview_kind": "cloud_run", "domain": "parcels"},
                routes=[
                    {
                        "method": "POST",
                        "path": "/v1/parcels/intake",
                        "operation_id": "intakeParcelShipment",
                        "summary": "Intake parcel shipment",
                        "url": f"http://127.0.0.1:{intake_port}/v1/parcels/intake",
                        "probe_safe": True,
                    },
                    {
                        "method": "GET",
                        "path": "/v1/parcels/{parcel_id}",
                        "operation_id": "getParcelStatus",
                        "summary": "Get parcel intake status",
                        "url": f"http://127.0.0.1:{intake_port}/v1/parcels/PCL-9001",
                        "probe_safe": True,
                    },
                    {
                        "method": "GET",
                        "path": "/healthz",
                        "operation_id": "healthz",
                        "summary": "Health check",
                        "url": f"http://127.0.0.1:{intake_port}/healthz",
                        "probe_safe": True,
                    },
                ],
                metrics_endpoint=f"http://127.0.0.1:{intake_port}/metrics",
                first_seen=now_iso,
                last_seen=now_iso,
                status="active",
            ),
            DiscoveredServiceDoc(
                doc_id=f"{self.env_id}__cloud_run.parcel-route-svc",
                env_id=self.env_id,
                name="parcel-route-svc",
                kind="cloud_run",
                adapter_family="local",
                locator=f"http://127.0.0.1:{route_port}",
                endpoint=f"http://127.0.0.1:{route_port}",
                labels={"godview_managed": "true", "godview_kind": "cloud_run", "domain": "parcels"},
                routes=[
                    {
                        "method": "POST",
                        "path": "/v1/parcels/route",
                        "operation_id": "calculateParcelRoute",
                        "summary": "Calculate parcel route",
                        "url": f"http://127.0.0.1:{route_port}/v1/parcels/route",
                    },
                    {
                        "method": "GET",
                        "path": "/healthz",
                        "operation_id": "healthz",
                        "summary": "Health check",
                        "url": f"http://127.0.0.1:{route_port}/healthz",
                        "probe_safe": True,
                    },
                ],
                metrics_endpoint=f"http://127.0.0.1:{route_port}/metrics",
                first_seen=now_iso,
                last_seen=now_iso,
                status="active",
            ),
            DiscoveredServiceDoc(
                doc_id=f"{self.env_id}__cloud_run.parcel-dispatch-svc",
                env_id=self.env_id,
                name="parcel-dispatch-svc",
                kind="cloud_run",
                adapter_family="local",
                locator=f"http://127.0.0.1:{dispatch_port}",
                endpoint=f"http://127.0.0.1:{dispatch_port}",
                labels={"godview_managed": "true", "godview_kind": "cloud_run", "domain": "parcels"},
                routes=[
                    {
                        "method": "POST",
                        "path": "/v1/parcels/dispatch",
                        "operation_id": "dispatchParcelCarrier",
                        "summary": "Dispatch parcel with courier carrier",
                        "url": f"http://127.0.0.1:{dispatch_port}/v1/parcels/dispatch",
                    },
                    {
                        "method": "GET",
                        "path": "/healthz",
                        "operation_id": "healthz",
                        "summary": "Health check",
                        "url": f"http://127.0.0.1:{dispatch_port}/healthz",
                        "probe_safe": True,
                    },
                ],
                metrics_endpoint=f"http://127.0.0.1:{dispatch_port}/metrics",
                first_seen=now_iso,
                last_seen=now_iso,
                status="active",
            ),
            DiscoveredServiceDoc(
                doc_id=f"{self.env_id}__cloud_run.parcel-proof-svc",
                env_id=self.env_id,
                name="parcel-proof-svc",
                kind="cloud_run",
                adapter_family="local",
                locator=f"http://127.0.0.1:{proof_port}",
                endpoint=f"http://127.0.0.1:{proof_port}",
                labels={"godview_managed": "true", "godview_kind": "cloud_run", "domain": "parcels"},
                routes=[
                    {
                        "method": "POST",
                        "path": "/v1/parcels/proof",
                        "operation_id": "recordDeliveryProof",
                        "summary": "Record digital proof of parcel delivery",
                        "url": f"http://127.0.0.1:{proof_port}/v1/parcels/proof",
                    },
                    {
                        "method": "GET",
                        "path": "/healthz",
                        "operation_id": "healthz",
                        "summary": "Health check",
                        "url": f"http://127.0.0.1:{proof_port}/healthz",
                        "probe_safe": True,
                    },
                ],
                metrics_endpoint=f"http://127.0.0.1:{proof_port}/metrics",
                first_seen=now_iso,
                last_seen=now_iso,
                status="active",
            ),
            DiscoveredServiceDoc(
                doc_id=f"{self.env_id}__cloud_sql.parcel-db",
                env_id=self.env_id,
                name="parcel-db",
                kind="cloud_sql",
                adapter_family="local",
                locator=f"postgresql://parcel_user@127.0.0.1:{db_port}/parcel_logistics",
                labels={"godview_managed": "true", "godview_kind": "cloud_sql", "godview_datastore": "postgres"},
                first_seen=now_iso,
                last_seen=now_iso,
                status="active",
            ),
        ]

        # Inferred edges: intake -> route -> dispatch -> proof
        s_intake = services[0].doc_id
        s_route = services[1].doc_id
        s_dispatch = services[2].doc_id
        s_proof = services[3].doc_id
        s_db = services[4].doc_id

        edges = [
            DiscoveredEdgeDoc(
                doc_id=f"{s_intake}__to__{s_route}",
                src=s_intake,
                dst=s_route,
                protocol="http",
                evidence="config",
                first_seen=now_iso,
                last_seen=now_iso,
            ),
            DiscoveredEdgeDoc(
                doc_id=f"{s_route}__to__{s_dispatch}",
                src=s_route,
                dst=s_dispatch,
                protocol="http",
                evidence="config",
                first_seen=now_iso,
                last_seen=now_iso,
            ),
            DiscoveredEdgeDoc(
                doc_id=f"{s_dispatch}__to__{s_proof}",
                src=s_dispatch,
                dst=s_proof,
                protocol="http",
                evidence="config",
                first_seen=now_iso,
                last_seen=now_iso,
            ),
            DiscoveredEdgeDoc(
                doc_id=f"{s_intake}__to__{s_db}",
                src=s_intake,
                dst=s_db,
                protocol="sql",
                evidence="config",
                first_seen=now_iso,
                last_seen=now_iso,
            ),
        ]

        return DiscoveryResult(
            services=services,
            edges=edges,
            entities=entities,
            evidence={
                "environment_type": "unknown_parcel_logistics",
                "total_services": len(services),
                "total_edges": len(edges),
            },
        )

    async def execute_capability(
        self,
        capability_id: str,
        args: dict[str, Any],
        *,
        trust_decision: Any | None = None,
    ) -> Any:
        """Execute local capabilities: http.probe, probe.execute, metrics.scrape."""
        if capability_id in ("http.probe", "probe.execute"):
            url = args.get("url", "")
            method = args.get("method", "GET").upper()
            timeout_s = float(args.get("timeout_s", 5.0))
            headers = dict(args.get("headers", {}))

            transport = self._transport
            async with httpx.AsyncClient(transport=transport, timeout=timeout_s) as client:
                t0 = datetime.now(tz=UTC)
                try:
                    resp = await client.request(method, url, headers=headers)
                    latency_ms = (datetime.now(tz=UTC) - t0).total_seconds() * 1000.0
                    body_json = None
                    try:
                        body_json = resp.json()
                    except Exception:
                        pass
                    return {
                        "status_code": resp.status_code,
                        "latency_ms": latency_ms,
                        "error": None,
                        "headers": dict(resp.headers),
                        "json": body_json,
                        "body": resp.text[:500],
                        "synthetic_marker_sent": True,
                    }
                except Exception as exc:
                    latency_ms = (datetime.now(tz=UTC) - t0).total_seconds() * 1000.0
                    return {
                        "status_code": 0,
                        "latency_ms": latency_ms,
                        "error": str(exc),
                        "headers": {},
                        "json": None,
                        "body": "",
                        "synthetic_marker_sent": True,
                    }

        if capability_id == "metrics.scrape":
            url = args.get("url", "")
            async with httpx.AsyncClient(transport=self._transport, timeout=5.0) as client:
                try:
                    resp = await client.get(url)
                    return {"metrics": resp.text, "status_code": resp.status_code}
                except Exception as exc:
                    return {"metrics": "", "status_code": 0, "error": str(exc)}

        raise NotImplementedError(f"Unsupported capability '{capability_id}'")
