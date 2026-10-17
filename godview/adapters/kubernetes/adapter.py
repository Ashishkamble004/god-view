"""Kubernetes Sense Adapter (`godview/adapters/kubernetes/adapter.py`).

Implements SDD §2.2, §2.3, §10 and TDD §1 (I-02, I-03, I-05, I-06, I-07), §3.7:
- Works identically against GKE Autopilot and local `kind` API servers using a bearer token
  resolved from the environment or service account mount (never `gke-gcloud-auth-plugin`).
- Discovers Deployments, Services, and workload edges while excluding system namespaces
  and observer resources (`is_excluded_resource`).
- Reads Kubernetes Events (`/api/v1/events` or namespace-scoped) and `metrics-server`
  pod/node usage (`/apis/metrics.k8s.io/v1beta1/pods`).
- Scrapes Prometheus exposition `/metrics` endpoints (`metrics.scrape`).
- Implements `k8s.get_resource` (via `gke-mcp` client when provided on GKE, else Kubernetes API)
  and mutating L3 capabilities `k8s.rollout_restart` and `k8s.scale` gated by `TrustDecision`.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable
from urllib.parse import urlparse

import httpx
from godview_shared.adapter_contract import (
    AdapterHealth,
    DiscoveryResult,
    SenseAdapter,
    SenseCategory,
)
from godview_shared.capabilities import (
    CapabilitySpec,
    get_capability,
    validate_mcp_tool_binding,
)
from godview_shared.config import (
    MANAGED_LABEL_KEY,
    MANAGED_LABEL_VALUE,
    is_excluded_resource,
)
from godview_shared.contracts import (
    DiscoveredEdgeDoc,
    DiscoveredServiceDoc,
    TrustDecision,
    TrustViolation,
)

SYSTEM_NAMESPACES: frozenset[str] = frozenset(
    {
        "kube-system",
        "kube-public",
        "kube-node-lease",
        "local-path-storage",
        "gke-managed-system",
        "gmp-system",
        "gmp-public",
        "istio-system",
    }
)

_PROM_SAMPLE_RE = re.compile(
    r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|NaN|[+-]Inf)$"
)
_PROM_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\"|[^"])*)"')


@runtime_checkable
class McpToolClientProtocol(Protocol):
    """Protocol agreed with @adapters-gcp (#72/#73) for official MCP server clients."""

    server_name: str

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any: ...


def parse_prometheus_text(raw_text: str) -> dict[str, Any]:
    """Parse Prometheus text exposition format into metric families and sample counts."""
    metrics: dict[str, list[dict[str, Any]]] = {}
    types: dict[str, str] = {}
    sample_count = 0

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# TYPE "):
            parts = line.split()
            if len(parts) >= 4:
                types[parts[2]] = parts[3]
            continue
        if line.startswith("#"):
            continue
        match = _PROM_SAMPLE_RE.match(line)
        if not match:
            continue
        metric_name, labels_blob, value_str = match.groups()
        labels: dict[str, str] = {}
        if labels_blob:
            for l_match in _PROM_LABEL_RE.finditer(labels_blob):
                labels[l_match.group(1)] = l_match.group(2).replace('\\"', '"')
        try:
            value = float(value_str)
        except ValueError:
            continue
        metrics.setdefault(metric_name, []).append(
            {
                "labels": labels,
                "value": value,
                "type": types.get(metric_name, "untyped"),
            }
        )
        sample_count += 1

    return {
        "metrics": metrics,
        "types": types,
        "sample_count": sample_count,
    }


IN_CLUSTER_CA_CERT_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")


def _resolve_bearer_token(explicit_token: str | None) -> str:
    """Resolve Kubernetes API bearer token from explicit arg, env, or in-cluster SA secret."""
    if explicit_token is not None:
        return explicit_token.strip()
    for env_var in (
        "GODVIEW_K8S_BEARER_TOKEN",
        "KUBERNETES_BEARER_TOKEN",
        "GOOGLE_OAUTH_ACCESS_TOKEN",
        "K8S_BEARER_TOKEN",
    ):
        val = os.environ.get(env_var, "").strip()
        if val:
            return val
    sa_token_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
    if sa_token_path.is_file():
        try:
            return sa_token_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return ""


def _resolve_tls_verify(
    verify_tls: bool | str,
    ca_cert_path: Path = IN_CLUSTER_CA_CERT_PATH,
) -> bool | str:
    """Resolve TLS verification setting, preferring explicit CA path or in-cluster SA CA."""
    if verify_tls is False:
        return False
    if isinstance(verify_tls, str) and verify_tls.strip():
        return verify_tls.strip()
    env_ca = os.environ.get("GODVIEW_K8S_CA_CERT", "").strip()
    if env_ca and Path(env_ca).is_file():
        return env_ca
    if ca_cert_path.is_file():
        return str(ca_cert_path)
    return True


def _resolve_api_url(explicit_url: str | None) -> str:
    """Resolve Kubernetes API server URL from explicit arg or standard environment variables."""
    if explicit_url is not None:
        return explicit_url.strip().rstrip("/")
    env_url = os.environ.get("KUBERNETES_API_URL", "").strip()
    if env_url:
        return env_url.rstrip("/")
    host = os.environ.get("KUBERNETES_SERVICE_HOST", "").strip()
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443").strip()
    if host:
        return f"https://{host}:{port}"
    return ""


def _validate_mutating_trust(
    capability_id: str,
    spec: CapabilitySpec,
    trust_decision: TrustDecision | None,
) -> TrustDecision:
    """Strict TrustGate validation for mutating Kubernetes capabilities (TDD I-06)."""
    if trust_decision is None or not trust_decision.authorized:
        raise TrustViolation(
            f"Mutating capability '{capability_id}' requires an authorized TrustDecision"
        )
    if trust_decision.capability_id != capability_id or trust_decision.action_type != capability_id:
        raise TrustViolation(f"TrustDecision capability/action_type mismatch for '{capability_id}'")
    if trust_decision.required_level != spec.min_level:
        raise TrustViolation(
            f"TrustDecision required_level ({trust_decision.required_level}) must match spec.min_level ({spec.min_level})"
        )
    if trust_decision.blast_radius_resources != 1:
        raise TrustViolation(
            f"Blast radius must be 1 resource (got {trust_decision.blast_radius_resources})"
        )
    if trust_decision.sa_to_impersonate != "godview-act-sa":
        raise TrustViolation(
            f"Mutating capability '{capability_id}' must impersonate 'godview-act-sa'"
        )
    if trust_decision.effective_level < 2:
        raise TrustViolation(
            f"Mutating capability '{capability_id}' requires effective_level >= 2 "
            f"(got {trust_decision.effective_level})"
        )
    if trust_decision.effective_level < spec.min_level and not trust_decision.approval_id:
        raise TrustViolation(
            f"Capability '{capability_id}' at effective_level={trust_decision.effective_level} "
            f"(< min_level={spec.min_level}) requires an approval_id"
        )
    return trust_decision


class KubernetesSenseAdapter(SenseAdapter):
    """Kubernetes sense and capability adapter supporting both GKE and kind."""

    family: Literal["gcp", "kubernetes", "sql", "git", "channels", "local"] = "kubernetes"
    name: str = "kubernetes-api-adapter"

    def __init__(
        self,
        *,
        api_url: str | None = None,
        bearer_token: str | None = None,
        env_id: str = "local-compose",
        cluster_mode: Literal["gke", "kind", "auto"] = "auto",
        gke_mcp_client: McpToolClientProtocol | None = None,
        verify_tls: bool | str = True,
        ca_cert_path: Path = IN_CLUSTER_CA_CERT_PATH,
        timeout_s: float = 10.0,
        http_transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_url = _resolve_api_url(api_url)
        self.bearer_token = _resolve_bearer_token(bearer_token)
        self.env_id = env_id
        self.cluster_mode = cluster_mode
        self.gke_mcp_client = gke_mcp_client
        self.verify_tls: bool | str = _resolve_tls_verify(verify_tls, ca_cert_path=ca_cert_path)
        self.timeout_s = timeout_s
        self._transport = http_transport

    def _headers(self, token_override: str | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        token = token_override if token_override is not None else self.bearer_token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {
            "base_url": self.api_url or "https://127.0.0.1:6443",
            "headers": self._headers(),
            "timeout": self.timeout_s,
            "verify": self.verify_tls,
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    def senses(self) -> list[SenseCategory]:
        return [
            SenseCategory.SCOPED_OBSERVABILITY,
            SenseCategory.CHANGE_PLANE,
            SenseCategory.CONTRACTS,
        ]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {
            "k8s.get_resource": get_capability("k8s.get_resource"),
            "k8s.rollout_restart": get_capability("k8s.rollout_restart"),
            "k8s.scale": get_capability("k8s.scale"),
            "metrics.scrape": get_capability("metrics.scrape"),
        }

    async def health(self) -> AdapterHealth:
        now = datetime.now(tz=UTC)
        if not self.api_url:
            return AdapterHealth(
                adapter_name=self.name,
                family=self.family,
                state="unavailable",
                evidence={
                    "api_server_reachable": False,
                    "metrics_server_available": False,
                    "reason": "No Kubernetes API server URL configured or discovered in environment",
                    "auth_plugin_used": None,
                },
                checked_at=now,
            )

        api_ok = False
        metrics_ok = False
        git_version = ""
        error_detail: str | None = None

        try:
            async with self._client() as client:
                ver_resp = await client.get("/version")
                if ver_resp.status_code == 200:
                    api_ok = True
                    git_version = ver_resp.json().get("gitVersion", "")
                else:
                    error_detail = f"/version returned HTTP {ver_resp.status_code}"

                if api_ok:
                    met_resp = await client.get("/apis/metrics.k8s.io/v1beta1/pods")
                    metrics_ok = met_resp.status_code == 200
        except httpx.HTTPError as exc:
            error_detail = str(exc)

        detected_mode = self.cluster_mode
        if detected_mode == "auto":
            detected_mode = "gke" if "-gke." in git_version else "kind"

        if not api_ok:
            state: Literal["available", "partial", "unavailable"] = "unavailable"
        elif metrics_ok:
            state = "available"
        else:
            state = "partial"

        evidence: dict[str, Any] = {
            "api_server_reachable": api_ok,
            "metrics_server_available": metrics_ok,
            "cluster_mode": detected_mode,
            "git_version": git_version,
            "bearer_token_present": bool(self.bearer_token),
            "auth_plugin_used": None,
        }
        if error_detail:
            evidence["error"] = error_detail

        return AdapterHealth(
            adapter_name=self.name,
            family=self.family,
            state=state,
            evidence=evidence,
            checked_at=now,
        )

    async def discover(self) -> DiscoveryResult:
        """Discover Kubernetes Deployments, Services, metrics endpoints, and outbound call edges."""
        if not self.api_url:
            return DiscoveryResult(evidence={"reason": "api_url not configured"})

        now_iso = datetime.now(tz=UTC).isoformat()
        async with self._client() as client:
            dep_resp = await client.get("/apis/apps/v1/deployments")
            dep_resp.raise_for_status()
            deployments = dep_resp.json().get("items", [])

            svc_resp = await client.get("/api/v1/services")
            services_error: str | None = None
            if svc_resp.status_code == 200:
                svc_items = svc_resp.json().get("items", [])
            else:
                svc_items = []
                services_error = (
                    f"GET /api/v1/services returned HTTP {svc_resp.status_code}: {svc_resp.text}"
                )

        svc_lookup: dict[tuple[str, str], dict[str, Any]] = {}
        for item in svc_items:
            meta = item.get("metadata", {})
            ns = meta.get("namespace", "default")
            name = meta.get("name", "")
            if name:
                svc_lookup[(ns, name)] = item

        discovered_services: list[DiscoveredServiceDoc] = []
        discovered_edges: list[DiscoveredEdgeDoc] = []
        raw_env_links: list[tuple[str, str, str]] = []

        for dep in deployments:
            meta = dep.get("metadata", {})
            name = meta.get("name", "")
            namespace = meta.get("namespace", "default")
            labels: dict[str, str] = {str(k): str(v) for k, v in (meta.get("labels") or {}).items()}

            if not name or namespace in SYSTEM_NAMESPACES:
                continue
            if is_excluded_resource(name, labels):
                continue

            k8s_svc = svc_lookup.get((namespace, name), {})
            metrics_endpoint = self._infer_metrics_endpoint(name, namespace, dep, k8s_svc)

            doc_id = f"{self.env_id}__gke_workload.{name}"
            discovered_services.append(
                DiscoveredServiceDoc(
                    doc_id=doc_id,
                    env_id=self.env_id,
                    name=name,
                    kind="gke_workload",
                    adapter_family="kubernetes",
                    locator=f"k8s://{namespace}/{name}",
                    labels=labels,
                    metrics_endpoint=metrics_endpoint,
                    first_seen=now_iso,
                    last_seen=now_iso,
                    status="active",
                )
            )

            containers = (
                dep.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
            )
            for container in containers:
                for env_entry in container.get("env") or []:
                    env_name = str(env_entry.get("name", ""))
                    env_val = str(env_entry.get("value", ""))
                    if env_name.endswith("_URL") and env_val.startswith(("http://", "https://")):
                        parsed = urlparse(env_val)
                        if parsed.hostname:
                            target_host = parsed.hostname.split(".")[0]
                            raw_env_links.append((doc_id, target_host, parsed.scheme or "http"))

        by_name = {
            s.name: s.doc_id or f"{self.env_id}__gke_workload.{s.name}" for s in discovered_services
        }
        for src_id, target_name, scheme in raw_env_links:
            dst_id = by_name.get(target_name, f"{self.env_id}__gke_workload.{target_name}")
            edge_id = f"{src_id}__to__{dst_id}"
            discovered_edges.append(
                DiscoveredEdgeDoc(
                    doc_id=edge_id,
                    src=src_id,
                    dst=dst_id,
                    protocol=scheme,
                    evidence="k8s",
                    call_rate_baseline=0.0,
                    first_seen=now_iso,
                    last_seen=now_iso,
                )
            )

        evidence_dict: dict[str, Any] = {
            "deployments_scanned": len(deployments),
            "services_scanned": len(svc_items),
            "services_list_status_code": svc_resp.status_code,
            "discovered_workloads": len(discovered_services),
        }
        if services_error is not None:
            evidence_dict["services_list_error"] = services_error

        return DiscoveryResult(
            services=discovered_services,
            edges=discovered_edges,
            entities=[],
            evidence=evidence_dict,
        )

    @staticmethod
    def _infer_metrics_endpoint(
        name: str,
        namespace: str,
        deployment: dict[str, Any],
        service_obj: dict[str, Any],
    ) -> str:
        svc_meta = service_obj.get("metadata", {})
        annotations = svc_meta.get("annotations") or {}
        svc_spec = service_obj.get("spec") or {}
        svc_status = service_obj.get("status") or {}

        ingress_list = svc_status.get("loadBalancer", {}).get("ingress") or []
        host = ""
        if ingress_list and isinstance(ingress_list[0], dict):
            host = ingress_list[0].get("ip") or ingress_list[0].get("hostname") or ""
        if not host:
            host = (
                svc_spec.get("loadBalancerIP")
                or svc_spec.get("clusterIP")
                or f"{name}.{namespace}.svc"
            )

        port = int(annotations.get("prometheus.io/port", 0) or 0)
        if not port:
            ports = svc_spec.get("ports") or []
            if ports and isinstance(ports[0], dict):
                port = int(ports[0].get("port", 8080))
        if not port:
            containers = (
                deployment.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
            )
            if containers and containers[0].get("ports"):
                port = int(containers[0]["ports"][0].get("containerPort", 8080))
        if not port:
            port = 8080

        metrics_path = annotations.get("prometheus.io/path", "/metrics")
        if not metrics_path.startswith("/"):
            metrics_path = "/" + metrics_path
        return f"http://{host}:{port}{metrics_path}"

    async def list_events(
        self,
        *,
        namespace: str | None = None,
        involved_object_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch Kubernetes Events (OOMKilling, BackOff, Unhealthy, etc.)."""
        async with self._client() as client:
            resp = await client.get("/api/v1/events")
            if resp.status_code == 404 and namespace:
                resp = await client.get(f"/api/v1/namespaces/{namespace}/events")
            resp.raise_for_status()
            items: list[dict[str, Any]] = resp.json().get("items", [])

        results: list[dict[str, Any]] = []
        for item in items:
            meta = item.get("metadata", {})
            involved = item.get("involvedObject", {})
            item_ns = meta.get("namespace") or involved.get("namespace")
            if namespace and item_ns != namespace:
                continue
            if involved_object_name and involved.get("name") != involved_object_name:
                continue
            results.append(item)
        return results

    async def get_pod_metrics(self, *, namespace: str | None = None) -> list[dict[str, Any]]:
        """Query metrics-server (`/apis/metrics.k8s.io/v1beta1/pods`) for live pod CPU/memory."""
        async with self._client() as client:
            resp = await client.get("/apis/metrics.k8s.io/v1beta1/pods")
            resp.raise_for_status()
            items: list[dict[str, Any]] = resp.json().get("items", [])
        if namespace is None:
            return items
        return [item for item in items if item.get("metadata", {}).get("namespace") == namespace]

    async def scrape_prometheus_metrics(
        self,
        endpoint: str,
        *,
        bearer_token: str | None = None,
    ) -> dict[str, Any]:
        """Scrape a Prometheus `/metrics` endpoint and parse its samples."""
        kwargs: dict[str, Any] = {
            "headers": self._headers(token_override=bearer_token),
            "timeout": self.timeout_s,
            "verify": self.verify_tls,
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.get(endpoint)
            resp.raise_for_status()
            parsed = parse_prometheus_text(resp.text)
            parsed["endpoint"] = endpoint
            return parsed

    async def _get_deployment(self, namespace: str, deployment: str) -> dict[str, Any]:
        async with self._client() as client:
            resp = await client.get(
                f"/apis/apps/v1/namespaces/{namespace}/deployments/{deployment}"
            )
            resp.raise_for_status()
            return dict(resp.json())

    @staticmethod
    def _assert_managed_resource(resource_doc: dict[str, Any], name: str) -> None:
        meta = resource_doc.get("metadata", {})
        labels: dict[str, str] = {str(k): str(v) for k, v in (meta.get("labels") or {}).items()}
        if is_excluded_resource(name, labels):
            raise TrustViolation(
                f"Target Kubernetes resource '{name}' is an excluded observer resource (Decision A11)"
            )
        if labels.get(MANAGED_LABEL_KEY) != MANAGED_LABEL_VALUE:
            raise TrustViolation(
                f"Target Kubernetes resource '{name}' does not carry label "
                f"{MANAGED_LABEL_KEY}={MANAGED_LABEL_VALUE!r} (TDD I-07)"
            )

    async def _execute_capability_impl(
        self,
        capability_id: str,
        args: dict[str, Any],
        trust_decision: TrustDecision | None = None,
    ) -> Any:
        spec = get_capability(capability_id)

        if capability_id == "metrics.scrape":
            endpoint = str(args.get("endpoint", "")).strip()
            if not endpoint:
                raise ValueError("metrics.scrape requires 'endpoint' in args")
            return await self.scrape_prometheus_metrics(
                endpoint,
                bearer_token=args.get("bearer_token"),
            )

        if capability_id == "k8s.get_resource":
            kind = str(args.get("kind", "Deployment"))
            namespace = str(args.get("namespace", "default"))
            name = str(args.get("name", ""))
            if not name:
                raise ValueError("k8s.get_resource requires 'name' in args")

            if self.gke_mcp_client is not None and self.cluster_mode == "gke":
                validate_mcp_tool_binding("gke-mcp", "get_k8s_resource")
                return await self.gke_mcp_client.call_tool(
                    "get_k8s_resource",
                    {"kind": kind, "namespace": namespace, "name": name},
                )

            kind_lower = kind.lower()
            if kind_lower in ("deployment", "deployments"):
                path = f"/apis/apps/v1/namespaces/{namespace}/deployments/{name}"
            elif kind_lower in ("service", "services"):
                path = f"/api/v1/namespaces/{namespace}/services/{name}"
            elif kind_lower in ("pod", "pods"):
                path = f"/api/v1/namespaces/{namespace}/pods/{name}"
            else:
                path = f"/api/v1/namespaces/{namespace}/{kind_lower}s/{name}"

            async with self._client() as client:
                resp = await client.get(path)
                resp.raise_for_status()
                return resp.json()

        if capability_id == "k8s.rollout_restart":
            _validate_mutating_trust(capability_id, spec, trust_decision)
            namespace = str(args.get("namespace", "default"))
            deployment = str(args.get("deployment") or args.get("name") or "")
            if not deployment:
                raise ValueError("k8s.rollout_restart requires 'deployment' in args")

            dep_doc = await self._get_deployment(namespace, deployment)
            self._assert_managed_resource(dep_doc, deployment)

            restarted_at = datetime.now(tz=UTC).isoformat()
            patch_body = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "kubectl.kubernetes.io/restartedAt": restarted_at,
                            }
                        }
                    }
                }
            }
            async with self._client() as client:
                patch_resp = await client.patch(
                    f"/apis/apps/v1/namespaces/{namespace}/deployments/{deployment}",
                    json=patch_body,
                    headers={
                        **self._headers(),
                        "Content-Type": "application/strategic-merge-patch+json",
                    },
                )
                patch_resp.raise_for_status()

            return {
                "status": "restarted",
                "namespace": namespace,
                "deployment": deployment,
                "restarted_at": restarted_at,
                "target_resource": f"k8s://{namespace}/deployment/{deployment}",
                "reversal_recipe": {
                    "capability": "k8s.rollout_restart",
                    "args": {"namespace": namespace, "deployment": deployment},
                },
            }

        if capability_id == "k8s.scale":
            _validate_mutating_trust(capability_id, spec, trust_decision)
            namespace = str(args.get("namespace", "default"))
            deployment = str(args.get("deployment") or args.get("name") or "")
            if not deployment or "replicas" not in args:
                raise ValueError("k8s.scale requires 'deployment' and 'replicas' in args")
            replicas = int(args["replicas"])
            if replicas < 1:
                raise ValueError(
                    f"k8s.scale requires replicas >= 1 (got {replicas}); scale-to-zero belongs to hibernate"
                )

            dep_doc = await self._get_deployment(namespace, deployment)
            self._assert_managed_resource(dep_doc, deployment)
            previous_replicas = int(dep_doc.get("spec", {}).get("replicas", 1))

            patch_body = {"spec": {"replicas": replicas}}
            async with self._client() as client:
                patch_resp = await client.patch(
                    f"/apis/apps/v1/namespaces/{namespace}/deployments/{deployment}/scale",
                    json=patch_body,
                    headers={
                        **self._headers(),
                        "Content-Type": "application/merge-patch+json",
                    },
                )
                patch_resp.raise_for_status()

            return {
                "status": "scaled",
                "namespace": namespace,
                "deployment": deployment,
                "replicas": replicas,
                "previous_replicas": previous_replicas,
                "target_resource": f"k8s://{namespace}/deployment/{deployment}",
                "reversal_recipe": {
                    "capability": "k8s.scale",
                    "args": {
                        "namespace": namespace,
                        "deployment": deployment,
                        "replicas": previous_replicas,
                    },
                },
            }

        raise NotImplementedError(
            f"Adapter '{self.name}' does not implement capability '{capability_id}'"
        )
