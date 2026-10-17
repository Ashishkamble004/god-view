"""16 GCP SenseAdapter implementations and Vertex AI model endpoint resolver.

Implements SenseAdapter over `httpx` REST transports (live or recorded fixture transport) for:
1. CloudAssetInventoryAdapter (Asset Inventory, `asset.list`)
2. CloudRunV2Adapter (Cloud Run v2, `run.restore_revision_config`, `metrics.scrape` with OIDC + Monitoring fallback, `probe.execute` with `x-godview-probe-safe` & `X-Synthetic: true`)
3. GkeAdapter (GKE Autopilot, `k8s.*` via `GkeMcpClient`)
4. ComputeEngineAdapter (Compute Engine VMs)
5. CloudSqlAdminAdapter (Cloud SQL Admin)
6. CloudMonitoringAdapter (Cloud Monitoring, `obs.list_timeseries` via `ObservabilityMcpClient`)
7. CloudTraceAdapter (Cloud Trace, `obs.list_traces`, `obs.get_trace`)
8. CloudLoggingAuditAdapter (Cloud Logging & Audit sink, `obs.query_logs_scoped` `pageSize <= 50` + PII-in-logs detector)
9. PubSubAdapter (Pub/Sub `godview-nervous-system` + DLQ)
10. BigQueryAdapter (BigQuery `godview_ledger` & billing export, `ledger.query`, `cost.query` with `sqlglot` AST validation)
11. DatastreamAdapter (Phase 2 stub reporting `unavailable`)
12. FirestoreAdapter (Firestore Native DB `god-view-memory-db`)
13. AgentEngineAdapter (Vertex AI Agent Engine, `agent.set_min_instances` 0↔1 PATCH + UC-23 idle engine cost findings)
14. AgentRegistryAdapter (Optional: discovered, never assumed; falls back to Firestore `court_agents`)
15. ModelArmorAdapter (Model Armor text-only `sanitizeUserPrompt`, rejecting image bytes)
16. CloudIamAdapter (Cloud IAM service accounts, `testIamPermissions`, `godview-act-sa` impersonation)
"""

from __future__ import annotations

import copy
import inspect
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import sqlglot
from godview_shared.adapter_contract import (
    AdapterHealth,
    DiscoveryResult,
    SenseAdapter,
    SenseCategory,
)
from godview_shared.capabilities import CapabilitySpec, get_capability
from godview_shared.config import validate_model_location
from godview_shared.contracts import (
    CostFindingDoc,
    DiscoveredEdgeDoc,
    DiscoveredServiceDoc,
    DiscoveredServiceKind,
    TrustDecision,
    TrustViolation,
)
from godview_shared.otel import inject_baggage_headers
from sqlglot import exp

from godview.adapters.gcp.drift import (
    TerraformDriftAdapter,
    UnmanagedResourceError,
    assert_managed_resource,
    assert_valid_mutating_trust,
    build_fixture_http_transport,
    is_resource_excluded,
    resolve_gcp_bearer_token,
    resolve_gcp_project_id,
)
from godview.adapters.gcp.mcp_clients import GkeMcpClient, ObservabilityMcpClient


class ProbeSafetyError(PermissionError):
    """Raised when `probe.execute` is called on a route without `x-godview-probe-safe: true` (TDD I-07)."""


@dataclass(frozen=True)
class ModelEndpointSpec:
    """Resolved Vertex AI publisher model endpoint URL and required headers (Coordinator #64, TDD I-09)."""

    model_id: str
    location: str
    url: str
    headers: dict[str, str]


class VertexModelEndpointClient:
    """Resolves Vertex AI publisher model endpoints enforcing Gemini 3.x/4 at `global` and `x-goog-user-project`."""

    def __init__(self, project_id: str) -> None:
        if not project_id or not project_id.strip():
            raise ValueError("VertexModelEndpointClient requires a non-empty project_id")
        self.project_id = project_id.strip()

    def build_model_endpoint(self, model_id: str, location: str) -> ModelEndpointSpec:
        validate_model_location(model_id, location)
        if location == "global":
            url = (
                f"https://aiplatform.googleapis.com/v1beta1/projects/{self.project_id}/"
                f"locations/global/publishers/google/models/{model_id}"
            )
        else:
            url = (
                f"https://{location}-aiplatform.googleapis.com/v1/projects/{self.project_id}/"
                f"locations/{location}/publishers/google/models/{model_id}"
            )
        return ModelEndpointSpec(
            model_id=model_id,
            location=location,
            url=url,
            headers={"x-goog-user-project": self.project_id},
        )


def _validate_single_select_sql(statement: str) -> str:
    """Validate with sqlglot that `statement` is a single read-only SELECT/UNION query."""
    cleaned = statement.strip()
    if not cleaned:
        raise ValueError("BigQuery SQL statement must not be empty")
    try:
        parsed = sqlglot.parse(cleaned, read="bigquery")
    except Exception as exc:
        raise ValueError(f"Unparseable BigQuery SQL statement: {exc}") from exc

    non_empty = [stmt for stmt in parsed if stmt is not None]
    if len(non_empty) != 1:
        raise ValueError(
            f"BigQuery query must contain exactly 1 SELECT statement (got {len(non_empty)})"
        )
    root = non_empty[0]
    if not isinstance(root, (exp.Select, exp.Union)):
        raise ValueError(
            f"BigQuery query only permits read-only SELECT statements (got {type(root).__name__})"
        )
    forbidden_nodes = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Drop,
        exp.Alter,
        exp.Create,
        exp.Command,
        exp.Transaction,
        exp.Grant,
        exp.Revoke,
    )
    for node in root.walk():
        if isinstance(node, forbidden_nodes):
            raise ValueError(
                f"Forbidden mutating/DDL SQL expression '{type(node).__name__}' in read-only query"
            )
    return cleaned


class _BaseGcpAdapter(SenseAdapter):
    family = "gcp"

    def __init__(
        self,
        *,
        project_id: str | None = None,
        region: str = "us-central1",
        zone: str = "us-central1-a",
        bearer_token: str | None = None,
        fixtures: dict[str, Any] | None = None,
        http_transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._fixtures = fixtures
        self.project_id = resolve_gcp_project_id(project_id, fixtures)
        self.region = region or str((fixtures or {}).get("region", "us-central1"))
        self.zone = zone or str((fixtures or {}).get("zone", "us-central1-a"))
        self.bearer_token = resolve_gcp_bearer_token(bearer_token)
        self._transport = http_transport or (
            build_fixture_http_transport(fixtures) if fixtures is not None else None
        )
        self.env_id = f"gcp-{self.project_id}"

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "x-goog-user-project": self.project_id,
        }
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return headers

    def _http_client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"headers": self._headers(), "timeout": 15.0}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    def _sync_http_client(self) -> httpx.Client:
        kwargs: dict[str, Any] = {"headers": self._headers(), "timeout": 15.0}
        if isinstance(self._transport, httpx.BaseTransport):
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)


# ------------------------------------------------------------------------------
# 1. Cloud Asset Inventory Adapter
# ------------------------------------------------------------------------------


