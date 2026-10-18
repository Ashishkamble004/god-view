"""Google Cloud API Gateway Sense Adapter.

Per plan §1.4, §1.5, §3.2:
Discovers API Gateways, API Configs, and downloads/parses OpenAPI documents.
Parses paths, HTTP methods, operation IDs, summaries, x-google-backend target addresses,
and vendor extensions (x-godview-probe-safe, x-godview-admin-action, x-godview-reversal).
Creates:
- `api_gateway` service nodes with discovered routes
- `openapi` edges connecting API Gateway to backend services (e.g. Cloud Run)
Implements capability:
- `apigateway.get_openapi`
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml
from godview_shared.adapter_contract import (
    AdapterHealth,
    DiscoveryResult,
    SenseAdapter,
    SenseCategory,
)
from godview_shared.capabilities import CapabilitySpec, get_capability
from godview_shared.contracts import DiscoveredEdgeDoc, DiscoveredServiceDoc, TrustDecision

from godview.adapters.gcp.drift import (
    build_fixture_http_transport,
    is_resource_excluded,
    resolve_gcp_bearer_token,
    resolve_gcp_project_id,
)

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "options", "head"})


class ApiGatewayAdapter(SenseAdapter):
    """Discovers API Gateways, parses OpenAPI contracts, and extracts backend routing."""

    family = "gcp"
    name = "gcp-api-gateway"

    def __init__(
        self,
        *,
        project_id: str | None = None,
        region: str = "us-central1",
        zone: str = "us-central1-a",
        auth: Any | None = None,
        bearer_token: str | None = None,
        fixtures: dict[str, Any] | None = None,
        http_transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._fixtures = fixtures
        self.project_id = resolve_gcp_project_id(project_id, fixtures)
        self.region = region
        self.zone = zone
        self.auth = auth
        self.bearer_token = resolve_gcp_bearer_token(bearer_token)
        self._transport = http_transport or (
            build_fixture_http_transport(fixtures) if fixtures is not None else None
        )
        self.env_id = f"gcp-{self.project_id}"
        self._cached_openapi_by_gateway: dict[str, dict[str, Any]] = {}

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "x-goog-user-project": self.project_id,
        }
        token: str | None = None
        if self.auth is not None and hasattr(self.auth, "access_token"):
            try:
                token = self.auth.access_token()
            except Exception:
                token = None
        if not token:
            token = self.bearer_token or resolve_gcp_bearer_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _http_client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"headers": self._headers(), "timeout": 15.0}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def _fetch_paginated(self, url: str, items_key: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        page_token: str | None = None
        async with self._http_client() as client:
            while True:
                params: dict[str, Any] = {}
                if page_token:
                    params["pageToken"] = page_token
                resp = await client.get(url, params=params)
                if resp.status_code == 404:
                    return results
                resp.raise_for_status()
                data = resp.json()
                results.extend(data.get(items_key, []))
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
        return results

    async def _fetch_gateways(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        seen_names: set[str] = set()

        # 1. Try wild-card location '-'
        try:
            url = f"https://apigateway.googleapis.com/v1/projects/{self.project_id}/locations/-/gateways"
            for gw in await self._fetch_paginated(url, "gateways"):
                name = gw.get("name")
                if name and name not in seen_names:
                    seen_names.add(name)
                    results.append(gw)
        except Exception:
            pass

        if results:
            return results

        # 2. Query configured region and standard GCP locations (API Gateway requires explicit location if '-' not supported)
        locs = [self.region, "us-central1", "us-east1", "us-west1", "europe-west1", "asia-east1", "global"]
        for loc in locs:
            if not loc:
                continue
            try:
                url = f"https://apigateway.googleapis.com/v1/projects/{self.project_id}/locations/{loc}/gateways"
                for gw in await self._fetch_paginated(url, "gateways"):
                    name = gw.get("name")
                    if name and name not in seen_names:
                        seen_names.add(name)
                        results.append(gw)
            except Exception:
                pass

        return results

    async def _fetch_api_config(self, api_config_name: str) -> dict[str, Any]:
        """Fetch api config with view=FULL to retrieve OpenAPI spec documents."""
        url = f"https://apigateway.googleapis.com/v1/{api_config_name}"
        async with self._http_client() as client:
            resp = await client.get(url, params={"view": "FULL"})
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            return resp.json()

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.CONTRACTS, SenseCategory.CHANGE_PLANE]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {"apigateway.get_openapi": get_capability("apigateway.get_openapi")}

    async def health(self) -> AdapterHealth:
        try:
            gateways = await self._fetch_gateways()
            state = "available"
            count = len(gateways)
            error = None
        except Exception as exc:
            state = "unavailable"
            count = 0
            error = str(exc)
        evidence = {
            "api": "apigateway.googleapis.com/v1",
            "gateways_count": count,
        }
        if error:
            evidence["error"] = error
        return AdapterHealth(
            adapter_name=self.name,
            family="gcp",
            state=state,  # type: ignore[arg-type]
            evidence=evidence,
            checked_at=datetime.now(tz=UTC),
        )

    def _parse_openapi_doc(self, raw_content: str | bytes) -> dict[str, Any]:
        if isinstance(raw_content, bytes):
            raw_content = raw_content.decode("utf-8", errors="replace")
        try:
            parsed = yaml.safe_load(raw_content)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return {}

    async def discover(self) -> DiscoveryResult:
        try:
            gateways = await self._fetch_gateways()
        except Exception as exc:
            return DiscoveryResult(
                services=[],
                edges=[],
                entities=[],
                evidence={"error": str(exc), "state": "unavailable"},
            )

        now_iso = datetime.now(tz=UTC).isoformat()
        services: list[DiscoveredServiceDoc] = []
        edges: list[DiscoveredEdgeDoc] = []
        config_errors: dict[str, str] = {}

        for gw in gateways:
            gw_full_name = str(gw.get("name", ""))
            gw_short_name = gw_full_name.rsplit("/", 1)[-1]
            labels = dict(gw.get("labels") or {})
            is_observer = (
                labels.get("godview_role") == "observer"
                or is_resource_excluded(gw_short_name, labels)
            )
            if is_observer:
                labels["godview_role"] = "observer"

            default_hostname = str(gw.get("defaultHostname", ""))
            if default_hostname:
                labels["hostname"] = default_hostname
            api_config_ref = str(gw.get("apiConfig", ""))
            doc_id = f"{self.env_id}__api_gateway.{gw_short_name}"

            routes: list[dict[str, Any]] = []
            backend_targets: set[str] = set()

            # Attempt to fetch API Config
            if api_config_ref:
                try:
                    cfg_data = await self._fetch_api_config(api_config_ref)
                    openapi_docs = cfg_data.get("openapiDocuments", [])
                    for doc_entry in openapi_docs:
                        doc_file = doc_entry.get("document", {})
                        contents_b64 = doc_file.get("contents", "")
                        raw_spec = ""
                        if contents_b64:
                            try:
                                raw_spec = base64.b64decode(contents_b64).decode("utf-8")
                            except Exception:
                                raw_spec = contents_b64
                        elif "contents_raw" in doc_file:
                            raw_spec = doc_file["contents_raw"]

                        if raw_spec:
                            spec_dict = self._parse_openapi_doc(raw_spec)
                            if spec_dict:
                                self._cached_openapi_by_gateway[gw_short_name] = spec_dict
                                paths = spec_dict.get("paths", {})
                                root_backend = spec_dict.get("x-google-backend", {})
                                for path_str, path_item in paths.items():
                                    if not isinstance(path_item, dict):
                                        continue
                                    # Path-level or operation-level backend (inherits root)
                                    path_backend = path_item.get("x-google-backend", root_backend)
                                    for method, op in path_item.items():
                                        method_lower = method.lower()
                                        if method_lower not in _HTTP_METHODS or not isinstance(op, dict):
                                            continue
                                        op_backend = op.get("x-google-backend", path_backend)
                                        backend_address = ""
                                        if isinstance(op_backend, dict):
                                            backend_address = str(op_backend.get("address", ""))

                                        route_entry = {
                                            "path": path_str,
                                            "method": method_lower.upper(),
                                            "operation_id": op.get("operationId"),
                                            "summary": op.get("summary", ""),
                                            "description": op.get("description", ""),
                                            "x-godview-probe-safe": bool(
                                                op.get("x-godview-probe-safe", False)
                                            ),
                                            "x-godview-admin-action": bool(
                                                op.get("x-godview-admin-action", False)
                                            ),
                                            "x-godview-reversal": bool(
                                                op.get("x-godview-reversal", False)
                                            ),
                                            "backend_address": backend_address,
                                        }
                                        routes.append(route_entry)
                                        if backend_address:
                                            backend_targets.add(backend_address)
                except Exception as c_exc:
                    config_errors[gw_short_name] = str(c_exc)

            services.append(
                DiscoveredServiceDoc(
                    doc_id=doc_id,
                    env_id=self.env_id,
                    name=gw_short_name,
                    kind="api_gateway",
                    adapter_family="gcp",
                    locator=f"https://{default_hostname}" if default_hostname else gw_short_name,
                    labels=labels,
                    routes=routes,
                    classification="journey_linked" if routes else "other_read_only",
                    first_seen=now_iso,
                    last_seen=now_iso,
                    status="active",
                )
            )

            # Build edges to backend services
            for addr in backend_targets:
                parsed = urlparse(addr)
                hostname = parsed.hostname or addr
                if ".run.app" in hostname:
                    base_host = hostname.split(".run.app")[0]
                    parts = base_host.split("-")
                    if len(parts) >= 3 and len(parts[-2]) >= 8:
                        target_service_name = "-".join(parts[:-2])
                    else:
                        target_service_name = base_host
                    dst_id = f"{self.env_id}__cloud_run.{target_service_name}"
                else:
                    target_service_name = hostname.split(":")[0]
                    dst_id = f"{self.env_id}__service.{target_service_name}"

                edges.append(
                    DiscoveredEdgeDoc(
                        doc_id=f"{doc_id}__to__{dst_id}",
                        src=doc_id,
                        dst=dst_id,
                        protocol="http",
                        evidence="openapi",
                        evidence_kind="openapi",
                        confidence=1.0,
                        first_seen=now_iso,
                        last_seen=now_iso,
                    )
                )

        evidence: dict[str, Any] = {
            "gateways_count": len(services),
            "total_routes_discovered": sum(len(s.routes) for s in services),
        }
        if config_errors:
            evidence["config_errors"] = config_errors

        return DiscoveryResult(
            services=services,
            edges=edges,
            entities=[],
            evidence=evidence,
        )

    async def _execute_capability_impl(
        self,
        capability_id: str,
        args: dict[str, Any],
        trust_decision: TrustDecision | None = None,
    ) -> Any:
        if capability_id == "apigateway.get_openapi":
            gw_name = args.get("gateway")
            if not self._cached_openapi_by_gateway:
                await self.discover()
            if gw_name:
                short_gw = str(gw_name).rsplit("/", 1)[-1]
                return (
                    self._cached_openapi_by_gateway.get(gw_name)
                    or self._cached_openapi_by_gateway.get(short_gw)
                    or {}
                )
            if len(self._cached_openapi_by_gateway) == 1:
                return next(iter(self._cached_openapi_by_gateway.values()))
            if len(self._cached_openapi_by_gateway) > 1:
                return dict(self._cached_openapi_by_gateway)
            return {}
        raise NotImplementedError(capability_id)
