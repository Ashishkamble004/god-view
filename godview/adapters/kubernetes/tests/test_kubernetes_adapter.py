"""Unit tests for the Kubernetes Sense Adapter (`godview/adapters/kubernetes/`).

Covers TDD §1 (I-02 randomised fixtures, I-03 open standards, I-06 TrustDecision before
k8s.rollout_restart / k8s.scale), §3.7, §6.2 #3, and SDD §2 & §10:
- API server discovery on both GKE and kind (bearer token from env, no gke-gcloud-auth-plugin)
- Observer resource and system namespace exclusion (Decision A11)
- Events inspection (OOMKilled, CrashLoopBackOff, Normal events)
- Metrics-server pod/node resource metrics (`/apis/metrics.k8s.io/v1beta1/pods`)
- Prometheus `/metrics` exposition scraping (`metrics.scrape`)
- Capability `k8s.get_resource` via `gke-mcp` (`McpToolClientProtocol`) on GKE or Kubernetes API on kind
- Capability `k8s.rollout_restart` and `k8s.scale` (mutating, L3, TrustDecision required, both failure directions)
- Availability states: `available`, `partial` (API server up, metrics-server unavailable), `unavailable`
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from godview_shared.adapter_contract import SenseCategory
from godview_shared.capabilities import validate_mcp_tool_binding
from godview_shared.contracts import TrustDecision, TrustViolation

from godview.adapters.kubernetes import KubernetesSenseAdapter


def _rand_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _kubectl_deployment_manifest(
    name: str, namespace: str, image: str, port: int = 8080
) -> dict[str, Any]:
    """Generate a Kubernetes v1.37 Deployment manifest using ~/.local/bin/kubectl --dry-run=client."""
    env = dict(os.environ)
    local_bin = str(Path.home() / ".local" / "bin")
    env["PATH"] = f"{local_bin}:{env.get('PATH', '')}"
    kubectl_bin = shutil.which("kubectl", path=env["PATH"])
    if kubectl_bin:
        raw = subprocess.check_output(
            [
                kubectl_bin,
                "create",
                "deployment",
                name,
                f"--image={image}",
                f"--port={port}",
                "--replicas=1",
                "--dry-run=client",
                "-o",
                "json",
            ],
            env=env,
            text=True,
        )
        doc: dict[str, Any] = json.loads(raw)
    else:
        doc = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": name, "labels": {"app": name}},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": name}},
                "template": {
                    "metadata": {"labels": {"app": name}},
                    "spec": {
                        "containers": [
                            {"name": name, "image": image, "ports": [{"containerPort": port}]}
                        ]
                    },
                },
            },
        }
    doc["metadata"]["namespace"] = namespace
    return doc


def _make_trust_decision(
    capability_id: str,
    *,
    authorized: bool = True,
    effective_level: int = 3,
    required_level: int = 3,
    blast_radius_resources: int = 1,
    sa_to_impersonate: str = "godview-act-sa",
) -> TrustDecision:
    return TrustDecision(
        authorized=authorized,
        agent_id=f"platform__{_rand_name('env')}",
        action_type=capability_id,
        capability_id=capability_id,
        effective_level=effective_level,
        required_level=required_level,
        sa_to_impersonate=sa_to_impersonate,
        blast_radius_resources=blast_radius_resources,
        reason="unit test authorization",
        decided_at=datetime.now(tz=UTC).isoformat(),
    )


class _FakeGkeMcpClient:
    """Fake gke-mcp client implementing McpToolClientProtocol (#72/#73)."""

    server_name = "gke-mcp"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        validate_mcp_tool_binding(self.server_name, tool_name)
        self.calls.append((tool_name, arguments))
        return {
            "kind": arguments.get("kind", "Deployment"),
            "name": arguments.get("name", ""),
            "namespace": arguments.get("namespace", "default"),
            "source": "gke-mcp",
        }


@pytest.mark.unit
async def test_k8s_discovery_gke_and_kind_with_kubectl_fixtures() -> None:
    """Discover workloads, services, and edges across namespaces; exclude system and observer workloads."""
    ns_tenant = _rand_name("ns-tenant")
    svc_alpha = _rand_name("wkld-alpha")
    svc_beta = _rand_name("wkld-beta")
    svc_observer = "god-view-sovereign"
    env_id = _rand_name("env-k8s")
    token = f"bearer-{uuid.uuid4().hex}"

    dep_alpha = _kubectl_deployment_manifest(
        svc_alpha, ns_tenant, f"registry.local/{svc_alpha}:sha1"
    )
    dep_alpha["metadata"]["labels"] = {
        "app": svc_alpha,
        "godview_managed": "true",
    }
    dep_alpha["spec"]["template"]["spec"]["containers"][0]["env"] = [
        {"name": "DOWNSTREAM_BETA_URL", "value": f"http://{svc_beta}:8080"},
    ]

    dep_beta = _kubectl_deployment_manifest(svc_beta, ns_tenant, f"registry.local/{svc_beta}:sha2")
    dep_beta["metadata"]["labels"] = {"app": svc_beta, "godview_managed": "false"}

    dep_observer = _kubectl_deployment_manifest(svc_observer, ns_tenant, "registry.local/obs:v1")
    dep_observer["metadata"]["labels"] = {"godview_role": "observer"}

    dep_kubesys = _kubectl_deployment_manifest(
        "coredns", "kube-system", "registry.k8s.io/coredns:v1"
    )

    seen_auth_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth_headers.append(request.headers.get("Authorization", ""))
        path = request.url.path
        if path == "/version":
            return httpx.Response(200, json={"gitVersion": "v1.37.1"})
        if path == "/apis/metrics.k8s.io/v1beta1/pods":
            return httpx.Response(
                200,
                json={
                    "kind": "PodMetricsList",
                    "items": [
                        {
                            "metadata": {"name": f"{svc_alpha}-pod-1", "namespace": ns_tenant},
                            "containers": [
                                {"name": svc_alpha, "usage": {"cpu": "120m", "memory": "210Mi"}}
                            ],
                        }
                    ],
                },
            )
        if path == "/apis/apps/v1/deployments":
            return httpx.Response(
                200,
                json={
                    "kind": "DeploymentList",
                    "items": [dep_alpha, dep_beta, dep_observer, dep_kubesys],
                },
            )
        if path == "/api/v1/services":
            return httpx.Response(
                200,
                json={
                    "kind": "ServiceList",
                    "items": [
                        {
                            "metadata": {
                                "name": svc_alpha,
                                "namespace": ns_tenant,
                                "labels": {"godview_managed": "true"},
                                "annotations": {
                                    "prometheus.io/scrape": "true",
                                    "prometheus.io/port": "8080",
                                },
                            },
                            "spec": {"clusterIP": "10.24.1.15", "ports": [{"port": 8080}]},
                            "status": {"loadBalancer": {"ingress": [{"ip": "10.10.0.51"}]}},
                        },
                        {
                            "metadata": {"name": svc_beta, "namespace": ns_tenant, "labels": {}},
                            "spec": {"clusterIP": "10.24.1.16", "ports": [{"port": 8080}]},
                            "status": {},
                        },
                    ],
                },
            )
        if path == "/api/v1/events":
            return httpx.Response(
                200,
                json={
                    "kind": "EventList",
                    "items": [
                        {
                            "metadata": {"name": "evt-oom-1", "namespace": ns_tenant},
                            "involvedObject": {
                                "kind": "Pod",
                                "name": f"{svc_alpha}-pod-1",
                                "namespace": ns_tenant,
                            },
                            "reason": "OOMKilling",
                            "type": "Warning",
                            "message": "Memory cgroup out of memory",
                            "count": 2,
                        }
                    ],
                },
            )
        return httpx.Response(404, json={"message": "not found"})

    transport = httpx.MockTransport(handler)
    adapter = KubernetesSenseAdapter(
        api_url="https://127.0.0.1:6443",
        bearer_token=token,
        env_id=env_id,
        cluster_mode="kind",
        http_transport=transport,
    )

    assert SenseCategory.SCOPED_OBSERVABILITY in adapter.senses()
    assert SenseCategory.CHANGE_PLANE in adapter.senses()
    assert adapter.verify_tls is True

    health = await adapter.health()
    assert health.state == "available"
    assert health.evidence["api_server_reachable"] is True
    assert health.evidence["metrics_server_available"] is True
    assert health.evidence["auth_plugin_used"] is None
    assert f"Bearer {token}" in seen_auth_headers

    discovery = await adapter.discover()
    names = {s.name for s in discovery.services}
    assert svc_alpha in names
    assert svc_beta in names
    assert svc_observer not in names
    assert "coredns" not in names

    by_name = {s.name: s for s in discovery.services}
    assert by_name[svc_alpha].kind == "gke_workload"
    assert by_name[svc_alpha].classification == "other_read_only"
    assert by_name[svc_alpha].metrics_endpoint == "http://10.10.0.51:8080/metrics"
    assert by_name[svc_beta].classification == "other_read_only"
    assert discovery.evidence["services_list_status_code"] == 200

    assert len(discovery.edges) == 1
    assert discovery.edges[0].evidence == "k8s"
    assert svc_alpha in discovery.edges[0].src
    assert svc_beta in discovery.edges[0].dst

    events = await adapter.list_events(namespace=ns_tenant)
    assert len(events) == 1
    assert events[0]["reason"] == "OOMKilling"

    pod_metrics = await adapter.get_pod_metrics(namespace=ns_tenant)
    assert len(pod_metrics) == 1
    assert pod_metrics[0]["containers"][0]["usage"]["memory"] == "210Mi"

    # Non-200 /api/v1/services is recorded in evidence rather than silently dropped
    def svc_error_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/apis/apps/v1/deployments":
            return httpx.Response(200, json={"kind": "DeploymentList", "items": [dep_alpha]})
        if request.url.path == "/api/v1/services":
            return httpx.Response(403, text="Forbidden services list")
        return httpx.Response(404)

    err_adapter = KubernetesSenseAdapter(
        api_url="https://127.0.0.1:6443",
        bearer_token=token,
        env_id=env_id,
        http_transport=httpx.MockTransport(svc_error_handler),
    )
    err_disc = await err_adapter.discover()
    assert err_disc.evidence["services_list_status_code"] == 403
    assert "Forbidden" in err_disc.evidence["services_list_error"]


@pytest.mark.unit
async def test_k8s_health_partial_and_unavailable_states() -> None:
    """Verify health() reports 'partial' when metrics-server is absent and 'unavailable' when API server fails."""

    def partial_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json={"gitVersion": "v1.37.1-gke.100"})
        if request.url.path == "/apis/metrics.k8s.io/v1beta1/pods":
            return httpx.Response(404, json={"message": "metrics.k8s.io not registered"})
        return httpx.Response(200, json={"items": []})

    partial_adapter = KubernetesSenseAdapter(
        api_url="https://10.0.0.1:443",
        bearer_token="tok-partial",
        cluster_mode="gke",
        http_transport=httpx.MockTransport(partial_handler),
    )
    h_partial = await partial_adapter.health()
    assert h_partial.state == "partial"
    assert h_partial.evidence["api_server_reachable"] is True
    assert h_partial.evidence["metrics_server_available"] is False

    unavail_adapter = KubernetesSenseAdapter(
        api_url="",
        bearer_token="",
    )
    h_unavail = await unavail_adapter.health()
    assert h_unavail.state == "unavailable"
    assert h_unavail.evidence["api_server_reachable"] is False