class CloudAssetInventoryAdapter(_BaseGcpAdapter):
    name = "gcp-asset-inventory"

    async def _fetch_assets(self) -> list[dict[str, Any]]:
        url = f"https://cloudasset.googleapis.com/v1/projects/{self.project_id}/assets"
        async with self._http_client() as client:
            resp = await client.get(url, params={"contentType": "RESOURCE"})
            resp.raise_for_status()
            return list(resp.json().get("assets", []))

    async def discover(self) -> DiscoveryResult:
        raw_assets = await self._fetch_assets()
        now_iso = datetime.now(tz=UTC).isoformat()
        services: list[DiscoveredServiceDoc] = []

        asset_kind_map: dict[str, DiscoveredServiceKind] = {
            "run.googleapis.com/Service": "cloud_run",
            "container.googleapis.com/Cluster": "gke_workload",
            "compute.googleapis.com/Instance": "gce_vm",
            "sqladmin.googleapis.com/Instance": "cloud_sql",
            "storage.googleapis.com/Bucket": "gcs_bucket",
            "pubsub.googleapis.com/Topic": "pubsub_topic",
            "bigquery.googleapis.com/Dataset": "bigquery_dataset",
        }

        for asset in raw_assets:
            asset_type = str(asset.get("assetType", ""))
            full_name = str(asset.get("name", ""))
            short_name = full_name.rsplit("/", 1)[-1]
            res_data = asset.get("resource", {}).get("data", {})
            labels = dict(res_data.get("labels") or res_data.get("userLabels") or {})

            if is_resource_excluded(short_name, labels):
                continue

            kind = asset_kind_map.get(asset_type)
            if kind is None:
                continue

            locator = str(res_data.get("uri") or full_name)
            services.append(
                DiscoveredServiceDoc(
                    doc_id=f"{self.env_id}__{kind}.{short_name}",
                    env_id=self.env_id,
                    name=short_name,
                    kind=kind,
                    adapter_family="gcp",
                    locator=locator,
                    labels=labels,
                    classification="other_read_only",
                    first_seen=now_iso,
                    last_seen=now_iso,
                )
            )

        return DiscoveryResult(
            services=services,
            evidence={"total_assets_scanned": len(raw_assets), "retained_services": len(services)},
        )

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.CHANGE_PLANE, SenseCategory.SCOPED_OBSERVABILITY]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {"asset.list": get_capability("asset.list")}

    async def health(self) -> AdapterHealth:
        try:
            assets = await self._fetch_assets()
            state = "available"
            count = len(assets)
        except httpx.HTTPError:
            state = "unavailable"
            count = 0
        return AdapterHealth(
            adapter_name=self.name,
            family="gcp",
            state=state,  # type: ignore[arg-type]
            evidence={"api": "cloudasset.googleapis.com/v1", "assets_count": count},
            checked_at=datetime.now(tz=UTC),
        )

    async def _execute_capability_impl(
        self,
        capability_id: str,
        args: dict[str, Any],
        trust_decision: TrustDecision | None = None,
    ) -> Any:
        if capability_id == "asset.list":
            disc = await self.discover()
            asset_type_filter = args.get("asset_type")
            items = [s.model_dump(mode="json") for s in disc.services]
            if asset_type_filter:
                items = [i for i in items if i["kind"] == asset_type_filter]
            return {"assets": items, "count": len(items)}
        raise NotImplementedError(capability_id)


# ------------------------------------------------------------------------------
# 2. Cloud Run v2 Adapter (`run.restore_revision_config`, `metrics.scrape`, `probe.execute`)
# ------------------------------------------------------------------------------


