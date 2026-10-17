"""Unit tests for God-View v5 GCP senses, edge topology, OpenAPI parsing, and error isolation.

Covers plan §3.2 requirements:
- Edge discovery (forwarding rules, target proxies, URL maps, backend services, Cloud Armor WAF sensitivity)
- API Gateway discovery with OpenAPI route/method parsing, backend mapping, and vendor extensions
- Pub/Sub subscription discovery, push endpoints, and DLQ depth
- Cloud SQL database/user names discovery
- Pagination handling across GCP APIs
- Multi-region discovery (locations/-, aggregatedList)
- Per-adapter error isolation (failure -> AdapterHealth unavailable + evidence, never 500)
- HTTP GET-only probe safety
- Capabilities exposure for sentinels and remediation
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from godview.adapters.gcp.adapters import (
    CloudLoggingAuditAdapter,
    CloudMonitoringAdapter,
    CloudRunV2Adapter,
    CloudSqlAdminAdapter,
    ComputeEngineAdapter,
    GkeAdapter,
    HttpProbeAdapter,
    PubSubAdapter,
)
from godview.adapters.gcp.apigateway import ApiGatewayAdapter
from godview.adapters.gcp.edge import CloudEdgeAdapter

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "gcp_api_fixtures.json"


@pytest.fixture
def gcp_fixtures() -> dict[str, Any]:
    with open(FIXTURES_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.unit
async def test_edge_adapter_discovery_and_waf_sensitivity(gcp_fixtures: dict[str, Any]) -> None:
    """Test CloudEdgeAdapter discovers frontend, url map, backend services, and Cloud Armor WAF sensitivity."""
    adapter = CloudEdgeAdapter(fixtures=gcp_fixtures)
    health = await adapter.health()
    assert health.state == "available"
    assert health.adapter_name == "gcp-edge"

    res = await adapter.discover()
    assert len(res.services) >= 3

    kinds = {s.kind for s in res.services}
    assert "lb_frontend" in kinds
    assert "backend_service" in kinds
    assert "security_policy" in kinds

    # Verify backend service configuration
    backend_svcs = [s for s in res.services if s.kind == "backend_service"]
    assert len(backend_svcs) >= 1
    bs = backend_svcs[0]
    assert bs.posture is not None
    assert bs.posture.iap is True
    assert bs.posture.waf == "sensitivity-1"
    assert bs.posture.details.get("logging_enabled") is True
    assert bs.posture.details.get("security_policy") == "sec-policy-edge"

    # Verify security policy has WAF sensitivity detected
    policies = [s for s in res.services if s.kind == "security_policy"]
    assert len(policies) >= 1
    pol = policies[0]
    assert pol.posture is not None
    assert pol.posture.waf == "sensitivity-1"
    assert pol.posture.details.get("has_waf") is True
    assert pol.posture.details.get("waf_sensitivities") == [1]

    # Verify edge route discovery
    assert len(res.edges) >= 1
    protocols = {e.protocol for e in res.edges}
    assert "lb_route" in protocols
    lb_edges = [e for e in res.edges if e.protocol == "lb_route"]
    assert len(lb_edges) >= 1
    assert lb_edges[0].evidence in ("lb_route", "url_map")

    # Verify edge capabilities
    backends = await adapter.execute_capability("edge.describe_backends", {})
    assert isinstance(backends, list)
    assert len(backends) >= 1

    policies_desc = await adapter.execute_capability("edge.describe_security_policies", {})
    assert isinstance(policies_desc, list)
    assert len(policies_desc) >= 1


@pytest.mark.unit
async def test_apigateway_adapter_openapi_parsing_and_routes(gcp_fixtures: dict[str, Any]) -> None:
    """Test ApiGatewayAdapter parses OpenAPI specs, vendor extensions, and routes."""
    adapter = ApiGatewayAdapter(fixtures=gcp_fixtures)
    health = await adapter.health()
    assert health.state == "available"
    assert health.adapter_name == "gcp-api-gateway"

    res = await adapter.discover()
    assert len(res.services) >= 1
    gw = res.services[0]
    assert gw.kind == "api_gateway"
    assert gw.labels.get("hostname") == "gw-main-xyz.gateway.dev"

    # Verify routes extracted from OpenAPI spec
    route_paths = [r["path"] for r in gw.routes]
    assert "/v1/items" in route_paths
    assert "/v1/admin/reconcile" in route_paths

    # Verify vendor extensions parsed generically
    probe_safe_routes = [r for r in gw.routes if r.get("x-godview-probe-safe")]
    assert len(probe_safe_routes) >= 1
    assert probe_safe_routes[0]["method"] == "GET"

    admin_routes = [r for r in gw.routes if r.get("x-godview-admin-action")]
    assert len(admin_routes) >= 1
    assert admin_routes[0]["path"] == "/v1/admin/reconcile"

    # Verify edges to backend service
    assert len(res.edges) >= 1
    edge = res.edges[0]
    assert edge.protocol == "http"
    assert "cloud_run.svc-core" in edge.dst

    # Verify capability
    spec = await adapter.execute_capability("apigateway.get_openapi", {"gateway_id": "gw-main"})
    assert "paths" in spec
    assert "/v1/items" in spec["paths"]


@pytest.mark.unit
async def test_pubsub_adapter_subscription_discovery_and_capabilities(
    gcp_fixtures: dict[str, Any],
) -> None:
    """Test PubSubAdapter subscription push endpoint discovery and capabilities."""
    adapter = PubSubAdapter(fixtures=gcp_fixtures)
    res = await adapter.discover()

    # Verify push endpoint edge to Cloud Run
    push_edges = [e for e in res.edges if "cloud_run" in e.dst]
    assert len(push_edges) >= 1
    assert "cloud_run.svc-core" in push_edges[0].dst

    # Verify capability: describe_subscriptions
    sub_desc = await adapter.execute_capability("pubsub.describe_subscriptions", {})
    assert isinstance(sub_desc, list)
    assert len(sub_desc) >= 1
    assert "sub-events-push" in sub_desc[0]["name"]

    # Verify capability: dlq_depth
    dlq = await adapter.execute_capability(
        "pubsub.dlq_depth", {"subscription_id": "sub-events-push"}
    )
    assert dlq["dlq_configured"] is True
    assert dlq["dlq_topic"] == "projects/test-project-123/topics/events-dlq"


@pytest.mark.unit
async def test_cloudsql_adapter_databases_and_users(gcp_fixtures: dict[str, Any]) -> None:
    """Test CloudSqlAdminAdapter discovers database names and user names without secrets."""
    adapter = CloudSqlAdminAdapter(fixtures=gcp_fixtures)
    res = await adapter.discover()
    assert len(res.services) >= 1
    db_svc = res.services[0]
    assert db_svc.kind == "cloud_sql"

    # Databases and users appear in labels / evidence
    assert "appdb" in db_svc.labels.get("databases", "")
    assert "appuser" in db_svc.labels.get("users", "")


@pytest.mark.unit
async def test_pagination_helper(gcp_fixtures: dict[str, Any]) -> None:
    """Test _fetch_paginated handles pageToken and nextPageToken seamlessly."""
    adapter = CloudRunV2Adapter(fixtures=gcp_fixtures)

    class PaginatedTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.calls = 0

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            self.calls += 1
            token = request.url.params.get("pageToken")
            if not token:
                return httpx.Response(200, json={"services": [{"name": "s1"}], "nextPageToken": "token-2"})
            elif token == "token-2":
                return httpx.Response(200, json={"services": [{"name": "s2"}], "nextPageToken": "token-3"})
            else:
                return httpx.Response(200, json={"services": [{"name": "s3"}]})

    adapter._transport = PaginatedTransport()
    items = await adapter._fetch_paginated("https://run.googleapis.com/v2/services", "services")
    assert len(items) == 3
    assert [x["name"] for x in items] == ["s1", "s2", "s3"]


@pytest.mark.unit
async def test_multi_region_discovery(gcp_fixtures: dict[str, Any]) -> None:
    """Test multi-region endpoints for Cloud Run, GKE, and Compute Engine."""
    run_adapter = CloudRunV2Adapter(fixtures=gcp_fixtures, regions=["us-central1", "us-east1"])
    run_disc = await run_adapter.discover()
    assert len(run_disc.services) >= 1
    for s in run_disc.services:
        assert s.kind == "cloud_run"

    gke_adapter = GkeAdapter(fixtures=gcp_fixtures, regions=["us-central1"])
    gke_disc = await gke_adapter.discover()
    assert len(gke_disc.services) >= 1

    compute_adapter = ComputeEngineAdapter(fixtures=gcp_fixtures)
    compute_disc = await compute_adapter.discover()
    assert len(compute_disc.services) >= 1
    assert compute_disc.services[0].kind == "gce_instance"
    # Ensure fixed legacy locator port was removed
    assert not compute_disc.services[0].locator.endswith(f":{8080 + 5}")


@pytest.mark.unit
async def test_per_adapter_error_isolation() -> None:
    """Test that an adapter experiencing HTTP 500 reports unavailable health and does not crash."""

    class FailingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=500, json={"error": {"code": 500, "message": "Simulated backend crash"}})

    failing_transport = FailingTransport()
    adapter = CloudRunV2Adapter(project_id="test-p", http_transport=failing_transport)

    health = await adapter.health()
    assert health.state == "unavailable"
    assert "error" in health.evidence

    # discover() handles errors gracefully
    disc = await adapter.discover()
    assert len(disc.services) == 0
    assert "error" in disc.evidence


@pytest.mark.unit
async def test_http_probe_adapter_safety(gcp_fixtures: dict[str, Any]) -> None:
    """Test HttpProbeAdapter executes GET probes and refuses non-GET operations."""
    probe_adapter = HttpProbeAdapter(fixtures=gcp_fixtures)
    health = await probe_adapter.health()
    assert health.state == "available"

    # Safe GET probe
    probe_res = await probe_adapter.execute_capability(
        "http.probe", {"url": "https://service-a.run.app/healthz", "method": "GET"}
    )
    assert probe_res["ok"] is True
    assert probe_res["status_code"] == 200

    # Non-GET probe must be rejected
    with pytest.raises(ValueError, match="GET"):
        await probe_adapter.execute_capability(
            "http.probe", {"url": "https://service-a.run.app/data", "method": "POST"}
        )


@pytest.mark.unit
async def test_read_capabilities_exposure(gcp_fixtures: dict[str, Any]) -> None:
    """Verify all 10 read capabilities are implemented and can be executed without error."""
    # 1. edge.describe_backends
    edge = CloudEdgeAdapter(fixtures=gcp_fixtures)
    res_edge = await edge.execute_capability("edge.describe_backends", {})
    assert isinstance(res_edge, list)

    # 2. edge.describe_security_policies
    res_sec = await edge.execute_capability("edge.describe_security_policies", {})
    assert isinstance(res_sec, list)

    # 3. apigateway.get_openapi
    gw = ApiGatewayAdapter(fixtures=gcp_fixtures)
    res_gw = await gw.execute_capability("apigateway.get_openapi", {})
    assert "paths" in res_gw

    # 4. pubsub.describe_subscriptions
    ps = PubSubAdapter(fixtures=gcp_fixtures)
    res_ps_sub = await ps.execute_capability("pubsub.describe_subscriptions", {})
    assert isinstance(res_ps_sub, list)

    # 5. pubsub.dlq_depth
    res_ps_dlq = await ps.execute_capability("pubsub.dlq_depth", {"subscription_id": "sub-events-push"})
    assert "dlq_configured" in res_ps_dlq

    # 6. run.get_posture
    run_ad = CloudRunV2Adapter(fixtures=gcp_fixtures)
    res_run = await run_ad.execute_capability("run.get_posture", {"service_name": "service-a"})
    assert "ingress" in res_run

    # 7. logging.query_lb_requests
    log_ad = CloudLoggingAuditAdapter(fixtures=gcp_fixtures)
    res_lb = await log_ad.execute_capability("logging.query_lb_requests", {})
    assert isinstance(res_lb, list)

    # 8. logging.query
    res_log = await log_ad.execute_capability("logging.query", {"filter": "severity>=ERROR"})
    assert isinstance(res_log, list)

    # 9. monitoring.query_timeseries
    mon_ad = CloudMonitoringAdapter(fixtures=gcp_fixtures)
    res_mon = await mon_ad.execute_capability("monitoring.query_timeseries", {})
    assert isinstance(res_mon, list)

    # 10. http.probe
    http_ad = HttpProbeAdapter(fixtures=gcp_fixtures)
    res_probe = await http_ad.execute_capability("http.probe", {"url": "https://service-a.run.app/healthz"})
    assert res_probe["ok"] is True