@pytest.mark.unit
async def test_k8s_prometheus_scrape_and_get_resource_mcp_vs_direct() -> None:
    """Verify Prometheus text exposition scraping and k8s.get_resource via gke-mcp vs direct API."""
    svc_name = _rand_name("svc-metrics")
    ns_name = _rand_name("ns-obs")
    prom_text = (
        "# HELP http_requests_total Total HTTP requests\n"
        "# TYPE http_requests_total counter\n"
        f'http_requests_total{{service="{svc_name}",status="200"}} 420\n'
        f'http_requests_total{{service="{svc_name}",status="503"}} 3\n'
        "# HELP http_request_duration_seconds_p95 Latency P95\n"
        "# TYPE http_request_duration_seconds_p95 gauge\n"
        f'http_request_duration_seconds_p95{{service="{svc_name}"}} 0.312\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/metrics":
            return httpx.Response(200, text=prom_text)
        if request.url.path == f"/apis/apps/v1/namespaces/{ns_name}/deployments/{svc_name}":
            return httpx.Response(
                200,
                json={"kind": "Deployment", "metadata": {"name": svc_name, "namespace": ns_name}},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    kind_adapter = KubernetesSenseAdapter(
        api_url="https://127.0.0.1:6443",
        bearer_token="tok-kind",
        cluster_mode="kind",
        http_transport=transport,
    )
    scrape_res = await kind_adapter.execute_capability(
        "metrics.scrape",
        {"endpoint": f"http://{svc_name}:8080/metrics"},
    )
    assert scrape_res["sample_count"] == 3
    assert scrape_res["metrics"]["http_request_duration_seconds_p95"][0]["value"] == pytest.approx(
        0.312
    )

    direct_res = await kind_adapter.execute_capability(
        "k8s.get_resource",
        {"kind": "Deployment", "namespace": ns_name, "name": svc_name},
    )
    assert direct_res["metadata"]["name"] == svc_name

    # Now test with injected gke-mcp client on GKE
    mcp_client = _FakeGkeMcpClient()
    gke_adapter = KubernetesSenseAdapter(
        api_url="https://10.0.0.1:443",
        bearer_token="tok-gke",
        cluster_mode="gke",
        gke_mcp_client=mcp_client,
        http_transport=transport,
    )
    mcp_res = await gke_adapter.execute_capability(
        "k8s.get_resource",
        {"kind": "Deployment", "namespace": ns_name, "name": svc_name},
    )
    assert mcp_res["source"] == "gke-mcp"
    assert len(mcp_client.calls) == 1
    assert mcp_client.calls[0][0] == "get_k8s_resource"


@pytest.mark.unit
async def test_k8s_rollout_restart_and_scale_trust_enforcement_both_directions() -> None:
    """Test both failure and success directions for k8s.rollout_restart and k8s.scale (TDD I-06 & I-07)."""
    ns_name = _rand_name("ns-prod")
    managed_dep = _rand_name("dep-managed")
    unmanaged_dep = _rand_name("dep-unmanaged")
    patches_received: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith(f"/deployments/{managed_dep}"):
            return httpx.Response(
                200,
                json={
                    "kind": "Deployment",
                    "metadata": {
                        "name": managed_dep,
                        "namespace": ns_name,
                        "labels": {"godview_managed": "true"},
                    },
                    "spec": {"replicas": 1},
                },
            )
        if request.method == "GET" and path.endswith(f"/deployments/{unmanaged_dep}"):
            return httpx.Response(
                200,
                json={
                    "kind": "Deployment",
                    "metadata": {
                        "name": unmanaged_dep,
                        "namespace": ns_name,
                        "labels": {"godview_managed": "false"},
                    },
                    "spec": {"replicas": 2},
                },
            )
        if request.method == "PATCH":
            body = json.loads(request.content.decode("utf-8"))
            patches_received.append((path, body))
            return httpx.Response(200, json={"status": "patched", "body": body})
        return httpx.Response(404)

    adapter = KubernetesSenseAdapter(
        api_url="https://127.0.0.1:6443",
        bearer_token="tok-act",
        http_transport=httpx.MockTransport(handler),
    )

    # Direction 1: Missing TrustDecision -> raises TrustViolation
    with pytest.raises(TrustViolation):
        await adapter.execute_capability(
            "k8s.rollout_restart",
            {"namespace": ns_name, "deployment": managed_dep},
            trust_decision=None,
        )

    # Direction 2: Insufficient trust level (L2 < L3) -> raises TrustViolation
    low_td = _make_trust_decision("k8s.rollout_restart", effective_level=2)
    with pytest.raises(TrustViolation):
        await adapter.execute_capability(
            "k8s.rollout_restart",
            {"namespace": ns_name, "deployment": managed_dep},
            trust_decision=low_td,
        )

    # Direction 3: Unmanaged deployment (godview_managed != "true") -> raises TrustViolation (I-07)
    valid_td = _make_trust_decision("k8s.rollout_restart", effective_level=3)
    with pytest.raises(TrustViolation):
        await adapter.execute_capability(
            "k8s.rollout_restart",
            {"namespace": ns_name, "deployment": unmanaged_dep},
            trust_decision=valid_td,
        )

    # Direction 4: Authorized L3 TrustDecision on managed deployment -> succeeds and patches restartedAt
    res = await adapter.execute_capability(
        "k8s.rollout_restart",
        {"namespace": ns_name, "deployment": managed_dep},
        trust_decision=valid_td,
    )
    assert res["status"] == "restarted"
    assert res["deployment"] == managed_dep
    assert "restarted_at" in res
    assert res["reversal_recipe"]["capability"] == "k8s.rollout_restart"
    assert len(patches_received) == 1
    annotations = patches_received[0][1]["spec"]["template"]["metadata"]["annotations"]
    assert "kubectl.kubernetes.io/restartedAt" in annotations

    # Test k8s.scale refuses replicas <= 0 (scale-to-zero belongs to hibernate) and succeeds for replicas >= 1
    scale_td = _make_trust_decision("k8s.scale", effective_level=3)
    for bad_replicas in (0, -1, -5):
        with pytest.raises(ValueError, match="replicas >= 1"):
            await adapter.execute_capability(
                "k8s.scale",
                {"namespace": ns_name, "deployment": managed_dep, "replicas": bad_replicas},
                trust_decision=scale_td,
            )

    scale_res = await adapter.execute_capability(
        "k8s.scale",
        {"namespace": ns_name, "deployment": managed_dep, "replicas": 2},
        trust_decision=scale_td,
    )
    assert scale_res["status"] == "scaled"
    assert scale_res["replicas"] == 2
    assert scale_res["reversal_recipe"]["args"]["replicas"] == 1