class CloudRunV2Adapter(_BaseGcpAdapter):
    name = "gcp-cloud-run-v2"

    def __init__(
        self,
        *,
        project_id: str | None = None,
        region: str = "us-central1",
        fixtures: dict[str, Any] | None = None,
        http_transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
        http_metrics_fetcher: Callable[[str, dict[str, str]], Awaitable[str] | str] | None = None,
        id_token_provider: Callable[[str], str] | None = None,
        observability_mcp: ObservabilityMcpClient | None = None,
        fallback_timeseries: list[dict[str, Any]] | None = None,
        probe_sender: (
            Callable[
                [str, str, dict[str, str], dict[str, Any]],
                Awaitable[dict[str, Any]] | dict[str, Any],
            ]
            | None
        ) = None,
    ) -> None:
        super().__init__(
            project_id=project_id,
            region=region,
            fixtures=fixtures,
            http_transport=http_transport,
        )
        self._http_metrics_fetcher = http_metrics_fetcher
        self._id_token_provider = id_token_provider or (
            lambda audience: f"eyJhbGciOiJSUzI1NiJ9.sense-sa.{audience}"
        )
        self._obs_mcp = observability_mcp
        self._fallback_timeseries = fallback_timeseries or []
        self._probe_sender = probe_sender

    async def _fetch_services(self) -> list[dict[str, Any]]:
        url = f"https://run.googleapis.com/v2/projects/{self.project_id}/locations/{self.region}/services"
        async with self._http_client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return list(resp.json().get("services", []))

    async def discover(self) -> DiscoveryResult:
        raw_services = await self._fetch_services()
        now_iso = datetime.now(tz=UTC).isoformat()
        services: list[DiscoveredServiceDoc] = []

        for svc in raw_services:
            full_name = str(svc.get("name", ""))
            short_name = full_name.rsplit("/", 1)[-1]
            labels = dict(svc.get("labels") or {})

            if is_resource_excluded(short_name, labels):
                continue

            uri = str(svc.get("uri", full_name))
            services.append(
                DiscoveredServiceDoc(
                    doc_id=f"{self.env_id}__cloud_run.{short_name}",
                    env_id=self.env_id,
                    name=short_name,
                    kind="cloud_run",
                    adapter_family="gcp",
                    locator=uri,
                    labels=labels,
                    metrics_endpoint=f"{uri.rstrip('/')}/metrics"
                    if uri.startswith("http")
                    else None,
                    classification="other_read_only",
                    first_seen=now_iso,
                    last_seen=now_iso,
                )
            )
        return DiscoveryResult(
            services=services,
            evidence={"cloud_run_services": len(services)},
        )

    def senses(self) -> list[SenseCategory]:
        return [
            SenseCategory.SCOPED_OBSERVABILITY,
            SenseCategory.SYNTHETIC_INTERROGATION,
            SenseCategory.CHANGE_PLANE,
        ]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {
            "metrics.scrape": get_capability("metrics.scrape"),
            "probe.execute": get_capability("probe.execute"),
            "run.restore_revision_config": get_capability("run.restore_revision_config"),
        }

    async def health(self) -> AdapterHealth:
        try:
            await self._fetch_services()
            state = "available"
        except httpx.HTTPError:
            state = "unavailable"
        return AdapterHealth(
            adapter_name=self.name,
            family="gcp",
            state=state,  # type: ignore[arg-type]
            evidence={"api": "run.googleapis.com/v2", "region": self.region},
            checked_at=datetime.now(tz=UTC),
        )

    async def _execute_capability_impl(
        self,
        capability_id: str,
        args: dict[str, Any],
        trust_decision: TrustDecision | None = None,
    ) -> Any:
        if capability_id == "metrics.scrape":
            return await self._scrape_metrics_with_fallback(args)
        if capability_id == "probe.execute":
            return await self._execute_synthetic_probe(args)
        if capability_id == "run.restore_revision_config":
            return await self._restore_revision_config_async(args, trust_decision)
        raise NotImplementedError(capability_id)

    async def _scrape_metrics_with_fallback(self, args: dict[str, Any]) -> dict[str, Any]:
        """Scrape `/metrics` with OIDC ID token (`godview-sense-sa`) or fall back to `obs.list_timeseries` (A5)."""
        url = str(args.get("url", ""))
        service_name = str(args.get("service", "unknown"))
        audience = url.split("/metrics")[0] if "/metrics" in url else url
        id_token = self._id_token_provider(audience)
        headers = {"Authorization": f"Bearer {id_token}"}

        try:
            if self._http_metrics_fetcher is None:
                raise ConnectionError("No HTTP metrics fetcher configured")
            raw_res = self._http_metrics_fetcher(url, headers)
            body = await raw_res if inspect.isawaitable(raw_res) else raw_res
            p95_ms = 0.0
            total_2xx = 0.0
            total_5xx = 0.0
            for line in body.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "duration_p95_ms" in stripped:
                    p95_ms = float(stripped.split()[-1])
                elif "godview_http_requests_total" in stripped:
                    val = float(stripped.split()[-1])
                    if 'status="5' in stripped:
                        total_5xx += val
                    else:
                        total_2xx += val
            total = total_2xx + total_5xx
            err_pct = (total_5xx / total * 100.0) if total > 0 else 0.0
            status = "red" if (p95_ms > 3500.0 or err_pct > 2.0) else "green"
            return {
                "source": "prometheus_scrape",
                "mttd_sla_seconds": 15,
                "url": url,
                "golden_signals": {
                    "status": status,
                    "p95_ms": p95_ms,
                    "error_rate_pct": round(err_pct, 3),
                },
            }
        except Exception as exc:
            # Fallback per Coordinator Decision A5 & TDD §9 #10: Cloud Monitoring list_timeseries (<= 4 min MTTD)
            series_list: list[dict[str, Any]] = []
            if self._obs_mcp is not None:
                mcp_res = await self._obs_mcp.list_timeseries(
                    filter_str=f'resource.type="cloud_run_revision" AND resource.labels.service_name="{service_name}"'
                )
                if isinstance(mcp_res, dict) and isinstance(mcp_res.get("timeSeries"), list):
                    series_list = list(mcp_res["timeSeries"])
                elif isinstance(mcp_res, list):
                    series_list = list(mcp_res)

            if not series_list and self._fallback_timeseries:
                series_list = list(self._fallback_timeseries)

            if not series_list:
                return {
                    "source": "cloud_monitoring_fallback",
                    "fallback_reason": str(exc),
                    "mttd_sla_seconds": 240,
                    "service": service_name,
                    "golden_signals": {
                        "status": "unavailable",
                        "p95_ms": None,
                        "error_rate_pct": None,
                    },
                }

            sample = series_list[0]
            if "p95_ms" in sample or "error_rate_pct" in sample:
                p95_ms = float(sample.get("p95_ms", 0.0))
                err_pct = float(sample.get("error_rate_pct", 0.0))
            else:
                points = sample.get("points", [])
                val_obj = points[0].get("value", {}) if points else {}
                p95_ms = float(val_obj.get("doubleValue", 0.0))
                err_pct = float(sample.get("errorRatePct", 0.0))

            status = "red" if (p95_ms > 3500.0 or err_pct > 2.0) else "green"
            return {
                "source": "cloud_monitoring_fallback",
                "fallback_reason": str(exc),
                "mttd_sla_seconds": 240,
                "service": service_name,
                "golden_signals": {
                    "status": status,
                    "p95_ms": p95_ms,
                    "error_rate_pct": err_pct,
                },
            }

    async def _execute_synthetic_probe(self, args: dict[str, Any]) -> dict[str, Any]:
        """Execute synthetic probe only on routes marked `probe_safe=True` with `X-Synthetic: true` (TDD I-07)."""
        if not args.get("probe_safe", False):
            raise ProbeSafetyError(
                f"Refusing to probe route '{args.get('url')}': route is not marked x-godview-probe-safe: true (TDD I-07)"
            )
        method = str(args.get("method", "POST")).upper()
        url = str(args.get("url", "")).strip()
        if not url:
            raise ValueError("probe.execute requires a non-empty target url")
        headers = inject_baggage_headers(
            journey_id=str(args.get("journey_id", "probe-journey")),
            customer_id=str(args.get("customer_id", "probe-customer")),
            synthetic=True,
        )
        body = copy.deepcopy(dict(args.get("body") or {}))
        body["is_ai_generated"] = True

        if self._probe_sender is not None:
            raw = self._probe_sender(method, url, headers, body)
            return await raw if inspect.isawaitable(raw) else raw

        async with self._http_client() as client:
            resp = await client.request(method, url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                return data
            return {"status_code": resp.status_code, "data": data}

    def _lookup_live_service_labels_sync(self, service_name: str) -> dict[str, str] | None:
        short = service_name.rsplit("/", 1)[-1]
        url = f"https://run.googleapis.com/v2/projects/{self.project_id}/locations/{self.region}/services"
        try:
            with self._sync_http_client() as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    for svc in resp.json().get("services", []):
                        svc_short = str(svc.get("name", "")).rsplit("/", 1)[-1]
                        if svc_short == short:
                            return dict(svc.get("labels") or {})
        except httpx.HTTPError:
            pass
        return None

    async def _lookup_live_service_labels_async(self, service_name: str) -> dict[str, str] | None:
        short = service_name.rsplit("/", 1)[-1]
        for svc in await self._fetch_services():
            svc_short = str(svc.get("name", "")).rsplit("/", 1)[-1]
            if svc_short == short:
                return dict(svc.get("labels") or {})
        return None

    def _restore_revision_config(
        self,
        args: dict[str, Any],
        trust_decision: TrustDecision | None,
    ) -> dict[str, Any]:
        """Synchronous defence-in-depth entrypoint for `run.restore_revision_config`."""
        td = assert_valid_mutating_trust("run.restore_revision_config", trust_decision)
        service_name = str(args.get("service_name", ""))
        caller_labels = (
            dict(args["labels"]) if "labels" in args and args["labels"] is not None else None
        )
        live_labels = self._lookup_live_service_labels_sync(service_name)
        assert_managed_resource(caller_labels, service_name, live_labels=live_labels)

        desired_config = dict(args.get("desired_config") or {})
        previous_config = dict(args.get("previous_config") or {})
        update_mask = "template.scaling,template.containers,template.maxInstanceRequestConcurrency"
        short_name = service_name.rsplit("/", 1)[-1]
        patch_url = (
            f"https://run.googleapis.com/v2/projects/{self.project_id}/"
            f"locations/{self.region}/services/{short_name}"
        )
        with self._sync_http_client() as client:
            resp = client.patch(patch_url, params={"updateMask": update_mask}, json=desired_config)
            resp.raise_for_status()
            api_response = resp.json()

        effective_labels = dict(live_labels if live_labels is not None else (caller_labels or {}))
        return {
            "restored": True,
            "service_name": service_name,
            "update_mask": update_mask,
            "applied_config": desired_config,
            "api_response": api_response,
            "sa_used": td.sa_to_impersonate,
            "reversal_recipe": {
                "capability": "run.restore_revision_config",
                "args": {
                    "service_name": service_name,
                    "desired_config": previous_config,
                    "labels": effective_labels,
                },
            },
        }

    async def _restore_revision_config_async(
        self,
        args: dict[str, Any],
        trust_decision: TrustDecision | None,
    ) -> dict[str, Any]:
        """Async execution of `run.restore_revision_config` over `httpx.AsyncClient`."""
        td = assert_valid_mutating_trust("run.restore_revision_config", trust_decision)
        service_name = str(args.get("service_name", ""))
        caller_labels = (
            dict(args["labels"]) if "labels" in args and args["labels"] is not None else None
        )
        live_labels = await self._lookup_live_service_labels_async(service_name)
        assert_managed_resource(caller_labels, service_name, live_labels=live_labels)

        desired_config = dict(args.get("desired_config") or {})
        previous_config = dict(args.get("previous_config") or {})
        update_mask = "template.scaling,template.containers,template.maxInstanceRequestConcurrency"
        short_name = service_name.rsplit("/", 1)[-1]
        patch_url = (
            f"https://run.googleapis.com/v2/projects/{self.project_id}/"
            f"locations/{self.region}/services/{short_name}"
        )
        async with self._http_client() as client:
            resp = await client.patch(
                patch_url, params={"updateMask": update_mask}, json=desired_config
            )
            resp.raise_for_status()
            api_response = resp.json()

        effective_labels = dict(live_labels if live_labels is not None else (caller_labels or {}))
        return {
            "restored": True,
            "service_name": service_name,
            "update_mask": update_mask,
            "applied_config": desired_config,
            "api_response": api_response,
            "sa_used": td.sa_to_impersonate,
            "reversal_recipe": {
                "capability": "run.restore_revision_config",
                "args": {
                    "service_name": service_name,
                    "desired_config": previous_config,
                    "labels": effective_labels,
                },
            },
        }


# ------------------------------------------------------------------------------
# 3. GKE Autopilot Adapter (`k8s.*` via `GkeMcpClient`)
# ------------------------------------------------------------------------------


class GkeAdapter(_BaseGcpAdapter):
    name = "gcp-gke"

    def __init__(
        self,
        *,
        project_id: str | None = None,
        region: str = "us-central1",
        fixtures: dict[str, Any] | None = None,
        http_transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
        gke_mcp: GkeMcpClient | None = None,
    ) -> None:
        super().__init__(
            project_id=project_id,
            region=region,
            fixtures=fixtures,
            http_transport=http_transport,
        )
        self._gke_mcp = gke_mcp

    async def _fetch_clusters(self) -> list[dict[str, Any]]:
        url = f"https://container.googleapis.com/v1/projects/{self.project_id}/locations/{self.region}/clusters"
        async with self._http_client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return list(resp.json().get("clusters", []))

    async def discover(self) -> DiscoveryResult:
        clusters = await self._fetch_clusters()
        now_iso = datetime.now(tz=UTC).isoformat()
        services: list[DiscoveredServiceDoc] = []

        for cl in clusters:
            cl_name = str(cl.get("name", ""))
            labels = dict(cl.get("resourceLabels") or {})
            if is_resource_excluded(cl_name, labels):
                continue
            services.append(
                DiscoveredServiceDoc(
                    doc_id=f"{self.env_id}__gke_workload.{cl_name}",
                    env_id=self.env_id,
                    name=cl_name,
                    kind="gke_workload",
                    adapter_family="gcp",
                    locator=str(cl.get("endpoint") or cl_name),
                    labels=labels,
                    classification="other_read_only",
                    first_seen=now_iso,
                    last_seen=now_iso,
                )
            )
        return DiscoveryResult(
            services=services,
            evidence={"clusters_count": len(clusters)},
        )

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.SCOPED_OBSERVABILITY, SenseCategory.CHANGE_PLANE]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {
            "k8s.list_clusters": get_capability("k8s.list_clusters"),
            "k8s.get_cluster": get_capability("k8s.get_cluster"),
            "k8s.get_resource": get_capability("k8s.get_resource"),
            "k8s.query_logs": get_capability("k8s.query_logs"),
            "k8s.get_log_schema": get_capability("k8s.get_log_schema"),
            "k8s.list_recommendations": get_capability("k8s.list_recommendations"),
        }

    async def health(self) -> AdapterHealth:
        try:
            await self._fetch_clusters()
            state = "available"
        except httpx.HTTPError:
            state = "unavailable"
        return AdapterHealth(
            adapter_name=self.name,
            family="gcp",
            state=state,  # type: ignore[arg-type]
            evidence={"api": "container.googleapis.com/v1", "mcp": "gke-mcp"},
            checked_at=datetime.now(tz=UTC),
        )

    async def _execute_capability_impl(
        self,
        capability_id: str,
        args: dict[str, Any],
        trust_decision: TrustDecision | None = None,
    ) -> Any:
        if capability_id == "k8s.list_clusters":
            if self._gke_mcp is not None:
                return await self._gke_mcp.list_clusters(
                    project_id=args.get("project_id", self.project_id),
                    location=args.get("location", self.region),
                )
            return {"clusters": await self._fetch_clusters()}

        if capability_id == "k8s.get_cluster":
            cluster_name = str(args.get("name", "")).strip()
            if not cluster_name:
                raise ValueError("k8s.get_cluster requires a non-empty cluster 'name'")
            if self._gke_mcp is not None:
                return await self._gke_mcp.get_cluster(
                    name=cluster_name,
                    project_id=args.get("project_id", self.project_id),
                    location=args.get("location", self.region),
                )
            url = (
                f"https://container.googleapis.com/v1/projects/{self.project_id}/"
                f"locations/{self.region}/clusters/{cluster_name}"
            )
            async with self._http_client() as client:
                resp = await client.get(url)
                if resp.status_code == 404:
                    raise LookupError(f"GKE cluster '{cluster_name}' not found")
                resp.raise_for_status()
                return resp.json()

        if capability_id == "k8s.query_logs":
            query = str(args.get("query", ""))
            limit = int(args.get("limit", 50))
            if limit < 1 or limit > 50:
                raise ValueError(f"k8s.query_logs enforces limit <= 50 (got {limit})")
            if self._gke_mcp is None:
                raise RuntimeError("Capability 'k8s.query_logs' requires an active GkeMcpClient")
            return await self._gke_mcp.query_logs(query=query, limit=limit)

        if capability_id == "k8s.get_resource":
            if self._gke_mcp is None:
                raise RuntimeError("Capability 'k8s.get_resource' requires an active GkeMcpClient")
            return await self._gke_mcp.get_k8s_resource(
                kind=str(args.get("kind", "")),
                name=str(args.get("name", "")),
                namespace=args.get("namespace"),
                cluster_name=args.get("cluster_name"),
            )

        if capability_id == "k8s.get_log_schema":
            if self._gke_mcp is None:
                raise RuntimeError(
                    "Capability 'k8s.get_log_schema' requires an active GkeMcpClient"
                )
            return await self._gke_mcp.get_log_schema()

        if capability_id == "k8s.list_recommendations":
            if self._gke_mcp is None:
                raise RuntimeError(
                    "Capability 'k8s.list_recommendations' requires an active GkeMcpClient"
                )
            return await self._gke_mcp.list_recommendations(
                project_id=args.get("project_id", self.project_id),
                location=args.get("location", self.region),
            )

        raise NotImplementedError(capability_id)


