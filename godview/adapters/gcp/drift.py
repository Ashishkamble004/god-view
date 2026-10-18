"""Custom tfstate-vs-live attribute drift engine, HTTP transport, and fast API restore (TF Spec §5, TDD §3.7 & §6.2 #2).

HashiCorp `terraform-mcp` is registry-docs/HCP-only and never used for drift detection or apply.
This module provides:
- `resolve_gcp_project_id` and `resolve_gcp_bearer_token` (inferring project_id and credentials from
  environment or GCE metadata server, never defaulting to a placeholder).
- `build_fixture_http_transport`: `httpx.AsyncBaseTransport` serving recorded GCP API fixtures so
  every adapter executes real `httpx.AsyncClient` requests in both live and hermetic modes.
- `is_resource_excluded`, `assert_managed_resource`, and `assert_valid_mutating_trust` enforcing
  TDD I-06, I-07, and Coordinator Decision A11.
- `TerraformDriftAdapter`: reads `platform/default.tfstate` from `gs://{project_id}-god-view-tfstate`
  via `httpx`, caches by GCS object `generation`, diffs curated attributes on managed non-excluded
  resources and IAM bindings, joins `suspected_actor` from Audit Logs, emits `godview.sentinel.breach`
  CloudEvents for unplanned drift, and executes `tf.restore_attribute` via live API `PATCH`.
"""

from __future__ import annotations

import copy
import json
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, unquote

import httpx
from godview_shared.adapter_contract import (
    AdapterHealth,
    DiscoveryResult,
    SenseAdapter,
    SenseCategory,
)
from godview_shared.capabilities import CapabilitySpec, get_capability
from godview_shared.config import (
    MANAGED_LABEL_KEY,
    MANAGED_LABEL_VALUE,
    is_excluded_resource,
)
from godview_shared.contracts import NervousSystemEvent, TrustDecision, TrustViolation


class UnmanagedResourceError(PermissionError):
    """Raised when a mutating capability targets a resource without `godview_managed="true"` or an excluded resource (TDD I-07)."""


def is_resource_excluded(
    resource_name: str,
    labels: dict[str, str] | None = None,
) -> bool:
    """Return True if a resource is excluded by observer role, default observer exclusions, or `GODVIEW_HINT_EXCLUDE` (Decision A11, TDD I-07)."""
    candidates = {
        resource_name,
        resource_name.rsplit("/", 1)[-1],
        resource_name.rsplit(".", 1)[-1],
    }
    for candidate in candidates:
        if not candidate:
            continue
        if is_excluded_resource(candidate, labels):
            return True
    return False


def assert_managed_resource(
    labels: dict[str, str] | None,
    resource_ref: str,
    *,
    live_labels: dict[str, str] | None = None,
) -> None:
    """Assert that a resource carries `godview_managed="true"` and is not excluded before mutating it (TDD I-07, A11)."""
    if is_resource_excluded(resource_ref, live_labels) or is_resource_excluded(
        resource_ref, labels
    ):
        raise UnmanagedResourceError(
            f"Refusing to mutate '{resource_ref}': resource is excluded by observer role or GODVIEW_HINT_EXCLUDE (TDD I-07, Decision A11)"
        )
    if live_labels is None:
        raise UnmanagedResourceError(
            f"Refusing to mutate '{resource_ref}': live resource labels could not be verified (TDD I-07)"
        )
    if live_labels.get(MANAGED_LABEL_KEY) != MANAGED_LABEL_VALUE:
        raise UnmanagedResourceError(
            f"Refusing to mutate '{resource_ref}': live resource lacks label {MANAGED_LABEL_KEY}='{MANAGED_LABEL_VALUE}' (TDD I-07)"
        )
    if labels is not None and labels.get(MANAGED_LABEL_KEY) != MANAGED_LABEL_VALUE:
        raise UnmanagedResourceError(
            f"Refusing to mutate '{resource_ref}': resource lacks label {MANAGED_LABEL_KEY}='{MANAGED_LABEL_VALUE}' (TDD I-07)"
        )


def assert_valid_mutating_trust(
    capability_id: str,
    trust_decision: TrustDecision | None,
) -> TrustDecision:
    """Enforce full TrustGate validation for mutating capabilities and SA impersonation (TDD I-06, Decision A10)."""
    if trust_decision is None or not trust_decision.authorized:
        raise TrustViolation(
            f"Mutating capability '{capability_id}' requires an authorized TrustDecision (TDD I-06)"
        )
    try:
        spec = get_capability(capability_id)
    except KeyError as exc:
        raise TrustViolation(f"Unknown capability '{capability_id}' in TrustDecision") from exc

    if not spec.mutating:
        raise TrustViolation(
            f"Capability '{capability_id}' is read-only and cannot authorize mutating execution or godview-act-sa impersonation"
        )
    if trust_decision.capability_id != capability_id:
        raise TrustViolation(
            f"TrustDecision capability_id mismatch: expected '{capability_id}', got '{trust_decision.capability_id}'"
        )
    if trust_decision.action_type != capability_id:
        raise TrustViolation(
            f"TrustDecision action_type mismatch: expected '{capability_id}', got '{trust_decision.action_type}'"
        )
    if trust_decision.required_level != spec.min_level:
        raise TrustViolation(
            f"TrustDecision required_level mismatch for '{capability_id}': expected {spec.min_level}, got {trust_decision.required_level}"
        )
    if trust_decision.blast_radius_resources != 1:
        raise TrustViolation(
            f"Blast radius must be 1 resource per action (got {trust_decision.blast_radius_resources})"
        )
    if spec.sovereign_only:
        if trust_decision.agent_id != "sovereign":
            raise TrustViolation(
                f"Sovereign-only capability '{capability_id}' cannot be invoked by '{trust_decision.agent_id}'"
            )
        if not trust_decision.sa_to_impersonate:
            raise TrustViolation(
                f"Sovereign-only capability '{capability_id}' requires explicit sa_to_impersonate in TrustDecision"
            )
    else:
        if trust_decision.sa_to_impersonate != "godview-act-sa":
            raise TrustViolation(
                f"Mutating capability '{capability_id}' must impersonate 'godview-act-sa'"
            )
        if trust_decision.effective_level < 2 and spec.min_level >= 2:
            raise TrustViolation(
                f"Mutating capability '{capability_id}' refused: effective_level={trust_decision.effective_level} < 2"
            )
        if trust_decision.effective_level < 1:
            raise TrustViolation(
                f"Mutating capability '{capability_id}' refused: effective_level={trust_decision.effective_level} < 1"
            )
        if (
            trust_decision.effective_level == 2 or trust_decision.effective_level < spec.min_level
        ) and not trust_decision.approval_id:
            raise TrustViolation(
                f"Mutating capability '{capability_id}' at effective_level={trust_decision.effective_level} (min_level={spec.min_level}) requires approval_id"
            )
        if spec.customer_comms and not trust_decision.approval_id:
            raise TrustViolation(
                f"Customer communication capability '{capability_id}' is capped at L2 and always requires approval_id"
            )
        if (
            spec.money_movement
            and trust_decision.effective_level < 4
            and not trust_decision.approval_id
        ):
            raise TrustViolation(
                f"Money-movement capability '{capability_id}' requires L4 whitelist or explicit approval_id (Decision A10)"
            )
    return trust_decision