# ------------------------------------------------------------------------------
# 4. Compute Engine Adapter
# ------------------------------------------------------------------------------


class ComputeEngineAdapter(_BaseGcpAdapter):
    name = "gcp-compute-engine"

    async def _fetch_instances(self) -> list[dict[str, Any]]:
        url = (
            f"https://compute.googleapis.com/compute/v1/projects/{self.project_id}/"
            f"zones/{self.zone}/instances"
        )
        async with self._http_client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return list(resp.json().get("items", []))

    async def discover(self) -> DiscoveryResult:
        items = await self._fetch_instances()
        now_iso = datetime.now(tz=UTC).isoformat()
        services: list[DiscoveredServiceDoc] = []

        for vm in items:
            vm_name = str(vm.get("name", ""))
            labels = dict(vm.get("labels") or {})
            if is_resource_excluded(vm_name, labels):
                continue
            net_ifs = vm.get("networkInterfaces") or [{}]
            priv_ip = str(net_ifs[0].get("networkIP", vm_name))
            services.append(
                DiscoveredServiceDoc(
                    doc_id=f"{self.env_id}__gce_vm.{vm_name}",
                    env_id=self.env_id,
                    name=vm_name,
                    kind="gce_vm",
                    adapter_family="gcp",
                    locator=f"http://{priv_ip}:8085",
                    labels=labels,
                    classification="other_read_only",
                    first_seen=now_iso,
                    last_seen=now_iso,
                )
            )
        return DiscoveryResult(services=services, evidence={"vms_count": len(services)})

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.SCOPED_OBSERVABILITY, SenseCategory.CHANGE_PLANE]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {}

    async def health(self) -> AdapterHealth:
        try:
            await self._fetch_instances()
            state = "available"
        except httpx.HTTPError:
            state = "unavailable"
        return AdapterHealth(
            adapter_name=self.name,
            family="gcp",
            state=state,  # type: ignore[arg-type]
            evidence={"api": "compute.googleapis.com/compute/v1", "zone": self.zone},
            checked_at=datetime.now(tz=UTC),
        )


# ------------------------------------------------------------------------------
# 5. Cloud SQL Admin Adapter
# ------------------------------------------------------------------------------


class CloudSqlAdminAdapter(_BaseGcpAdapter):
    name = "gcp-cloud-sql-admin"

    async def _fetch_sql_instances(self) -> list[dict[str, Any]]:
        url = f"https://sqladmin.googleapis.com/sql/v1beta4/projects/{self.project_id}/instances"
        async with self._http_client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return list(resp.json().get("items", []))

    async def discover(self) -> DiscoveryResult:
        items = await self._fetch_sql_instances()
        now_iso = datetime.now(tz=UTC).isoformat()
        services: list[DiscoveredServiceDoc] = []

        for sql_inst in items:
            sql_name = str(sql_inst.get("name", ""))
            settings = sql_inst.get("settings", {})
            labels = dict(settings.get("userLabels") or {})
            if is_resource_excluded(sql_name, labels):
                continue
            ips = sql_inst.get("ipAddresses") or [{}]
            priv_ip = str(ips[0].get("ipAddress", sql_name))
            services.append(
                DiscoveredServiceDoc(
                    doc_id=f"{self.env_id}__cloud_sql.{sql_name}",
                    env_id=self.env_id,
                    name=sql_name,
                    kind="cloud_sql",
                    adapter_family="gcp",
                    locator=f"postgres://{priv_ip}:5432",
                    labels=labels,
                    classification="other_read_only",
                    first_seen=now_iso,
                    last_seen=now_iso,
                )
            )
        return DiscoveryResult(services=services, evidence={"sql_instances": len(services)})

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.DATA, SenseCategory.CHANGE_PLANE]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {}

    async def health(self) -> AdapterHealth:
        try:
            await self._fetch_sql_instances()
            state = "available"
        except httpx.HTTPError:
            state = "unavailable"
        return AdapterHealth(
            adapter_name=self.name,
            family="gcp",
            state=state,  # type: ignore[arg-type]
            evidence={"api": "sqladmin.googleapis.com/sql/v1beta4"},
            checked_at=datetime.now(tz=UTC),
        )


# ------------------------------------------------------------------------------
# 6. Cloud Monitoring Adapter
# ------------------------------------------------------------------------------


class CloudMonitoringAdapter(_BaseGcpAdapter):
    name = "gcp-monitoring"

    def __init__(
        self,
        *,
        project_id: str | None = None,
        fixtures: dict[str, Any] | None = None,
        http_transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
        observability_mcp: ObservabilityMcpClient | None = None,
    ) -> None:
        super().__init__(
            project_id=project_id,
            fixtures=fixtures,
            http_transport=http_transport,
        )
        self._obs_mcp = observability_mcp

    async def _fetch_alert_policies(self) -> list[dict[str, Any]]:
        url = f"https://monitoring.googleapis.com/v3/projects/{self.project_id}/alertPolicies"
        async with self._http_client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return list(resp.json().get("alertPolicies", []))

    async def discover(self) -> DiscoveryResult:
        policies = await self._fetch_alert_policies()
        return DiscoveryResult(evidence={"alert_policies_count": len(policies)})

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.SCOPED_OBSERVABILITY]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {"obs.list_timeseries": get_capability("obs.list_timeseries")}

    async def health(self) -> AdapterHealth:
        try:
            await self._fetch_alert_policies()
            state = "available"
        except httpx.HTTPError:
            state = "unavailable"
        return AdapterHealth(
            adapter_name=self.name,
            family="gcp",
            state=state,  # type: ignore[arg-type]
            evidence={"api": "monitoring.googleapis.com/v3", "mcp": "observability-mcp"},
            checked_at=datetime.now(tz=UTC),
        )

    async def _execute_capability_impl(
        self,
        capability_id: str,
        args: dict[str, Any],
        trust_decision: TrustDecision | None = None,
    ) -> Any:
        if capability_id == "obs.list_timeseries":
            filter_str = str(args.get("filter", ""))
            if self._obs_mcp is not None:
                return await self._obs_mcp.list_timeseries(
                    filter_str=filter_str,
                    interval_seconds=int(args.get("interval_seconds", 300)),
                    project_id=self.project_id,
                )
            url = f"https://monitoring.googleapis.com/v3/projects/{self.project_id}/timeSeries"
            async with self._http_client() as client:
                resp = await client.get(url, params={"filter": filter_str})
                resp.raise_for_status()
                return resp.json()
        raise NotImplementedError(capability_id)


# ------------------------------------------------------------------------------
# 7. Cloud Trace Adapter
# ------------------------------------------------------------------------------


class CloudTraceAdapter(_BaseGcpAdapter):
    name = "gcp-trace"

    def __init__(
        self,
        *,
        project_id: str | None = None,
        fixtures: dict[str, Any] | None = None,
        http_transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
        observability_mcp: ObservabilityMcpClient | None = None,
    ) -> None:
        super().__init__(
            project_id=project_id,
            fixtures=fixtures,
            http_transport=http_transport,
        )
        self._obs_mcp = observability_mcp

    async def _fetch_traces(self) -> list[dict[str, Any]]:
        url = f"https://cloudtrace.googleapis.com/v1/projects/{self.project_id}/traces"
        async with self._http_client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return list(resp.json().get("traces", []))

    async def discover(self) -> DiscoveryResult:
        traces = await self._fetch_traces()
        now_iso = datetime.now(tz=UTC).isoformat()
        edges: list[DiscoveredEdgeDoc] = []

        for tr in traces:
            spans = tr.get("spans", [])
            span_service: dict[str, str] = {}
            for sp in spans:
                svc_name = sp.get("labels", {}).get("service.name")
                if svc_name and sp.get("spanId"):
                    span_service[str(sp["spanId"])] = str(svc_name)
            for sp in spans:
                parent_id = str(sp.get("parentSpanId", ""))
                child_svc = sp.get("labels", {}).get("service.name")
                parent_svc = span_service.get(parent_id)
                if parent_svc and child_svc and parent_svc != child_svc:
                    src_id = f"{self.env_id}__cloud_run.{parent_svc}"
                    dst_id = f"{self.env_id}__gke_workload.{child_svc}"
                    edges.append(
                        DiscoveredEdgeDoc(
                            doc_id=f"{src_id}__to__{dst_id}",
                            src=src_id,
                            dst=dst_id,
                            protocol="http",
                            evidence="trace",
                            call_rate_baseline=1.0,
                            first_seen=now_iso,
                            last_seen=now_iso,
                        )
                    )
        return DiscoveryResult(edges=edges, evidence={"traces_sampled": len(traces)})

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.SCOPED_OBSERVABILITY]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {
            "obs.list_traces": get_capability("obs.list_traces"),
            "obs.get_trace": get_capability("obs.get_trace"),
        }

    async def health(self) -> AdapterHealth:
        try:
            await self._fetch_traces()
            state = "available"
        except httpx.HTTPError:
            state = "unavailable"
        return AdapterHealth(
            adapter_name=self.name,
            family="gcp",
            state=state,  # type: ignore[arg-type]
            evidence={"api": "cloudtrace.googleapis.com/v1", "mcp": "observability-mcp"},
            checked_at=datetime.now(tz=UTC),
        )

    async def _execute_capability_impl(
        self,
        capability_id: str,
        args: dict[str, Any],
        trust_decision: TrustDecision | None = None,
    ) -> Any:
        if capability_id == "obs.list_traces":
            if self._obs_mcp is not None:
                return await self._obs_mcp.list_traces(
                    filter_str=str(args.get("filter", "")),
                    page_size=int(args.get("page_size", 10)),
                    project_id=self.project_id,
                )
            return {"traces": await self._fetch_traces()}

        if capability_id == "obs.get_trace":
            trace_id = str(args.get("trace_id", "")).strip()
            if not trace_id:
                raise ValueError("obs.get_trace requires a non-empty trace_id")
            if self._obs_mcp is not None:
                return await self._obs_mcp.get_trace(trace_id=trace_id, project_id=self.project_id)
            url = (
                f"https://cloudtrace.googleapis.com/v1/projects/{self.project_id}/traces/{trace_id}"
            )
            async with self._http_client() as client:
                resp = await client.get(url)
                if resp.status_code == 404:
                    raise LookupError(f"Trace '{trace_id}' not found")
                resp.raise_for_status()
                return resp.json()

        raise NotImplementedError(capability_id)


# ------------------------------------------------------------------------------
# 8. Cloud Logging & Audit Sink Adapter
# ------------------------------------------------------------------------------

_PAN_REGEX = re.compile(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b")
_AADHAAR_REGEX = re.compile(r"\b([2-9][0-9]{3}\s?[0-9]{4}\s?[0-9]{4})\b")


class CloudLoggingAuditAdapter(_BaseGcpAdapter):
    name = "gcp-logging-audit"

    def __init__(
        self,
        *,
        project_id: str | None = None,
        fixtures: dict[str, Any] | None = None,
        http_transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
        log_entries_fixture: list[dict[str, Any]] | None = None,
        observability_mcp: ObservabilityMcpClient | None = None,
    ) -> None:
        super().__init__(
            project_id=project_id,
            fixtures=fixtures,
            http_transport=http_transport,
        )
        self._log_entries_fixture = log_entries_fixture
        self._obs_mcp = observability_mcp

    async def _fetch_sinks(self) -> list[dict[str, Any]]:
        url = f"https://logging.googleapis.com/v2/projects/{self.project_id}/sinks"
        async with self._http_client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return list(resp.json().get("sinks", []))

    async def discover(self) -> DiscoveryResult:
        sinks = await self._fetch_sinks()
        audit_sinks = [
            s.get("name") for s in sinks if "cloudaudit.googleapis.com" in str(s.get("filter", ""))
        ]
        return DiscoveryResult(evidence={"sinks_count": len(sinks), "audit_sinks": audit_sinks})

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.SCOPED_OBSERVABILITY, SenseCategory.CHANGE_PLANE]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {"obs.query_logs_scoped": get_capability("obs.query_logs_scoped")}

    async def health(self) -> AdapterHealth:
        try:
            await self._fetch_sinks()
            state = "available"
        except httpx.HTTPError:
            state = "unavailable"
        return AdapterHealth(
            adapter_name=self.name,
            family="gcp",
            state=state,  # type: ignore[arg-type]
            evidence={"api": "logging.googleapis.com/v2", "max_page_size": 50},
            checked_at=datetime.now(tz=UTC),
        )

    async def _execute_capability_impl(
        self,
        capability_id: str,
        args: dict[str, Any],
        trust_decision: TrustDecision | None = None,
    ) -> Any:
        if capability_id == "obs.query_logs_scoped":
            filter_str = str(args.get("filter", "")).strip()
            page_size = int(args.get("page_size", args.get("pageSize", 50)))
            if not filter_str:
                raise ValueError(
                    "obs.query_logs_scoped requires a non-empty resource/severity filter (Agent Spec §5.1)"
                )
            if page_size < 1 or page_size > 50:
                raise ValueError(
                    f"obs.query_logs_scoped enforces pageSize <= 50 (got {page_size}) per TDD §3.7"
                )
            if self._obs_mcp is not None:
                mcp_res = await self._obs_mcp.list_log_entries(
                    filter_str=filter_str,
                    page_size=page_size,
                    project_id=self.project_id,
                )
                return {"filter": filter_str, "page_size": page_size, "entries": mcp_res}
            if self._log_entries_fixture is not None:
                return {
                    "filter": filter_str,
                    "page_size": page_size,
                    "entries": copy.deepcopy(self._log_entries_fixture[:page_size]),
                }
            async with self._http_client() as client:
                resp = await client.post(
                    "https://logging.googleapis.com/v2/entries:list",
                    json={
                        "resourceNames": [f"projects/{self.project_id}"],
                        "filter": filter_str,
                        "pageSize": page_size,
                    },
                )
                resp.raise_for_status()
                entries = resp.json().get("entries", [])
                return {"filter": filter_str, "page_size": page_size, "entries": entries}
        raise NotImplementedError(capability_id)

    def scan_entries_for_pii(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """UC-13 PII-in-logs detector: detects raw PAN / Aadhaar in log entries and returns masked samples."""
        findings: list[dict[str, Any]] = []
        for entry in entries:
            payload = str(entry.get("textPayload") or entry.get("jsonPayload") or "")
            has_pan = bool(_PAN_REGEX.search(payload))
            has_aadhaar = bool(_AADHAAR_REGEX.search(payload))
            if has_pan or has_aadhaar:
                masked = _PAN_REGEX.sub(lambda m: m.group(1)[:2] + "***" + m.group(1)[-1], payload)
                masked = _AADHAAR_REGEX.sub(lambda m: "***-***-" + m.group(1)[-4:], masked)
                svc = (
                    entry.get("resource", {})
                    .get("labels", {})
                    .get("service_name", "unknown-service")
                )
                findings.append(
                    {
                        "log_name": str(entry.get("logName", "")),
                        "service": str(svc),
                        "pii_types": [
                            t for t, flag in (("PAN", has_pan), ("AADHAAR", has_aadhaar)) if flag
                        ],
                        "masked_sample": masked,
                    }
                )
        return findings


# ------------------------------------------------------------------------------
# 9. Pub/Sub Nervous System Adapter
# ------------------------------------------------------------------------------


class PubSubAdapter(_BaseGcpAdapter):
    name = "gcp-pubsub"

    async def _fetch_topics(self) -> list[str]:
        url = f"https://pubsub.googleapis.com/v1/projects/{self.project_id}/topics"
        async with self._http_client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return [str(t.get("name", "")) for t in resp.json().get("topics", [])]

    async def discover(self) -> DiscoveryResult:
        topics = await self._fetch_topics()
        return DiscoveryResult(
            evidence={
                "topics": topics,
                "has_nervous_system": any(t.endswith("/godview-nervous-system") for t in topics),
                "has_dlq": any(t.endswith("/godview-nervous-system-dlq") for t in topics),
            }
        )

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.CHANGE_PLANE]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {}

    async def health(self) -> AdapterHealth:
        try:
            await self._fetch_topics()
            state = "available"
        except httpx.HTTPError:
            state = "unavailable"
        return AdapterHealth(
            adapter_name=self.name,
            family="gcp",
            state=state,  # type: ignore[arg-type]
            evidence={"api": "pubsub.googleapis.com/v1", "topic": "godview-nervous-system"},
            checked_at=datetime.now(tz=UTC),
        )