def _snake_to_camel(s: str) -> str:
    """Convert snake_case identifier to camelCase for GCP API field names."""
    components = s.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


async def _impersonate_act_sa_async(
    client: httpx.AsyncClient,
    project_id: str,
    sa_to_impersonate: str,
    bearer_token: str | None = None,
) -> str:
    """Impersonate godview-act-sa via IAM Credentials API (TDD I-06). Refuses on failure."""
    target_sa = (
        f"{sa_to_impersonate}@{project_id}.iam.gserviceaccount.com"
        if "@" not in sa_to_impersonate
        else sa_to_impersonate
    )
    endpoint = (
        f"https://iamcredentials.googleapis.com/v1/projects/-/"
        f"serviceAccounts/{target_sa}:generateAccessToken"
    )
    headers = {"Accept": "application/json"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    resp = await client.post(
        endpoint,
        json={
            "scope": ["https://www.googleapis.com/auth/cloud-platform"],
            "lifetime": "900s",
        },
        headers=headers,
    )
    if resp.status_code != 200:
        raise PermissionError(
            f"Refusing mutation: impersonation of '{target_sa}' failed with HTTP {resp.status_code}: {resp.text[:120]}"
        )
    data = resp.json()
    token = data.get("accessToken")
    if not token:
        raise PermissionError(
            f"Refusing mutation: IAM Credentials API returned empty accessToken for '{target_sa}'"
        )
    return str(token)


def _impersonate_act_sa_sync(
    client: httpx.Client,
    project_id: str,
    sa_to_impersonate: str,
    bearer_token: str | None = None,
) -> str:
    """Synchronous impersonation of godview-act-sa via IAM Credentials API (TDD I-06). Refuses on failure."""
    target_sa = (
        f"{sa_to_impersonate}@{project_id}.iam.gserviceaccount.com"
        if "@" not in sa_to_impersonate
        else sa_to_impersonate
    )
    endpoint = (
        f"https://iamcredentials.googleapis.com/v1/projects/-/"
        f"serviceAccounts/{target_sa}:generateAccessToken"
    )
    headers = {"Accept": "application/json"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    resp = client.post(
        endpoint,
        json={
            "scope": ["https://www.googleapis.com/auth/cloud-platform"],
            "lifetime": "900s",
        },
        headers=headers,
    )
    if resp.status_code != 200:
        raise PermissionError(
            f"Refusing mutation: impersonation of '{target_sa}' failed with HTTP {resp.status_code}: {resp.text[:120]}"
        )
    data = resp.json()
    token = data.get("accessToken")
    if not token:
        raise PermissionError(
            f"Refusing mutation: IAM Credentials API returned empty accessToken for '{target_sa}'"
        )
    return str(token)


def resolve_gcp_project_id(
    explicit_project_id: str | None = None,
    fixtures: dict[str, Any] | None = None,
) -> str:
    """Resolve GCP project_id from explicit argument, recorded fixture metadata, environment, or GCE metadata server (TDD I-02)."""
    if explicit_project_id and explicit_project_id.strip():
        return explicit_project_id.strip()
    if fixtures and isinstance(fixtures.get("project_id"), str) and fixtures["project_id"].strip():
        return str(fixtures["project_id"]).strip()
    for env_key in (
        "GOOGLE_CLOUD_PROJECT",
        "GCP_PROJECT_ID",
        "GCLOUD_PROJECT",
        "CLOUDSDK_CORE_PROJECT",
    ):
        val = os.environ.get(env_key, "").strip()
        if val:
            return val
    # Probe GCE metadata server if running in GCP without env var
    if os.environ.get("GODVIEW_ENV", "").strip().lower() == "gcp":
        try:
            resp = httpx.get(
                "http://metadata.google.internal/computeMetadata/v1/project/project-id",
                headers={"Metadata-Flavor": "Google"},
                timeout=1.5,
            )
            if resp.status_code == 200 and resp.text.strip():
                return resp.text.strip()
        except httpx.HTTPError:
            pass
    raise RuntimeError(
        "Could not infer GCP project_id from explicit argument, GOOGLE_CLOUD_PROJECT/GCP_PROJECT_ID environment variables, or GCE metadata server (TDD I-02)"
    )


def resolve_gcp_bearer_token(explicit_token: str | None = None) -> str:
    """Resolve GCP OAuth2 Bearer token from explicit argument, environment, ADC, or GCE metadata server."""
    if explicit_token and explicit_token.strip():
        return explicit_token.strip()
    for env_key in ("GOOGLE_OAUTH_ACCESS_TOKEN", "GCP_ACCESS_TOKEN"):
        val = os.environ.get(env_key, "").strip()
        if val:
            return val
    try:
        import google.auth
        import google.auth.transport.requests

        creds, _ = google.auth.default()
        req = google.auth.transport.requests.Request()
        creds.refresh(req)
        if creds.token:
            return str(creds.token).strip()
    except Exception:
        pass
    if os.environ.get("GODVIEW_ENV", "").strip().lower() == "gcp" or bool(os.environ.get("K_SERVICE")):
        try:
            resp = httpx.get(
                "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                headers={"Metadata-Flavor": "Google"},
                timeout=1.5,
            )
            if resp.status_code == 200:
                token = str(resp.json().get("access_token", "")).strip()
                if token:
                    return token
        except httpx.HTTPError:
            pass
    return ""


class _FixtureAsyncTransport(httpx.BaseTransport, httpx.AsyncBaseTransport):
    """Recorded-fixture `httpx` transport (sync + async) mapping GCP REST API endpoints to fixture payloads."""

    def __init__(self, fixtures: dict[str, Any]) -> None:
        self._fixtures = fixtures
        self.requests: list[httpx.Request] = []

    def _json_response(self, status_code: int, payload: Any) -> httpx.Response:
        return httpx.Response(
            status_code=status_code,
            headers={"Content-Type": "application/json"},
            content=json.dumps(payload).encode("utf-8"),
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._dispatch(request)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self._dispatch(request)

    def _dispatch(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        path = request.url.path
        method = request.method.upper()

        # Synthetic probe requests (carry X-Synthetic: true)
        if request.headers.get("X-Synthetic") == "true":
            body = json.loads(request.content.decode("utf-8")) if request.content else {}
            return self._json_response(
                200,
                {
                    "status_code": 200,
                    "synthetic": True,
                    "method": method,
                    "url": str(request.url),
                    "headers": dict(request.headers),
                    "body": body,
                },
            )

        # 1. Cloud Asset Inventory
        if host == "cloudasset.googleapis.com" and path.endswith("/assets"):
            if "cloud_asset" not in self._fixtures:
                return self._json_response(404, {"error": {"code": 404, "message": "not found"}})
            return self._json_response(200, self._fixtures["cloud_asset"])

        # 2. Cloud Run v2
        if host == "run.googleapis.com" and "/v2/" in path:
            if "cloud_run_v2" not in self._fixtures:
                return self._json_response(404, {"error": {"code": 404, "message": "not found"}})
            if method == "GET" and path.endswith("/services"):
                return self._json_response(200, self._fixtures["cloud_run_v2"])
            if method == "PATCH":
                body = json.loads(request.content.decode("utf-8")) if request.content else {}
                return self._json_response(
                    200,
                    {
                        "name": path.removeprefix("/v2/"),
                        "updateMask": request.url.params.get("updateMask", ""),
                        "appliedConfig": body,
                    },
                )

        # 3. GKE Container API
        if host == "container.googleapis.com" and "/clusters" in path:
            if "gke" not in self._fixtures:
                return self._json_response(404, {"error": {"code": 404, "message": "not found"}})
            if path.endswith("/clusters"):
                return self._json_response(200, self._fixtures["gke"])
            cluster_name = path.rsplit("/", 1)[-1]
            for cl in self._fixtures["gke"].get("clusters", []):
                if cl.get("name") == cluster_name:
                    return self._json_response(200, cl)
            return self._json_response(
                404, {"error": {"code": 404, "message": f"Cluster '{cluster_name}' not found"}}
            )

        # 4. Compute Engine
        if host == "compute.googleapis.com":
            if path.endswith("/forwardingRules"):
                fr_items = self._fixtures.get("forwarding_rules", {}).get("items", [])
                if "/aggregated/" in path:
                    return self._json_response(200, {"items": {"global": {"forwardingRules": fr_items}}})
                return self._json_response(200, self._fixtures.get("forwarding_rules", {"items": []}))
            if path.endswith("/urlMaps"):
                um_items = self._fixtures.get("url_maps", {}).get("items", [])
                if "/aggregated/" in path:
                    return self._json_response(200, {"items": {"global": {"urlMaps": um_items}}})
                return self._json_response(200, self._fixtures.get("url_maps", {"items": []}))
            if path.endswith("/backendServices"):
                bs_items = self._fixtures.get("backend_services", {}).get("items", [])
                if "/aggregated/" in path:
                    return self._json_response(200, {"items": {"global": {"backendServices": bs_items}}})
                return self._json_response(200, self._fixtures.get("backend_services", {"items": []}))
            if path.endswith("/securityPolicies"):
                sp_items = self._fixtures.get("security_policies", {}).get("items", [])
                if "/aggregated/" in path:
                    return self._json_response(200, {"items": {"global": {"securityPolicies": sp_items}}})
                return self._json_response(200, self._fixtures.get("security_policies", {"items": []}))
            if "/aggregated/instances" in path:
                zone = str(self._fixtures.get("zone", "us-central1-a"))
                inst_list = self._fixtures.get("compute", {}).get("items", [])
                agg_default = {"items": {f"zones/{zone}": {"instances": inst_list}}}
                return self._json_response(200, self._fixtures.get("compute_aggregated", agg_default))
            if "/instances" in path:
                if "compute" not in self._fixtures:
                    return self._json_response(404, {"error": {"code": 404, "message": "not found"}})
                if method == "GET":
                    return self._json_response(200, self._fixtures["compute"])
                if method in ("PATCH", "POST"):
                    body = json.loads(request.content.decode("utf-8")) if request.content else {}
                    return self._json_response(200, {"status": "DONE", "applied": body})

        # 4b. API Gateway
        if host == "apigateway.googleapis.com":
            if path.endswith("/gateways"):
                return self._json_response(200, self._fixtures.get("api_gateways", {"gateways": []}))
            if path.endswith("/configs"):
                return self._json_response(200, self._fixtures.get("api_configs", {"apiConfigs": []}))
            if "/configs/" in path:
                cfg_full = self._fixtures.get("api_config_full")
                if cfg_full:
                    return self._json_response(200, cfg_full)
                configs = self._fixtures.get("api_configs", {}).get("apiConfigs", [])
                if configs:
                    return self._json_response(200, configs[0])
                return self._json_response(404, {"error": {"code": 404, "message": "config not found"}})

        # 5. Cloud SQL Admin
        if host == "sqladmin.googleapis.com":
            if path.endswith("/databases"):
                return self._json_response(200, self._fixtures.get("sql_databases", {"items": []}))
            if path.endswith("/users"):
                return self._json_response(200, self._fixtures.get("sql_users", {"items": []}))
            if "/instances" in path:
                if "sql_admin" not in self._fixtures:
                    return self._json_response(404, {"error": {"code": 404, "message": "not found"}})
                if method == "GET":
                    return self._json_response(200, self._fixtures["sql_admin"])
                if method in ("PATCH", "POST"):
                    body = json.loads(request.content.decode("utf-8")) if request.content else {}
                    return self._json_response(200, {"status": "DONE", "applied": body})

        # 6. Cloud Monitoring
        if host == "monitoring.googleapis.com":
            if "monitoring" not in self._fixtures:
                return self._json_response(404, {"error": {"code": 404, "message": "not found"}})
            if path.endswith("/alertPolicies"):
                return self._json_response(200, self._fixtures["monitoring"])
            if path.endswith("/timeSeries"):
                filter_str = str(request.url.params.get("filter", ""))
                if "num_undelivered_messages" in filter_str:
                    num_undelivered = self._fixtures.get("pubsub_num_undelivered", 0)
                    return self._json_response(200, {
                        "timeSeries": [{
                            "metric": {"type": "pubsub.googleapis.com/subscription/num_undelivered_messages"},
                            "points": [{"value": {"int64Value": str(num_undelivered)}}],
                        }]
                    })
                if "oldest_unacked_message_age" in filter_str:
                    ack_age = self._fixtures.get("pubsub_ack_age_s", 0)
                    return self._json_response(200, {
                        "timeSeries": [{
                            "metric": {"type": "pubsub.googleapis.com/subscription/oldest_unacked_message_age"},
                            "points": [{"value": {"int64Value": str(ack_age)}}],
                        }]
                    })
                if "push_request_count" in filter_str:
                    return self._json_response(200, {
                        "timeSeries": [
                            {
                                "metric": {
                                    "type": "pubsub.googleapis.com/subscription/push_request_count",
                                    "labels": {"response_class": "2xx"},
                                },
                                "points": [{"value": {"int64Value": "150"}}],
                            },
                            {
                                "metric": {
                                    "type": "pubsub.googleapis.com/subscription/push_request_count",
                                    "labels": {"response_class": "5xx"},
                                },
                                "points": [{"value": {"int64Value": "2"}}],
                            },
                        ]
                    })
                ts = self._fixtures["monitoring"].get("timeSeries", [])
                return self._json_response(200, {"timeSeries": ts})

        # 7. Cloud Trace
        if host == "cloudtrace.googleapis.com" and "/traces" in path:
            if "trace" not in self._fixtures:
                return self._json_response(404, {"error": {"code": 404, "message": "not found"}})
            if path.endswith("/traces"):
                return self._json_response(200, self._fixtures["trace"])
            trace_id = path.rsplit("/", 1)[-1]
            for tr in self._fixtures["trace"].get("traces", []):
                if tr.get("traceId") == trace_id:
                    return self._json_response(200, tr)
            return self._json_response(
                404, {"error": {"code": 404, "message": f"Trace '{trace_id}' not found"}}
            )

        # 8. Cloud Logging
        if host == "logging.googleapis.com":
            if path.endswith("/sinks"):
                if "logging_sinks" not in self._fixtures:
                    return self._json_response(
                        404, {"error": {"code": 404, "message": "not found"}}
                    )
                return self._json_response(200, self._fixtures["logging_sinks"])
            if path.endswith("/entries:list"):
                body = json.loads(request.content.decode("utf-8")) if request.content else {}
                filter_str = str(body.get("filter", ""))
                if "http_load_balancer" in filter_str or "enforcedSecurityPolicy" in filter_str or "statusDetails" in filter_str:
                    entries = self._fixtures.get("lb_log_entries") or self._fixtures.get("logging_entries", [])
                else:
                    entries = self._fixtures.get("logging_entries", [])
                return self._json_response(200, {"entries": entries})

        # 9. Pub/Sub
        if host == "pubsub.googleapis.com":
            if path.endswith("/topics"):
                if "pubsub" not in self._fixtures:
                    return self._json_response(404, {"error": {"code": 404, "message": "not found"}})
                return self._json_response(200, self._fixtures["pubsub"])
            if path.endswith("/subscriptions"):
                return self._json_response(200, self._fixtures.get("pubsub_subscriptions", {"subscriptions": []}))

        # 10. BigQuery
        if host == "bigquery.googleapis.com":
            if "bigquery" not in self._fixtures:
                return self._json_response(404, {"error": {"code": 404, "message": "not found"}})
            if path.endswith("/datasets"):
                return self._json_response(200, self._fixtures["bigquery"])
            if path.endswith("/queries") and method == "POST":
                body = json.loads(request.content.decode("utf-8")) if request.content else {}
                query_text = str(body.get("query", ""))
                rows = self._fixtures["bigquery"].get(
                    "query_rows",
                    [{"status": "ok", "query": query_text}],
                )
                return self._json_response(
                    200,
                    {
                        "kind": "bigquery#queryResponse",
                        "jobComplete": True,
                        "rows": rows,
                        "billingTier": 1,
                    },
                )

        # 11. Datastream
        if host == "datastream.googleapis.com":
            ds = self._fixtures.get(
                "datastream",
                {"error": {"code": 403, "status": "PERMISSION_DENIED"}},
            )
            code = int(ds.get("error", {}).get("code", 403)) if "error" in ds else 200
            return self._json_response(code, ds)

        # 12. Firestore
        if host == "firestore.googleapis.com" and path.endswith("/databases"):
            if "firestore" not in self._fixtures:
                return self._json_response(404, {"error": {"code": 404, "message": "not found"}})
            return self._json_response(200, self._fixtures["firestore"])

        # 13. Vertex AI Agent Engine
        if host.endswith("aiplatform.googleapis.com") and "/reasoningEngines" in path:
            if "agent_engine" not in self._fixtures:
                return self._json_response(404, {"error": {"code": 404, "message": "not found"}})
            if method == "GET" and path.endswith("/reasoningEngines"):
                return self._json_response(200, self._fixtures["agent_engine"])
            if method == "PATCH":
                body = json.loads(request.content.decode("utf-8")) if request.content else {}
                dep_spec = body.get("spec", {}).get("deploymentSpec", {})
                return self._json_response(
                    200,
                    {
                        "name": path.removeprefix("/v1beta1/"),
                        "updateMask": request.url.params.get("updateMask", ""),
                        "spec": {"deploymentSpec": dep_spec},
                    },
                )

        # 14. Agent Registry
        if host == "agentregistry.googleapis.com":
            reg = self._fixtures.get("agent_registry")
            if not reg or not isinstance(reg, dict):
                return self._json_response(404, {"error": {"code": 404, "status": "NOT_FOUND"}})
            if "error" in reg:
                code = int(reg["error"].get("code", 404))
                return self._json_response(code, reg)
            if method == "POST":
                body = json.loads(request.content.decode("utf-8")) if request.content else {}
                return self._json_response(200, {"catalogued": True, **body})
            return self._json_response(200, reg)

        # 15. Model Armor
        if host.startswith("modelarmor.") and host.endswith(".rep.googleapis.com"):
            if "model_armor" not in self._fixtures:
                return self._json_response(404, {"error": {"code": 404, "message": "not found"}})
            if method == "GET" and path.endswith("/templates"):
                return self._json_response(200, self._fixtures["model_armor"])
            if method == "POST" and path.endswith(":sanitizeUserPrompt"):
                body = json.loads(request.content.decode("utf-8")) if request.content else {}
                prompt_text = str(body.get("userPromptData", {}).get("text", "")).lower()
                blocked_markers = (
                    "ignore previous instructions",
                    "ignore all previous instructions",
                    "system prompt",
                    "jailbreak",
                    "bypass policy guard",
                    "drop table",
                    "union select",
                    "override_guard",
                )
                matched = next((m for m in blocked_markers if m in prompt_text), None)
                if matched is not None:
                    return self._json_response(
                        200,
                        {
                            "sanitizationResult": {
                                "filterMatchState": "MATCH_FOUND",
                                "filterResults": {
                                    "piAndJailbreakFilterResult": {
                                        "matchState": "MATCH_FOUND",
                                        "matchedPattern": matched,
                                    }
                                },
                            }
                        },
                    )
                return self._json_response(
                    200,
                    {
                        "sanitizationResult": {
                            "filterMatchState": "NO_MATCH_FOUND",
                            "filterResults": {
                                "piAndJailbreakFilterResult": {"matchState": "NO_MATCH_FOUND"}
                            },
                        }
                    },
                )

        # 16. IAM & Resource Manager & IAM Credentials
        if host == "iam.googleapis.com" and path.endswith("/serviceAccounts"):
            if "iam" not in self._fixtures:
                return self._json_response(404, {"error": {"code": 404, "message": "not found"}})
            return self._json_response(200, {"accounts": self._fixtures["iam"].get("accounts", [])})
        if host == "cloudresourcemanager.googleapis.com" and path.endswith(":testIamPermissions"):
            if "iam" not in self._fixtures:
                return self._json_response(404, {"error": {"code": 404, "message": "not found"}})
            return self._json_response(
                200, {"permissions": self._fixtures["iam"].get("permissions", [])}
            )
        if host == "iamcredentials.googleapis.com" and path.endswith(":generateAccessToken"):
            if "iam" not in self._fixtures:
                return self._json_response(404, {"error": {"code": 404, "message": "not found"}})
            sa_segment = path.split("/serviceAccounts/")[-1].split(":generateAccessToken")[0]
            accounts = [a.get("email", "") for a in self._fixtures["iam"].get("accounts", [])]
            if accounts and sa_segment not in accounts:
                return self._json_response(
                    403, {"error": {"code": 403, "message": f"SA {sa_segment} not in project"}}
                )
            token_val = self._fixtures["iam"].get("impersonated_access_token")
            if not token_val:
                if os.environ.get("GODVIEW_ENV") != "hermetic":
                    return self._json_response(
                        401,
                        {"error": {"code": 401, "message": "Impersonated token requires hermetic mode or explicit fixture"}},
                    )
                token_val = "hermetic-fixture-token-act-sa"
            return self._json_response(
                200,
                {
                    "accessToken": token_val,
                    "expireTime": "2026-10-16T18:00:00Z",
                },
            )

        # 17. Cloud Storage (tfstate metadata and object download)
        if host == "storage.googleapis.com" and "/storage/v1/b/" in path:
            if "tfstate" not in self._fixtures:
                return self._json_response(404, {"error": {"code": 404, "message": "not found"}})
            alt = request.url.params.get("alt", "")
            if alt == "media":
                return self._json_response(200, self._fixtures["tfstate"].get("state", {}))
            obj_name = (
                unquote(path.split("/o/")[-1]) if "/o/" in path else "platform/default.tfstate"
            )
            meta = dict(self._fixtures["tfstate"].get("object_meta", {}))
            meta["name"] = obj_name
            return self._json_response(200, meta)

        # 18. Generic HTTP probe targets (e.g. Cloud Run, Gateway, public endpoints)
        if host.endswith(".run.app") or host.endswith(".gateway.dev") or host in ("example.com", "localhost"):
            return self._json_response(200, {"status": "ok", "probe": "passed"})

        return self._json_response(
            404,
            {"error": {"code": 404, "message": f"Unhandled fixture route {method} {request.url}"}},
        )


def build_fixture_http_transport(fixtures: dict[str, Any]) -> _FixtureAsyncTransport:
    """Create an `httpx.AsyncBaseTransport` backed by recorded GCP API fixtures."""
    return _FixtureAsyncTransport(fixtures)


_SENSITIVE_KEY_SUBSTRINGS = (
    "password",
    "private_key",
    "secret",
    "token",
    "credential",
    "auth_token",
    "client_key",
    "client_certificate",
    "cluster_ca_certificate",
    "key_string",
    "kube_config",
    "connection_string",
    "database_url",
    "jwt",
    "signature",
)


def _redact_tfstate_node(node: Any) -> Any:
    """Recursively redact sensitive credentials, passwords, and tokens from tfstate (Decision A2/A8)."""
    if isinstance(node, dict):
        cleaned: dict[str, Any] = {}
        sensitive_paths: list[list[str]] = []
        for sa in node.get("sensitive_attributes", []):
            if isinstance(sa, list):
                path: list[str] = []
                for step in sa:
                    if isinstance(step, dict) and "value" in step:
                        path.append(str(step["value"]))
                    elif isinstance(step, str):
                        path.append(step)
                if path:
                    sensitive_paths.append(path)
            elif isinstance(sa, str):
                sensitive_paths.append(sa.split("."))

        for k, v in node.items():
            lower_k = str(k).lower()
            if any(s in lower_k for s in _SENSITIVE_KEY_SUBSTRINGS):
                cleaned[k] = "[REDACTED]"
            elif lower_k in ("result", "bcrypt_hash") and isinstance(v, str) and len(v) > 0:
                cleaned[k] = "[REDACTED]"
            elif isinstance(v, dict) and bool(v.get("sensitive")):
                cleaned[k] = {"sensitive": True, "value": "[REDACTED]"}
            elif isinstance(v, str) and v.startswith("-----BEGIN "):
                cleaned[k] = "[REDACTED]"
            else:
                cleaned[k] = _redact_tfstate_node(v)

        def _apply_redaction(target: Any, path: list[str]) -> None:
            if not isinstance(target, dict) or not path:
                return
            first = path[0]
            if len(path) == 1:
                if first in target:
                    target[first] = "[REDACTED]"
            else:
                if first in target:
                    _apply_redaction(target[first], path[1:])

        # Apply sensitive paths against cleaned instance attributes and cleaned root
        for p in sensitive_paths:
            if "attributes" in cleaned and isinstance(cleaned["attributes"], dict):
                _apply_redaction(cleaned["attributes"], p)
            _apply_redaction(cleaned, p)

        return cleaned
    if isinstance(node, list):
        return [_redact_tfstate_node(item) for item in node]
    return node


class TerraformDriftAdapter(SenseAdapter):
    """SenseAdapter for GCS tfstate inspection, curated attribute drift diffing, and fast API restore."""

    family = "gcp"
    name = "gcp-terraform-drift"

    def __init__(
        self,
        *,
        project_id: str | None = None,
        region: str = "us-central1",
        zone: str = "us-central1-a",
        tfstate_bucket: str | None = None,
        auth: Any | None = None,
        bearer_token: str | None = None,
        fixtures: dict[str, Any] | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._fixtures = fixtures
        self.project_id = resolve_gcp_project_id(project_id, fixtures)
        self.region = region
        self.zone = zone
        self.tfstate_bucket = tfstate_bucket or f"{self.project_id}-god-view-tfstate"
        self.auth = auth
        self.bearer_token = resolve_gcp_bearer_token(bearer_token)
        self._transport = http_transport or (
            build_fixture_http_transport(fixtures) if fixtures is not None else None
        )
        self._cached_tfstate_by_prefix: dict[str, dict[str, Any]] = {}
        self._cached_generation_by_prefix: dict[str, str] = {}
        self.tfstate_download_count: int = 0
        self._restored_log: list[dict[str, Any]] = []

    def _http_client(self) -> httpx.AsyncClient:
        headers: dict[str, str] = {"Accept": "application/json"}
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
        kwargs: dict[str, Any] = {"headers": headers, "timeout": 15.0}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def discover(self) -> DiscoveryResult:
        try:
            state_data = await self._read_tfstate(prefix="platform")
            resources = state_data.get("state", {}).get("resources", [])
            return DiscoveryResult(
                services=[],
                edges=[],
                entities=[],
                evidence={
                    "bucket": self.tfstate_bucket,
                    "generation": state_data.get("generation"),
                    "terraform_resources_count": len(resources),
                },
            )
        except Exception as exc:
            return DiscoveryResult(
                services=[],
                edges=[],
                entities=[],
                evidence={"error": str(exc), "state": "unavailable", "bucket": self.tfstate_bucket},
            )

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.CHANGE_PLANE, SenseCategory.CONTRACTS]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {
            "tfstate.read": get_capability("tfstate.read"),
            "tf.drift_diff": get_capability("tf.drift_diff"),
            "tf.restore_attribute": get_capability("tf.restore_attribute"),
        }

    async def health(self) -> AdapterHealth:
        try:
            async with self._http_client() as client:
                obj_encoded = quote("platform/default.tfstate", safe="")
                resp = await client.get(
                    f"https://storage.googleapis.com/storage/v1/b/{self.tfstate_bucket}/o/{obj_encoded}"
                )
                has_tfstate = resp.status_code == 200
        except httpx.HTTPError:
            has_tfstate = False
        evidence: dict[str, Any] = {
            "bucket": self.tfstate_bucket,
            "prefix": "platform",
            "has_tfstate": has_tfstate,
        }
        if not has_tfstate:
            evidence["error"] = "GCS tfstate object not found or unavailable"
        return AdapterHealth(
            adapter_name=self.name,
            family="gcp",
            state="available" if has_tfstate else "unavailable",
            evidence=evidence,
            checked_at=datetime.now(tz=UTC),
        )

    async def _execute_capability_impl(
        self,
        capability_id: str,
        args: dict[str, Any],
        trust_decision: TrustDecision | None = None,
    ) -> Any:
        if capability_id == "tfstate.read":
            prefix = str(args.get("prefix", "platform"))
            return await self._read_tfstate(prefix=prefix)
        if capability_id == "tf.drift_diff":
            return await self._drift_diff(
                prefix=str(args.get("prefix", "platform")),
                planned_operator_action=bool(args.get("planned_operator_action", False)),
            )
        if capability_id == "tf.restore_attribute":
            return await self._restore_attribute(args, trust_decision)
        raise NotImplementedError(f"Unsupported capability '{capability_id}'")

    async def _read_tfstate(self, prefix: str = "platform") -> dict[str, Any]:
        """Read `<prefix>/default.tfstate` via GCS REST API and cache by object `generation` (TF Spec §5.2)."""
        object_name = f"{prefix}/default.tfstate"
        obj_encoded = quote(object_name, safe="")
        base_url = (
            f"https://storage.googleapis.com/storage/v1/b/{self.tfstate_bucket}/o/{obj_encoded}"
        )

        async with self._http_client() as client:
            meta_resp = await client.get(base_url)
            meta_resp.raise_for_status()
            obj_meta = meta_resp.json()
            generation = str(obj_meta.get("generation", "0"))

            if (
                prefix in self._cached_tfstate_by_prefix
                and self._cached_generation_by_prefix.get(prefix) == generation
            ):
                return {
                    "bucket": self.tfstate_bucket,
                    "object": object_name,
                    "generation": generation,
                    "from_cache": True,
                    "state": copy.deepcopy(self._cached_tfstate_by_prefix[prefix]),
                }

            media_resp = await client.get(base_url, params={"alt": "media"})
            media_resp.raise_for_status()
            raw_state = _redact_tfstate_node(copy.deepcopy(media_resp.json()))

        self.tfstate_download_count += 1
        self._cached_tfstate_by_prefix[prefix] = raw_state
        self._cached_generation_by_prefix[prefix] = generation
        return {
            "bucket": self.tfstate_bucket,
            "object": object_name,
            "generation": generation,
            "from_cache": False,
            "state": copy.deepcopy(raw_state),
        }

    def _lookup_suspected_actor(self, resource_name: str) -> str:
        """Join recent Audit Logs to identify `suspected_actor` for a drifted resource (TF Spec §5.2)."""
        audit_logs = (self._fixtures or {}).get("audit_logs", [])
        for entry in audit_logs:
            if resource_name in str(entry.get("resource", "")):
                return str(entry.get("principal", "unknown"))
        return "unknown"

    async def _fetch_live_resources(
        self,
        region: str | None = None,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        """Fetch live Cloud Run v2, Compute Engine, and Cloud SQL resources over `httpx`."""
        live_run_by_name: dict[str, dict[str, Any]] = {}
        live_vm_by_name: dict[str, dict[str, Any]] = {}
        live_sql_by_name: dict[str, dict[str, Any]] = {}

        async with self._http_client() as client:
            # 1. Cloud Run (locations/{region} or locations/- with pagination)
            loc = region if region else "-"
            page_token: str | None = None
            while True:
                params = {"pageToken": page_token} if page_token else {}
                run_resp = await client.get(
                    f"https://run.googleapis.com/v2/projects/{self.project_id}/locations/{loc}/services",
                    params=params,
                )
                if run_resp.status_code == 200:
                    data = run_resp.json()
                    for svc in data.get("services", []):
                        short_name = str(svc.get("name", "")).rsplit("/", 1)[-1]
                        live_run_by_name[short_name] = svc
                    page_token = data.get("nextPageToken")
                    if not page_token:
                        break
                else:
                    break

            # 2. Compute Engine (aggregated/instances with pagination)
            page_token = None
            while True:
                params = {"pageToken": page_token} if page_token else {}
                comp_resp = await client.get(
                    f"https://compute.googleapis.com/compute/v1/projects/{self.project_id}/aggregated/instances",
                    params=params,
                )
                if comp_resp.status_code == 200:
                    data = comp_resp.json()
                    raw_items = data.get("items", {})
                    if isinstance(raw_items, dict):
                        for scope_val in raw_items.values():
                            if isinstance(scope_val, dict) and "instances" in scope_val:
                                for vm in scope_val["instances"]:
                                    live_vm_by_name[str(vm.get("name", ""))] = vm
                    elif isinstance(raw_items, list):
                        for vm in raw_items:
                            live_vm_by_name[str(vm.get("name", ""))] = vm
                    page_token = data.get("nextPageToken")
                    if not page_token:
                        break
                elif comp_resp.status_code in (404, 405):
                    # fallback to zonal instances
                    z_resp = await client.get(
                        f"https://compute.googleapis.com/compute/v1/projects/{self.project_id}/zones/{self.zone}/instances",
                    )
                    if z_resp.status_code == 200:
                        for vm in z_resp.json().get("items", []):
                            live_vm_by_name[str(vm.get("name", ""))] = vm
                    break
                else:
                    break

            # 3. Cloud SQL (instances with pagination)
            page_token = None
            while True:
                params = {"pageToken": page_token} if page_token else {}
                sql_resp = await client.get(
                    f"https://sqladmin.googleapis.com/sql/v1beta4/projects/{self.project_id}/instances",
                    params=params,
                )
                if sql_resp.status_code == 200:
                    data = sql_resp.json()
                    for sql_inst in data.get("items", []):
                        live_sql_by_name[str(sql_inst.get("name", ""))] = sql_inst
                    page_token = data.get("nextPageToken")
                    if not page_token:
                        break
                else:
                    break

        return live_run_by_name, live_vm_by_name, live_sql_by_name

    async def _fetch_live_iam_bindings(self) -> list[dict[str, str]]:
        if (
            (
                os.environ.get("GODVIEW_ENV") in ("hermetic", "test")
                or self._transport is not None
            )
            and self._fixtures is not None
            and "live_iam_bindings" in self._fixtures
        ):
            return list(self._fixtures["live_iam_bindings"])
        bindings_list: list[dict[str, str]] = []
        try:
            async with self._http_client() as client:
                resp = await client.post(
                    f"https://cloudresourcemanager.googleapis.com/v1/projects/{self.project_id}:getIamPolicy",
                    json={},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for b in data.get("bindings", []):
                        role = b.get("role", "")
                        for m in b.get("members", []):
                            bindings_list.append({"resource": self.project_id, "role": role, "member": m})
                else:
                    self._errors["live_iam_bindings"] = f"CRM getIamPolicy HTTP {resp.status_code}"

                # Also inspect resource-level policies for discovered Cloud Run services
                try:
                    run_services = await self._fetch_live_resources()
                    for svc_name in run_services[0].keys():
                        full_svc = f"projects/{self.project_id}/locations/{self.region}/services/{svc_name}"
                        run_iam = await client.get(f"https://run.googleapis.com/v2/{full_svc}:getIamPolicy")
                        if run_iam.status_code == 200:
                            for b in run_iam.json().get("bindings", []):
                                role = b.get("role", "")
                                for m in b.get("members", []):
                                    bindings_list.append({"resource": svc_name, "role": role, "member": m})
                except Exception:
                    pass
        except Exception as exc:
            self._errors["live_iam_bindings"] = str(exc)
        return bindings_list

    async def _drift_diff(
        self,
        *,
        prefix: str = "platform",
        planned_operator_action: bool = False,
    ) -> dict[str, Any]:
        """Compare curated tfstate attributes against live GCP resources (TF Spec §5.1–§5.3)."""
        tf_read = await self._read_tfstate(prefix=prefix)
        tf_resources = tf_read["state"].get("resources", [])
        now_iso = datetime.now(tz=UTC).isoformat()
        findings: list[dict[str, Any]] = []

        live_run_by_name, live_vm_by_name, live_sql_by_name = await self._fetch_live_resources()
        desired_iam_bindings: set[tuple[str, str, str]] = set()

        for res_block in tf_resources:
            if res_block.get("mode") != "managed":
                continue
            r_type = str(res_block.get("type", ""))
            r_name = str(res_block.get("name", ""))
            module_prefix = str(res_block.get("module", "")).strip()
            address = (
                f"{module_prefix}.{r_type}.{r_name}" if module_prefix else f"{r_type}.{r_name}"
            )

            for inst in res_block.get("instances", []):
                attrs = inst.get("attributes", {})

                # Collect desired IAM bindings BEFORE label filtering because google_*_iam_member
                # resources do not have a `labels` attribute in the Terraform provider schema.
                if r_type.endswith(("_iam_member", "_iam_binding")):
                    res_target = str(
                        attrs.get("name")
                        or attrs.get("service")
                        or attrs.get("bucket")
                        or attrs.get("project")
                        or ""
                    ).rsplit("/", 1)[-1]
                    role = str(attrs.get("role", ""))
                    if r_type.endswith("_iam_member"):
                        member = str(attrs.get("member", ""))
                        if res_target and role and member:
                            desired_iam_bindings.add((res_target, role, member))
                    else:
                        for member in attrs.get("members") or []:
                            if res_target and role and member:
                                desired_iam_bindings.add((res_target, role, str(member)))
                    continue

                labels = attrs.get("labels") or (attrs.get("settings") or [{}])[0].get(
                    "user_labels", {}
                )
                res_identifier = str(attrs.get("name", r_name))
                if labels.get(MANAGED_LABEL_KEY) != MANAGED_LABEL_VALUE:
                    continue
                if is_resource_excluded(res_identifier, labels):
                    continue

                tf_module = str(labels.get("tf_module", module_prefix or "root"))

                if r_type == "google_cloud_run_v2_service":
                    svc_name = str(attrs.get("name", ""))
                    live_svc = live_run_by_name.get(svc_name)
                    if not live_svc:
                        continue
                    live_labels = live_svc.get("labels") or {}
                    if is_resource_excluded(svc_name, live_labels):
                        continue
                    desired_tpl = (attrs.get("template") or [{}])[0]
                    desired_scaling = (desired_tpl.get("scaling") or [{}])[0]
                    desired_container = (desired_tpl.get("containers") or [{}])[0]
                    desired_limits = (desired_container.get("resources") or [{}])[0].get(
                        "limits", {}
                    )

                    live_tpl = live_svc.get("template", {})
                    live_scaling = live_tpl.get("scaling", {})
                    live_container = (live_tpl.get("containers") or [{}])[0]
                    live_limits = live_container.get("resources", {}).get("limits", {})

                    checks = [
                        (
                            "template.scaling.min_instance_count",
                            desired_scaling.get("min_instance_count"),
                            live_scaling.get("minInstanceCount"),
                        ),
                        (
                            "template.scaling.max_instance_count",
                            desired_scaling.get("max_instance_count"),
                            live_scaling.get("maxInstanceCount"),
                        ),
                        (
                            "template.containers[0].image",
                            desired_container.get("image"),
                            live_container.get("image"),
                        ),
                        (
                            "template.containers[0].resources.limits.cpu",
                            desired_limits.get("cpu"),
                            live_limits.get("cpu"),
                        ),
                        (
                            "template.containers[0].resources.limits.memory",
                            desired_limits.get("memory"),
                            live_limits.get("memory"),
                        ),
                        (
                            "template.service_account",
                            desired_tpl.get("service_account"),
                            live_tpl.get("serviceAccount"),
                        ),
                        (
                            "ingress",
                            attrs.get("ingress"),
                            live_svc.get("ingress"),
                        ),
                        (
                            "template.max_instance_request_concurrency",
                            desired_tpl.get("max_instance_request_concurrency"),
                            live_tpl.get("maxInstanceRequestConcurrency"),
                        ),
                    ]
                    for attr_path, desired_val, live_val in checks:
                        if (
                            desired_val is not None
                            and live_val is not None
                            and desired_val != live_val
                        ):
                            findings.append(
                                {
                                    "address": address,
                                    "resource_name": svc_name,
                                    "tf_module": tf_module,
                                    "attribute": attr_path,
                                    "desired": desired_val,
                                    "live": live_val,
                                    "severity": "sev2",
                                    "first_seen": now_iso,
                                    "suspected_actor": self._lookup_suspected_actor(svc_name),
                                    "planned": bool(planned_operator_action),
                                }
                            )

                elif r_type == "google_compute_instance":
                    vm_name = str(attrs.get("name", ""))
                    live_vm = live_vm_by_name.get(vm_name)
                    if not live_vm:
                        continue
                    desired_mt = str(attrs.get("machine_type", ""))
                    live_mt = str(live_vm.get("machineType", "")).rsplit("/", 1)[-1]
                    if desired_mt and live_mt and desired_mt != live_mt:
                        findings.append(
                            {
                                "address": address,
                                "resource_name": vm_name,
                                "tf_module": tf_module,
                                "attribute": "machine_type",
                                "desired": desired_mt,
                                "live": live_mt,
                                "severity": "sev2",
                                "first_seen": now_iso,
                                "suspected_actor": self._lookup_suspected_actor(vm_name),
                                "planned": planned_operator_action,
                            }
                        )

                elif r_type == "google_sql_database_instance":
                    sql_name = str(attrs.get("name", ""))
                    live_sql = live_sql_by_name.get(sql_name)
                    if not live_sql:
                        continue
                    desired_settings = (attrs.get("settings") or [{}])[0]
                    live_settings = live_sql.get("settings", {})
                    if desired_settings.get("tier") != live_settings.get("tier"):
                        findings.append(
                            {
                                "address": address,
                                "resource_name": sql_name,
                                "tf_module": tf_module,
                                "attribute": "settings.tier",
                                "desired": desired_settings.get("tier"),
                                "live": live_settings.get("tier"),
                                "severity": "sev2",
                                "first_seen": now_iso,
                                "suspected_actor": self._lookup_suspected_actor(sql_name),
                                "planned": planned_operator_action,
                            }
                        )

        # Check live IAM bindings for any unauthorized member or Sev-1 allUsers/allAuthenticatedUsers
        live_iam_bindings = await self._fetch_live_iam_bindings()
        for live_binding in live_iam_bindings:
            res_target = str(live_binding.get("resource", "")).rsplit("/", 1)[-1]
            role = str(live_binding.get("role", ""))
            member = str(live_binding.get("member", ""))
            if is_resource_excluded(res_target):
                continue
            if (res_target, role, member) not in desired_iam_bindings:
                sev = "sev1" if member in ("allUsers", "allAuthenticatedUsers") else "sev2"
                findings.append(
                    {
                        "address": f"module.project_iam.iam_binding.{res_target}",
                        "resource_name": res_target,
                        "tf_module": "01-project-iam",
                        "attribute": f"iam.{role}",
                        "desired": "restricted_sa_only",
                        "live": member,
                        "severity": sev,
                        "first_seen": now_iso,
                        "suspected_actor": self._lookup_suspected_actor(res_target),
                        "planned": planned_operator_action,
                    }
                )

        # Emit NervousSystemEvent (godview.sentinel.breach) only for unplanned drift (TF Spec §5.2)
        cloudevents: list[dict[str, Any]] = []
        for idx, finding in enumerate(findings):
            if not finding["planned"]:
                ev = NervousSystemEvent(
                    type="godview.sentinel.breach",
                    source="//sovereign/sentinel-engine/drift-diff",
                    id=f"snt-drift_diff-{idx + 1}:{now_iso}",
                    time=now_iso,
                    subject=str(finding["address"]),
                    data={
                        "sentinel_id": "snt-drift_diff-platform",
                        "class": "drift_diff",
                        **finding,
                    },
                    godviewenvid=f"gcp-{self.project_id}",
                    godviewsynthetic=False,
                )
                cloudevents.append(ev.model_dump(mode="json"))

        return {
            "generation": tf_read["generation"],
            "findings": findings,
            "cloudevents": cloudevents,
        }

    async def _lookup_live_resource_labels(
        self, resource_ref: str, region: str | None = None
    ) -> dict[str, str] | None:
        """Look up the live labels of a resource over `httpx` in target region."""
        target_reg = region or self.region
        live_run, live_vm, live_sql = await self._fetch_live_resources(region=target_reg)
        short = resource_ref.rsplit("/", 1)[-1].rsplit(".", 1)[-1]
        for candidate in (resource_ref, short):
            if candidate in live_run:
                return dict(live_run[candidate].get("labels") or {})
            if candidate in live_vm:
                return dict(live_vm[candidate].get("labels") or {})
            if candidate in live_sql:
                return dict(live_sql[candidate].get("settings", {}).get("userLabels") or {})
        return None

    async def _restore_attribute(
        self,
        args: dict[str, Any],
        trust_decision: TrustDecision | None,
    ) -> dict[str, Any]:
        """Restore a single drifted attribute to its tfstate value via live API PATCH (L3, godview-act-sa)."""
        td = assert_valid_mutating_trust("tf.restore_attribute", trust_decision)
        address = str(args.get("address", ""))
        resource_name = str(args.get("resource_name") or address.rsplit(".", 1)[-1])
        attribute = str(args.get("attribute", ""))
        desired = args.get("desired")
        previous_live = args.get("live")
        caller_labels = args.get("labels") if "labels" in args else None

        target_region = args.get("region")
        if not target_region and "/locations/" in resource_name:
            parts = resource_name.split("/locations/")[1].split("/")
            if len(parts) >= 2 and parts[0] != "-":
                target_region = parts[0]
        if not target_region:
            target_region = self.region

        live_labels = await self._lookup_live_resource_labels(resource_name, region=target_region)
        assert_managed_resource(caller_labels, address, live_labels=live_labels)
        if resource_name != address:
            assert_managed_resource(caller_labels, resource_name, live_labels=live_labels)

        addr_parts = address.split(".")
        resource_type = addr_parts[-2] if len(addr_parts) >= 2 else address
        if resource_type != "google_cloud_run_v2_service":
            raise NotImplementedError(
                f"tf.restore_attribute fast-restore currently supports google_cloud_run_v2_service resources (got '{address}'). "
                "Use remediation orchestrator for infrastructure-level mutations."
            )
        if "[" in attribute or "]" in attribute:
            raise ValueError(
                f"Invalid attribute path '{attribute}': array indexing is not supported by updateMask"
            )

        if not td.sa_to_impersonate:
            raise TrustViolation("tf.restore_attribute requires an authorized sa_to_impersonate")

        # Impersonate act-sa strictly; REFUSE if impersonation fails
        async with self._http_client() as client:
            act_token = await _impersonate_act_sa_async(
                client, self.project_id, td.sa_to_impersonate, self.bearer_token
            )
        sa_used = td.sa_to_impersonate
        headers = {
            "Authorization": f"Bearer {act_token}",
            "Accept": "application/json",
        }

        short_name = resource_name.rsplit("/", 1)[-1]
        patch_url = (
            f"https://run.googleapis.com/v2/projects/{self.project_id}/"
            f"locations/{target_region}/services/{short_name}"
        )

        # Convert snake_case attribute path to camelCase for Cloud Run v2
        camel_parts = [_snake_to_camel(p) for p in attribute.split(".")]
        camel_attribute = ".".join(camel_parts)

        # Build nested dict from camelCase attribute path
        body_dict: dict[str, Any] = {}
        curr = body_dict
        for p in camel_parts[:-1]:
            curr[p] = {}
            curr = curr[p]
        curr[camel_parts[-1]] = desired

        async with self._http_client() as client:
            resp = await client.patch(
                patch_url,
                params={"updateMask": camel_attribute},
                json=body_dict,
                headers=headers,
            )
            resp.raise_for_status()
            api_response = resp.json()

        effective_labels = dict(live_labels if live_labels is not None else (caller_labels or {}))
        reversal_recipe = {
            "capability": "tf.restore_attribute",
            "args": {
                "address": address,
                "attribute": attribute,
                "desired": previous_live,
                "labels": effective_labels,
                "region": target_region,
            },
        }
        record = {
            "restored": True,
            "address": address,
            "attribute": attribute,
            "restored_value": desired,
            "previous_live_value": previous_live,
            "sa_used": sa_used,
            "api_response": api_response,
            "reversal_recipe": reversal_recipe,
            "restored_at": datetime.now(tz=UTC).isoformat(),
        }
        self._restored_log.append(record)
        return record