# ------------------------------------------------------------------------------
# 10. BigQuery Sense Adapter (Decision A13: sense/query adapter only)
# ------------------------------------------------------------------------------


class BigQueryAdapter(_BaseGcpAdapter):
    name = "gcp-bigquery"

    async def _fetch_datasets(self) -> list[dict[str, Any]]:
        url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{self.project_id}/datasets"
        async with self._http_client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return list(resp.json().get("datasets", []))

    async def discover(self) -> DiscoveryResult:
        datasets = await self._fetch_datasets()
        ds_ids = [d.get("datasetReference", {}).get("datasetId", "") for d in datasets]
        return DiscoveryResult(
            evidence={
                "datasets": ds_ids,
                "has_godview_ledger": "godview_ledger" in ds_ids,
            }
        )

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.DATA, SenseCategory.SCOPED_OBSERVABILITY]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {
            "ledger.query": get_capability("ledger.query"),
            "cost.query": get_capability("cost.query"),
        }

    async def health(self) -> AdapterHealth:
        try:
            await self._fetch_datasets()
            state = "available"
        except httpx.HTTPError:
            state = "unavailable"
        return AdapterHealth(
            adapter_name=self.name,
            family="gcp",
            state=state,  # type: ignore[arg-type]
            evidence={"api": "bigquery.googleapis.com/bigquery/v2", "dataset": "godview_ledger"},
            checked_at=datetime.now(tz=UTC),
        )

    async def _run_bigquery_select(self, statement: str) -> dict[str, Any]:
        validated_sql = _validate_single_select_sql(statement)
        url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{self.project_id}/queries"
        async with self._http_client() as client:
            resp = await client.post(
                url,
                json={"query": validated_sql, "useLegacySql": False},
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "statement": validated_sql,
                "job_complete": bool(data.get("jobComplete", True)),
                "rows": list(data.get("rows", [])),
            }

    async def _execute_capability_impl(
        self,
        capability_id: str,
        args: dict[str, Any],
        trust_decision: TrustDecision | None = None,
    ) -> Any:
        if capability_id == "ledger.query":
            statement = str(args.get("statement", ""))
            return await self._run_bigquery_select(statement)
        if capability_id == "cost.query":
            statement = str(
                args.get(
                    "statement",
                    "SELECT service, cost_usd FROM godview_ledger.billing_export",
                )
            )
            return await self._run_bigquery_select(statement)
        raise NotImplementedError(capability_id)


# ------------------------------------------------------------------------------
# 11. Datastream CDC Adapter (Phase 2 Stub reporting `unavailable`)
# ------------------------------------------------------------------------------


class DatastreamAdapter(_BaseGcpAdapter):
    name = "gcp-datastream"

    async def discover(self) -> DiscoveryResult:
        return DiscoveryResult(
            services=[],
            edges=[],
            entities=[],
            evidence={
                "phase": 2,
                "cdc_enabled": False,
                "enable_datastream": False,
                "reason": "Datastream CDC is Phase 2 (Decision A13); OTel span attributes feed godview_ledger in v1",
            },
        )

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.DATA, SenseCategory.CHANGE_PLANE]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {}

    async def health(self) -> AdapterHealth:
        return AdapterHealth(
            adapter_name=self.name,
            family="gcp",
            state="unavailable",
            evidence={
                "phase": 2,
                "enable_datastream": False,
                "cloudsql_logical_decoding": "on",
                "reason": "Phase 2 stub; Datastream API disabled in v1",
            },
            checked_at=datetime.now(tz=UTC),
        )


# ------------------------------------------------------------------------------
# 12. Firestore Native DB Adapter
# ------------------------------------------------------------------------------


class FirestoreAdapter(_BaseGcpAdapter):
    name = "gcp-firestore"

    async def _fetch_databases(self) -> list[dict[str, Any]]:
        url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases"
        async with self._http_client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return list(resp.json().get("databases", []))

    async def discover(self) -> DiscoveryResult:
        dbs = await self._fetch_databases()
        memory_db = next(
            (d for d in dbs if str(d.get("name", "")).endswith("/god-view-memory-db")),
            None,
        )
        return DiscoveryResult(
            evidence={
                "databases_count": len(dbs),
                "memory_db_found": memory_db is not None,
                "memory_db_type": memory_db.get("type") if memory_db else None,
            }
        )

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.DATA]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {}

    async def health(self) -> AdapterHealth:
        try:
            dbs = await self._fetch_databases()
            has_mem_db = any(str(d.get("name", "")).endswith("/god-view-memory-db") for d in dbs)
            state = "available" if has_mem_db else ("partial" if dbs else "unavailable")
        except httpx.HTTPError:
            state = "unavailable"
        return AdapterHealth(
            adapter_name=self.name,
            family="gcp",
            state=state,  # type: ignore[arg-type]
            evidence={"database": "god-view-memory-db", "vector_dimension": 768},
            checked_at=datetime.now(tz=UTC),
        )


# ------------------------------------------------------------------------------
# 13. Vertex AI Agent Engine Adapter (ReasoningEngine, Warmth PATCH, UC-23 Idle Cost)
# ------------------------------------------------------------------------------

ALLOWED_REASONING_ENGINE_CPUS: frozenset[str] = frozenset({"1", "2", "4", "6", "8"})


class AgentEngineAdapter(_BaseGcpAdapter):
    name = "gcp-agent-engine"

    async def _fetch_engines(self) -> list[dict[str, Any]]:
        url = (
            f"https://{self.region}-aiplatform.googleapis.com/v1beta1/"
            f"projects/{self.project_id}/locations/{self.region}/reasoningEngines"
        )
        async with self._http_client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return list(resp.json().get("reasoningEngines", []))

    def _fetch_engines_sync(self) -> list[dict[str, Any]]:
        url = (
            f"https://{self.region}-aiplatform.googleapis.com/v1beta1/"
            f"projects/{self.project_id}/locations/{self.region}/reasoningEngines"
        )
        with self._sync_http_client() as client:
            resp = client.get(url)
            resp.raise_for_status()
            return list(resp.json().get("reasoningEngines", []))

    async def discover(self) -> DiscoveryResult:
        engines = await self._fetch_engines()
        now_iso = datetime.now(tz=UTC).isoformat()
        services: list[DiscoveredServiceDoc] = []

        for eng in engines:
            full_name = str(eng.get("name", ""))
            display_name = str(eng.get("displayName") or full_name.rsplit("/", 1)[-1])
            dep_spec = eng.get("spec", {}).get("deploymentSpec", {})
            env_list = dep_spec.get("env", [])
            is_godview_agent = any(e.get("name") == "GODVIEW_AGENT_ID" for e in env_list)
            labels = {"godview_managed": "true"} if is_godview_agent else {}

            services.append(
                DiscoveredServiceDoc(
                    doc_id=f"{self.env_id}__reasoning_engine.{display_name}",
                    env_id=self.env_id,
                    name=display_name,
                    kind="reasoning_engine",
                    adapter_family="gcp",
                    locator=full_name,
                    labels=labels,
                    classification="other_read_only",
                    first_seen=now_iso,
                    last_seen=now_iso,
                )
            )
        return DiscoveryResult(
            services=services,
            evidence={"reasoning_engines_count": len(engines)},
        )

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.SCOPED_OBSERVABILITY, SenseCategory.CHANGE_PLANE]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {"agent.set_min_instances": get_capability("agent.set_min_instances")}

    async def health(self) -> AdapterHealth:
        try:
            await self._fetch_engines()
            state = "available"
        except httpx.HTTPError:
            state = "unavailable"
        return AdapterHealth(
            adapter_name=self.name,
            family="gcp",
            state=state,  # type: ignore[arg-type]
            evidence={
                "api": f"{self.region}-aiplatform.googleapis.com/v1beta1",
                "warmth_min_instances_allowed": [0, 1],
                "platform_min_instances_range": [0, 75],
                "max_instances_range": [1, 1000],
                "allowed_cpus": sorted(ALLOWED_REASONING_ENGINE_CPUS),
            },
            checked_at=datetime.now(tz=UTC),
        )

    def detect_idle_reasoning_engines(self) -> list[CostFindingDoc]:
        """UC-23: Identify Reasoning Engines idling with `minInstances >= 1` (read-only cost finding)."""
        engines = self._fetch_engines_sync()
        now = datetime.now(tz=UTC)
        date_str = now.strftime("%Y%m%d")
        findings: list[CostFindingDoc] = []

        for idx, eng in enumerate(engines):
            dep_spec = eng.get("spec", {}).get("deploymentSpec", {})
            min_inst = int(dep_spec.get("minInstances", 0))
            limits = dep_spec.get("resourceLimits", {})
            cpu_str = str(limits.get("cpu", "4"))
            env_list = dep_spec.get("env", [])
            is_godview_court = any(e.get("name") == "GODVIEW_AGENT_ID" for e in env_list)

            if min_inst >= 1 and not is_godview_court:
                cpu_num = int(cpu_str) if cpu_str.isdigit() else 4
                monthly_usd = Decimal(str(round(cpu_num * 34.50 * min_inst, 2)))
                res_name = str(eng.get("name", f"reasoningEngine-{idx}"))
                slug = res_name.rsplit("/", 1)[-1]
                findings.append(
                    CostFindingDoc(
                        doc_id=f"cost-{date_str}-idle-re-{slug}",
                        resource=res_name,
                        finding_type="idle_reasoning_engine",
                        monthly_usd_estimate=monthly_usd,
                        recommendation=(
                            f"Report to project owner to scale down or delete idle non-God-View ReasoningEngine "
                            f"(currently minInstances={min_inst} at {cpu_str} vCPU / {limits.get('memory', '4Gi')})"
                        ),
                        status="open",
                        found_by="platform__agent",
                        found_at=now.isoformat(),
                    )
                )
        return findings

    async def _execute_capability_impl(
        self,
        capability_id: str,
        args: dict[str, Any],
        trust_decision: TrustDecision | None = None,
    ) -> Any:
        if capability_id == "agent.set_min_instances":
            assert_valid_mutating_trust("agent.set_min_instances", trust_decision)
            re_name = str(args.get("reasoning_engine", "")).strip()
            if not re_name:
                raise ValueError("agent.set_min_instances requires a non-empty 'reasoning_engine'")
            min_instances = int(args.get("min_instances", 0))
            max_instances = int(args.get("max_instances", 1))
            cpu = str(args.get("cpu", "1"))
            memory = str(args.get("memory", "2Gi"))
            concurrency = int(args.get("container_concurrency", 4))

            if min_instances not in (0, 1):
                raise ValueError(
                    f"Sovereign warmth controller only permits deploymentSpec.minInstances in {{0, 1}} "
                    f"(0 <-> 1 per TDD §3.4 & UC-21; got minInstances={min_instances})"
                )
            if max_instances not in range(1, 1001):
                raise ValueError(
                    f"ReasoningEngine deploymentSpec.maxInstances must be in [1, 1000] (got {max_instances})"
                )
            if cpu not in ALLOWED_REASONING_ENGINE_CPUS:
                raise ValueError(
                    f"ReasoningEngine resourceLimits.cpu must be in {sorted(ALLOWED_REASONING_ENGINE_CPUS)} (got '{cpu}')"
                )

            # Verify target engine is a God-View court agent when present in live inventory (TDD I-07)
            engines = await self._fetch_engines()
            target_id = re_name.rsplit("/", 1)[-1]
            for eng in engines:
                eng_name = str(eng.get("name", ""))
                if eng_name == re_name or eng_name.rsplit("/", 1)[-1] == target_id:
                    env_list = eng.get("spec", {}).get("deploymentSpec", {}).get("env", [])
                    is_godview_court = any(e.get("name") == "GODVIEW_AGENT_ID" for e in env_list)
                    if not is_godview_court:
                        raise UnmanagedResourceError(
                            f"Refusing to PATCH non-God-View ReasoningEngine '{re_name}': engine lacks GODVIEW_AGENT_ID (TDD I-07)"
                        )

            deployment_spec = {
                "minInstances": min_instances,
                "maxInstances": max_instances,
                "resourceLimits": {"cpu": cpu, "memory": memory},
                "containerConcurrency": concurrency,
            }
            endpoint = f"https://{self.region}-aiplatform.googleapis.com/v1beta1/{re_name}"
            update_mask = "spec.deploymentSpec.minInstances"
            async with self._http_client() as client:
                resp = await client.patch(
                    endpoint,
                    params={"updateMask": update_mask},
                    json={"spec": {"deploymentSpec": deployment_spec}},
                )
                resp.raise_for_status()
                api_response = resp.json()

            return {
                "reasoning_engine": re_name,
                "method": "PATCH",
                "endpoint": endpoint,
                "update_mask": update_mask,
                "deployment_spec": deployment_spec,
                "api_response": api_response,
            }
        raise NotImplementedError(capability_id)


# ------------------------------------------------------------------------------
# 14. Agent Registry Adapter (Optional: discovered, never assumed)
# ------------------------------------------------------------------------------


class AgentRegistryAdapter(_BaseGcpAdapter):
    """Discovers `agentregistry.googleapis.com` v1 when available; falls back to Firestore `court_agents`."""

    name = "gcp-agent-registry"

    async def _probe_registry(self) -> tuple[bool, dict[str, Any]]:
        url = (
            f"https://agentregistry.googleapis.com/v1/projects/{self.project_id}/"
            f"locations/{self.region}/agents"
        )
        try:
            async with self._http_client() as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return False, {}
                data = resp.json()
                if "error" in data:
                    return False, {}
                return True, data
        except httpx.HTTPError:
            return False, {}

    async def discover(self) -> DiscoveryResult:
        avail, reg = await self._probe_registry()
        if not avail:
            return DiscoveryResult(
                services=[],
                evidence={
                    "available": False,
                    "fallback_registry": "firestore:court_agents",
                },
            )
        agents = reg.get("agents", [])
        services = reg.get("services", [])
        return DiscoveryResult(
            services=[],
            evidence={
                "available": True,
                "discovered_agents_count": len(agents),
                "discovered_services_count": len(services),
                "agents": copy.deepcopy(agents),
            },
        )

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.CONTRACTS, SenseCategory.SCOPED_OBSERVABILITY]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {}

    async def health(self) -> AdapterHealth:
        avail, _ = await self._probe_registry()
        return AdapterHealth(
            adapter_name=self.name,
            family="gcp",
            state="available" if avail else "unavailable",
            evidence={
                "api": "agentregistry.googleapis.com/v1",
                "discovered": avail,
                "fallback_registry": "firestore:court_agents",
            },
            checked_at=datetime.now(tz=UTC),
        )

    async def catalogue_agent(
        self,
        *,
        agent_id: str,
        display_name: str,
        a2a_card_url: str,
    ) -> dict[str, Any]:
        """Catalogue a court agent in Agent Registry only if one was discovered (Agent Spec §1 & §2.3)."""
        avail, _ = await self._probe_registry()
        if not avail:
            return {
                "catalogued": False,
                "agent_id": agent_id,
                "registry_of_record": "firestore:court_agents",
                "reason": "agent_registry_unavailable",
            }
        endpoint = (
            f"https://agentregistry.googleapis.com/v1/projects/{self.project_id}/"
            f"locations/{self.region}/agents"
        )
        async with self._http_client() as client:
            resp = await client.post(
                endpoint,
                json={
                    "agentId": agent_id,
                    "displayName": display_name,
                    "a2aCardUrl": a2a_card_url,
                },
            )
            resp.raise_for_status()
        return {
            "catalogued": True,
            "agent_id": agent_id,
            "display_name": display_name,
            "a2a_card_url": a2a_card_url,
            "registry_endpoint": endpoint,
        }


# ------------------------------------------------------------------------------
# 15. Model Armor Adapter (Text-only `sanitizeUserPrompt`, SDD §8.1)
# ------------------------------------------------------------------------------


class ModelArmorAdapter(_BaseGcpAdapter):
    name = "gcp-model-armor"

    async def _fetch_templates(self) -> list[dict[str, Any]]:
        url = (
            f"https://modelarmor.{self.region}.rep.googleapis.com/v1/"
            f"projects/{self.project_id}/locations/{self.region}/templates"
        )
        async with self._http_client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return list(resp.json().get("templates", []))

    async def discover(self) -> DiscoveryResult:
        templates = await self._fetch_templates()
        return DiscoveryResult(
            evidence={
                "templates_count": len(templates),
                "templates": [t.get("name", "") for t in templates],
            }
        )

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.CONTRACTS, SenseCategory.SCOPED_OBSERVABILITY]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {}

    async def health(self) -> AdapterHealth:
        try:
            templates = await self._fetch_templates()
            state = "available" if templates is not None else "partial"
        except httpx.HTTPError:
            state = "partial"
        return AdapterHealth(
            adapter_name=self.name,
            family="gcp",
            state=state,  # type: ignore[arg-type]
            evidence={
                "api": f"modelarmor.{self.region}.rep.googleapis.com/v1",
                "mode": "text_only_image_bypass",
            },
            checked_at=datetime.now(tz=UTC),
        )

    async def sanitize_user_prompt(
        self,
        *,
        template_id: str,
        text_fields: dict[str, str | None],
        image_bytes: bytes | None = None,
    ) -> dict[str, Any]:
        """Scan free-text prompt fields via Model Armor `sanitizeUserPrompt` (rejecting binary/image inputs per SDD §8.1)."""
        if image_bytes is not None:
            raise ValueError(
                "Model Armor policy is text-only: binary or document image bytes must never be sent to Model Armor (SDD §8.1)"
            )
        combined = " ".join(v for v in text_fields.values() if v)
        endpoint = (
            f"https://modelarmor.{self.region}.rep.googleapis.com/v1/projects/{self.project_id}/"
            f"locations/{self.region}/templates/{template_id}:sanitizeUserPrompt"
        )
        async with self._http_client() as client:
            resp = await client.post(
                endpoint,
                json={"userPromptData": {"text": combined}},
            )
            resp.raise_for_status()
            payload = resp.json()

        san_result = payload.get("sanitizationResult", {})
        match_state = str(san_result.get("filterMatchState", "NO_MATCH_FOUND"))
        if match_state == "MATCH_FOUND":
            return {
                "status": "MODEL_ARMOR_BLOCKED",
                "endpoint": endpoint,
                "findings": san_result.get(
                    "filterResults", {"piAndJailbreakFilterResult": "MATCH_FOUND"}
                ),
            }
        return {
            "status": "CLEAN",
            "endpoint": endpoint,
            "findings": san_result.get(
                "filterResults", {"piAndJailbreakFilterResult": "NO_MATCH_FOUND"}
            ),
        }


# ------------------------------------------------------------------------------
# 16. Cloud IAM Adapter (`testIamPermissions` & `godview-act-sa` impersonation)
# ------------------------------------------------------------------------------


class CloudIamAdapter(_BaseGcpAdapter):
    name = "gcp-iam"

    async def _fetch_iam_state(self) -> tuple[list[str], list[str]]:
        sa_url = f"https://iam.googleapis.com/v1/projects/{self.project_id}/serviceAccounts"
        perm_url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{self.project_id}:testIamPermissions"
        async with self._http_client() as client:
            sa_resp = await client.get(sa_url)
            sa_resp.raise_for_status()
            accounts = [str(a.get("email", "")) for a in sa_resp.json().get("accounts", [])]

            perm_resp = await client.post(
                perm_url,
                json={
                    "permissions": [
                        "resourcemanager.projects.get",
                        "run.services.list",
                        "cloudasset.assets.listResource",
                        "monitoring.timeSeries.list",
                        "cloudtrace.traces.get",
                        "logging.logEntries.list",
                        "iam.serviceAccounts.getAccessToken",
                    ]
                },
            )
            perm_resp.raise_for_status()
            permissions = [str(p) for p in perm_resp.json().get("permissions", [])]
        return accounts, permissions

    async def discover(self) -> DiscoveryResult:
        accounts, permissions = await self._fetch_iam_state()
        return DiscoveryResult(
            evidence={
                "service_accounts": accounts,
                "permissions": permissions,
                "has_sense_sa": any(a.startswith("godview-sense-sa@") for a in accounts),
                "has_act_sa": any(a.startswith("godview-act-sa@") for a in accounts),
            }
        )

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.CHANGE_PLANE, SenseCategory.SCOPED_OBSERVABILITY]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {}

    async def health(self) -> AdapterHealth:
        try:
            await self._fetch_iam_state()
            state = "available"
        except httpx.HTTPError:
            state = "unavailable"
        return AdapterHealth(
            adapter_name=self.name,
            family="gcp",
            state=state,  # type: ignore[arg-type]
            evidence={"api": "iam.googleapis.com/v1"},
            checked_at=datetime.now(tz=UTC),
        )

    def impersonate_act_sa(self, trust_decision: TrustDecision | None) -> dict[str, str]:
        """Mint short-lived credentials for `godview-act-sa` only inside a validated mutating TrustDecision (TDD I-06)."""
        if trust_decision is None:
            raise TrustViolation(
                "Cannot impersonate godview-act-sa without an authorized TrustDecision (TDD I-06)"
            )
        td = assert_valid_mutating_trust(trust_decision.capability_id, trust_decision)
        if td.sa_to_impersonate != "godview-act-sa":
            raise TrustViolation(
                f"Expected sa_to_impersonate='godview-act-sa', got '{td.sa_to_impersonate}'"
            )
        target_sa = f"godview-act-sa@{self.project_id}.iam.gserviceaccount.com"
        endpoint = (
            f"https://iamcredentials.googleapis.com/v1/projects/-/"
            f"serviceAccounts/{target_sa}:generateAccessToken"
        )
        with self._sync_http_client() as client:
            resp = client.post(
                endpoint,
                json={
                    "scope": ["https://www.googleapis.com/auth/cloud-platform"],
                    "lifetime": "900s",
                },
            )
            resp.raise_for_status()
            token_data = resp.json()

        access_token = str(token_data.get("accessToken", "")).strip()
        if not access_token:
            raise RuntimeError("IAM Credentials generateAccessToken returned empty accessToken")
        return {
            "service_account": target_sa,
            "access_token": access_token,
            "expire_time": str(token_data.get("expireTime", "")),
            "endpoint": endpoint,
        }


def build_all_gcp_adapters(
    *,
    project_id: str | None = None,
    region: str = "us-central1",
    zone: str = "us-central1-a",
    fixtures: dict[str, Any] | None = None,
) -> list[SenseAdapter]:
    """Instantiate the full suite of 16 GCP sense adapters + the Terraform drift adapter."""
    common = {"project_id": project_id, "fixtures": fixtures}
    return [
        CloudAssetInventoryAdapter(**common),
        CloudRunV2Adapter(project_id=project_id, region=region, fixtures=fixtures),
        GkeAdapter(project_id=project_id, region=region, fixtures=fixtures),
        ComputeEngineAdapter(project_id=project_id, region=region, zone=zone, fixtures=fixtures),
        CloudSqlAdminAdapter(**common),
        CloudMonitoringAdapter(**common),
        CloudTraceAdapter(**common),
        CloudLoggingAuditAdapter(**common),
        PubSubAdapter(**common),
        BigQueryAdapter(**common),
        DatastreamAdapter(**common),
        FirestoreAdapter(**common),
        AgentEngineAdapter(project_id=project_id, region=region, fixtures=fixtures),
        AgentRegistryAdapter(project_id=project_id, region=region, fixtures=fixtures),
        ModelArmorAdapter(project_id=project_id, region=region, fixtures=fixtures),
        CloudIamAdapter(**common),
        TerraformDriftAdapter(project_id=project_id, region=region, zone=zone, fixtures=fixtures),
    ]
