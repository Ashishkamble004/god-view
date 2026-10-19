"""Universal sentinel template pack and automated registration.

Auto-compiles and registers sentinel definitions per discovered resource and edge
during perception cycles without tenant-specific knowledge.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from datetime import UTC, datetime
from typing import Any

from godview_shared.contracts import (
    SentinelBaseline,
    SentinelDefinition,
    SentinelState,
    SentinelWakeCondition,
)


def _agent_id_for_service(service_name: str, kind: str = "cloud_run", env_id: str = "env") -> str:
    """Generate canonical target agent ID without hardcoding domain roles."""
    clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", service_name)
    if kind in ("lb_frontend", "backend_service", "security_policy", "platform"):
        return f"platform__{env_id}"
    return f"application__{clean_name}"


def build_lb_requests_sentinel(
    resource_id: str,
    *,
    host: str | None = None,
    path_prefix: str | None = None,
    security_policy: str | None = None,
    error_ratio_threshold: float = 0.05,
    target_agent_id: str = "platform__env",
    state: SentinelState = "enforced",
    compiled_by: str = "sentinels_pack",
) -> SentinelDefinition:
    """Build an `lb_requests` sentinel for an edge or host+path route."""
    clean_id = re.sub(r"[^a-zA-Z0-9_-]", "_", resource_id)
    sentinel_id = f"snt-lb-{clean_id}"
    now_iso = datetime.now(tz=UTC).isoformat()

    spec: dict[str, Any] = {
        "host": host,
        "path_prefix": path_prefix,
        "error_ratio_threshold": error_ratio_threshold,
    }
    if security_policy:
        spec["security_policy"] = security_policy

    return SentinelDefinition.model_validate(
        {
            "sentinel_id": sentinel_id,
            "class": "lb_requests",
            "source_ref": resource_id,
            "check": {
                "dialect": "python",
                "statement": f"Evaluate Cloud Logging LB requests for {resource_id}",
                "spec": spec,
                "window_s": 300,
            },
            "cadence_s": 60,
            "baseline": SentinelBaseline(model="adaptive", expected=0.0, stddev=0.1),
            "wake_condition": SentinelWakeCondition(
                operator=">=",
                threshold=1.0,
                consecutive=1,
                window_s=300,
                target_agent_id=target_agent_id,
            ),
            "target_agent_id": target_agent_id,
            "state": state,
            "compiled_by": compiled_by,
            "compiled_at": now_iso,
        }
    )


def build_posture_sentinel(
    resource_id: str,
    *,
    resource_kind: str = "cloud_run",
    baseline: dict[str, Any] | None = None,
    target_agent_id: str | None = None,
    state: SentinelState = "enforced",
    compiled_by: str = "sentinels_pack",
) -> SentinelDefinition:
    """Build a `posture` drift sentinel for a compute or edge resource."""
    clean_id = re.sub(r"[^a-zA-Z0-9_-]", "_", resource_id)
    sentinel_id = f"snt-posture-{clean_id}"
    agent_id = target_agent_id or _agent_id_for_service(resource_id, resource_kind)
    now_iso = datetime.now(tz=UTC).isoformat()

    # Do not impose opinionated defaults; allow tfstate or first-seen to establish baseline
    # so public services and backends don't trigger false positives
    default_baseline: dict[str, Any] = {}
    effective_baseline = {**default_baseline, **(baseline or {})}

    return SentinelDefinition.model_validate(
        {
            "sentinel_id": sentinel_id,
            "class": "posture",
            "source_ref": resource_id,
            "check": {
                "dialect": "python",
                "statement": f"Verify security posture and drift for {resource_id}",
                "spec": {
                    "resource_id": resource_id,
                    "resource_kind": resource_kind,
                    "baseline": effective_baseline,
                },
                "window_s": 300,
            },
            "cadence_s": 10,
            "baseline": SentinelBaseline(model="iac_baseline", expected=0.0, stddev=0.0),
            "wake_condition": SentinelWakeCondition(
                operator=">=",
                threshold=1.0,
                consecutive=1,
                window_s=300,
                target_agent_id=agent_id,
            ),
            "target_agent_id": agent_id,
            "state": state,
            "compiled_by": compiled_by,
            "compiled_at": now_iso,
        }
    )


def build_probe_sentinel(
    target_url: str,
    *,
    probe_name: str | None = None,
    expected_status: list[int] | None = None,
    target_agent_id: str = "platform__env",
    state: SentinelState = "enforced",
    compiled_by: str = "sentinels_pack",
) -> SentinelDefinition:
    """Build a synthetic `probe` sentinel for an endpoint or asset URL."""
    name = probe_name or re.sub(r"[^a-zA-Z0-9_-]", "_", target_url)
    sentinel_id = f"snt-probe-{name[:50]}"
    now_iso = datetime.now(tz=UTC).isoformat()

    return SentinelDefinition.model_validate(
        {
            "sentinel_id": sentinel_id,
            "class": "probe",
            "source_ref": target_url,
            "check": {
                "dialect": "python",
                "statement": f"Synthetic GET probe on {target_url}",
                "spec": {
                    "target_url": target_url,
                    "expected_status": expected_status or [200, 204, 301, 302],
                    "timeout_s": 5.0,
                    "max_latency_ms": 5000.0,
                },
                "window_s": 300,
            },
            "cadence_s": 10,
            "baseline": SentinelBaseline(model="synthetic", expected=0.0, stddev=0.0),
            "wake_condition": SentinelWakeCondition(
                operator=">=",
                threshold=1.0,
                consecutive=1,
                window_s=300,
                target_agent_id=target_agent_id,
            ),
            "target_agent_id": target_agent_id,
            "state": state,
            "compiled_by": compiled_by,
            "compiled_at": now_iso,
        }
    )


def build_pubsub_health_sentinel(
    subscription_name: str,
    *,
    target_agent_id: str = "platform__env",
    state: SentinelState = "enforced",
    compiled_by: str = "sentinels_pack",
) -> SentinelDefinition:
    """Build a `pubsub_health` sentinel for a Pub/Sub subscription."""
    clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", subscription_name)
    sentinel_id = f"snt-pubsub-{clean_name}"
    now_iso = datetime.now(tz=UTC).isoformat()

    return SentinelDefinition.model_validate(
        {
            "sentinel_id": sentinel_id,
            "class": "pubsub_health",
            "source_ref": subscription_name,
            "check": {
                "dialect": "python",
                "statement": f"Monitor Pub/Sub subscription delivery and DLQ for {subscription_name}",
                "spec": {"subscription_name": subscription_name, "backlog_threshold": 50},
                "window_s": 300,
            },
            "cadence_s": 60,
            "baseline": SentinelBaseline(model="queue_health", expected=0.0, stddev=0.0),
            "wake_condition": SentinelWakeCondition(
                operator=">=",
                threshold=1.0,
                consecutive=1,
                window_s=300,
                target_agent_id=target_agent_id,
            ),
            "target_agent_id": target_agent_id,
            "state": state,
            "compiled_by": compiled_by,
            "compiled_at": now_iso,
        }
    )


def build_golden_signals_sentinel(
    service_name: str,
    *,
    p95_threshold_ms: float = 5000.0,
    error_rate_threshold: float = 0.05,
    target_agent_id: str | None = None,
    state: SentinelState = "enforced",
    compiled_by: str = "sentinels_pack",
) -> SentinelDefinition:
    """Build a `golden_signals` sentinel for a compute service."""
    clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", service_name)
    sentinel_id = f"snt-signals-{clean_name}"
    agent_id = target_agent_id or _agent_id_for_service(service_name)
    now_iso = datetime.now(tz=UTC).isoformat()

    return SentinelDefinition.model_validate(
        {
            "sentinel_id": sentinel_id,
            "class": "golden_signals",
            "source_ref": service_name,
            "check": {
                "dialect": "python",
                "statement": f"Cloud Monitoring golden signals for {service_name}",
                "spec": {
                    "service_name": service_name,
                    "p95_threshold_ms": p95_threshold_ms,
                    "error_rate_threshold": error_rate_threshold,
                },
                "window_s": 300,
            },
            "cadence_s": 60,
            "baseline": SentinelBaseline(model="adaptive_monitoring", expected=0.0, stddev=0.1),
            "wake_condition": SentinelWakeCondition(
                operator=">=",
                threshold=1.0,
                consecutive=2,
                window_s=300,
                target_agent_id=agent_id,
            ),
            "target_agent_id": agent_id,
            "state": state,
            "compiled_by": compiled_by,
            "compiled_at": now_iso,
        }
    )


def build_journey_conformance_sentinel(
    journey_name: str,
    *,
    target_agent_id: str = "business_line__inferred",
    state: SentinelState = "enforced",
    compiled_by: str = "sentinels_pack",
) -> SentinelDefinition:
    """Build a `journey_conformance` sentinel for an inferred business journey."""
    clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", journey_name)
    sentinel_id = f"snt-journey-{clean_name}"
    now_iso = datetime.now(tz=UTC).isoformat()

    return SentinelDefinition.model_validate(
        {
            "sentinel_id": sentinel_id,
            "class": "journey_conformance",
            "source_ref": journey_name,
            "check": {
                "dialect": "python",
                "statement": f"Journey conformance check for {journey_name}",
                "spec": {"journey_name": journey_name},
                "window_s": 300,
            },
            "cadence_s": 10,
            "baseline": SentinelBaseline(model="journey_dag", expected=0.0, stddev=0.0),
            "wake_condition": SentinelWakeCondition(
                operator=">=",
                threshold=1.0,
                consecutive=1,
                window_s=300,
                target_agent_id=target_agent_id,
            ),
            "target_agent_id": target_agent_id,
            "state": state,
            "compiled_by": compiled_by,
            "compiled_at": now_iso,
        }
    )


def build_kpi_changepoint_sentinel(
    kpi_name: str,
    *,
    threshold: float = 2.5,
    target_agent_id: str = "business_line__inferred",
    state: SentinelState = "enforced",
    compiled_by: str = "sentinels_pack",
) -> SentinelDefinition:
    """Build a `kpi_changepoint` sentinel for an inferred business KPI."""
    clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", kpi_name)
    sentinel_id = f"snt-kpi-{clean_name}"
    now_iso = datetime.now(tz=UTC).isoformat()

    return SentinelDefinition.model_validate(
        {
            "sentinel_id": sentinel_id,
            "class": "kpi_changepoint",
            "source_ref": kpi_name,
            "check": {
                "dialect": "python",
                "statement": f"Statistical changepoint / CUSUM on KPI {kpi_name}",
                "spec": {"kpi_name": kpi_name},
                "window_s": 300,
            },
            "cadence_s": 10,
            "baseline": SentinelBaseline(model="cusum_zscore", expected=0.0, stddev=1.0),
            "wake_condition": SentinelWakeCondition(
                operator=">=",
                threshold=threshold,
                consecutive=1,
                window_s=300,
                target_agent_id=target_agent_id,
            ),
            "target_agent_id": target_agent_id,
            "state": state,
            "compiled_by": compiled_by,
            "compiled_at": now_iso,
        }
    )


def build_invariant_sentinel(
    invariant_id: str,
    statement: str,
    *,
    target_agent_id: str = "business_line__inferred",
    state: SentinelState = "shadow",
    compiled_by: str = "sentinels_pack",
) -> SentinelDefinition:
    """Build an `invariant` sentinel from an inferred business invariant in shadow mode."""
    clean_id = re.sub(r"[^a-zA-Z0-9_-]", "_", invariant_id)
    sentinel_id = f"snt-inv-{clean_id}"
    now_iso = datetime.now(tz=UTC).isoformat()

    return SentinelDefinition.model_validate(
        {
            "sentinel_id": sentinel_id,
            "class": "invariant",
            "source_ref": invariant_id,
            "check": {
                "dialect": "bigquery",
                "statement": statement,
                "window_s": 300,
            },
            "cadence_s": 10,
            "baseline": SentinelBaseline(model="invariant_rule", expected=0.0, stddev=0.0),
            "wake_condition": SentinelWakeCondition(
                operator=">=",
                threshold=1.0,
                consecutive=1,
                window_s=300,
                target_agent_id=target_agent_id,
            ),
            "target_agent_id": target_agent_id,
            "state": state,
            "compiled_by": compiled_by,
            "compiled_at": now_iso,
        }
    )


def compile_universal_sentinels(
    services: list[dict[str, Any] | Any],
    edges: list[dict[str, Any] | Any] | None = None,
    business_contexts: dict[str, Any] | None = None,
    tfstate: dict[str, Any] | None = None,
    env_id: str = "env",
    env_mode: str | None = None,
) -> list[SentinelDefinition]:
    """Auto-compile universal sentinels for all discovered services, edges, and invariants."""
    compiled: list[SentinelDefinition] = []
    seen_ids: set[str] = set()

    detected_env_id = env_id if env_id != "env" else ""
    for svc in services:
        s_env = (
            svc.get("env_id")
            if isinstance(svc, dict)
            else getattr(svc, "env_id", None)
        )
        if s_env and s_env != "env":
            detected_env_id = str(s_env)
            break
        labels = svc.get("labels") if isinstance(svc, dict) else getattr(svc, "labels", None)
        if isinstance(labels, dict):
            if labels.get("env_id") and labels["env_id"] != "env":
                detected_env_id = str(labels["env_id"])
                break
            if labels.get("environment"):
                detected_env_id = str(labels["environment"])
                break

    if not detected_env_id:
        detected_env_id = os.environ.get("GODVIEW_ENV_ID") or "env"

    # Strictly gate local mode on GODVIEW_ENV or explicit env_mode parameter.
    # Never key on env_id substring (preserves strict SSRF posture in GCP mode).
    env_mode_val = env_mode or os.environ.get("GODVIEW_ENV", "")
    is_local = env_mode_val.lower() in ("local", "hermetic", "test")

    platform_agent_id = f"platform__{detected_env_id}"

    def add(snt: SentinelDefinition) -> None:
        if snt.sentinel_id not in seen_ids:
            seen_ids.add(snt.sentinel_id)
            compiled.append(snt)

    for svc in services:
        name = getattr(svc, "name", None) or (svc.get("name") if isinstance(svc, dict) else "")
        kind = getattr(svc, "kind", None) or (svc.get("kind") if isinstance(svc, dict) else "cloud_run")

        if not name:
            continue

        # 1. Posture sentinel for compute & edge resources
        if kind in ("cloud_run", "backend_service", "security_policy", "gke_workload"):
            add(
                build_posture_sentinel(
                    name,
                    resource_kind=kind,
                    target_agent_id=_agent_id_for_service(name, kind, detected_env_id),
                )
            )

        # 2. Golden signals sentinel for compute services
        if kind in ("cloud_run", "gke_workload", "gce_instance", "gce_vm"):
            add(
                build_golden_signals_sentinel(
                    name,
                    target_agent_id=_agent_id_for_service(name, kind, detected_env_id),
                )
            )

        # 3. Load balancer requests sentinel: compile ONE per security policy or public LB frontend/host (not per backend)
        if kind == "security_policy":
            add(
                build_lb_requests_sentinel(
                    name,
                    security_policy=name,
                    target_agent_id=platform_agent_id,
                )
            )
        elif kind == "lb_frontend":
            routes = svc.get("routes") if isinstance(svc, dict) else getattr(svc, "routes", None)
            hosts: set[str] = set()
            if isinstance(routes, list):
                for r in routes:
                    h = r.get("host") if isinstance(r, dict) else getattr(r, "host", None)
                    if h and not str(h).startswith("*"):
                        hosts.add(str(h))
            for attr in ("endpoint", "locator", "host", "hostname"):
                v = svc.get(attr) if isinstance(svc, dict) else getattr(svc, attr, None)
                if v and str(v).startswith(("http://", "https://")):
                    try:
                        parsed = urllib.parse.urlparse(str(v)).hostname
                        if parsed and not parsed.startswith("*"):
                            hosts.add(parsed)
                    except Exception:
                        pass
                elif v and isinstance(v, str) and "." in v and not v.startswith(("http", "projects/")):
                    hosts.add(v)

            valid_hosts = [
                h for h in sorted(hosts)
                if (is_local or not h.startswith(("10.", "172.", "192.168.", "127.", "0.")))
                and not h.endswith(".googleapis.com")
                and (is_local or not h.replace(".", "").isdigit())
            ]

            if valid_hosts:
                for h in valid_hosts:
                    sentinel_res_id = f"{name}_{h}"
                    add(build_lb_requests_sentinel(sentinel_res_id, host=h, target_agent_id=platform_agent_id))
            else:
                add(build_lb_requests_sentinel(name, target_agent_id=platform_agent_id))

        # 4. Probe sentinel for public endpoints and probe-safe entrypoints
        candidate_urls: list[str] = []
        is_public_entrypoint = (
            kind in ("lb_frontend", "api_gateway", "gateway")
            or getattr(svc, "tier", None) == "edge"
            or (isinstance(svc, dict) and svc.get("tier") == "edge")
            or getattr(svc, "is_public", False)
            or (isinstance(svc, dict) and svc.get("is_public", False))
        )

        def _is_safe_public_url(u: str, is_public: bool = is_public_entrypoint) -> bool:
            if not u or not isinstance(u, str):
                return False
            if not u.startswith(("http://", "https://")):
                return False
            try:
                parsed = urllib.parse.urlparse(u)
                h = (parsed.hostname or "").lower()
                if not h:
                    return False
                # Reject private and loopback IPs outside local/hermetic test mode
                if not is_local:
                    if (
                        h.startswith(("10.", "192.168.", "127.", "0.", "169.254."))
                        or (h.startswith("172.") and any(h.startswith(f"172.{i}.") for i in range(16, 32)))
                        or h == "localhost"
                    ):
                        return False
                # Reject cloud provider internal APIs and metadata
                if (
                    h.endswith(".googleapis.com")
                    or h == "googleapis.com"
                    or h.endswith(".internal")
                    or "metadata.google.internal" in h
                ):
                    return False
                # Do NOT probe internal-only .run.app URLs unless explicitly on a public entrypoint
                if ".run.app" in h and not is_public:
                    return False
                return True
            except Exception:
                return False

        # For public entrypoints (lb_frontend, api_gateway), probe their discovered public hosts
        if kind in ("lb_frontend", "api_gateway", "gateway"):
            fe_hosts: set[str] = set()
            raw_routes = svc.get("routes") if isinstance(svc, dict) else getattr(svc, "routes", None)
            if isinstance(raw_routes, list):
                for r in raw_routes:
                    h = r.get("host") if isinstance(r, dict) else getattr(r, "host", None)
                    if h and not str(h).startswith("*"):
                        fe_hosts.add(str(h))
            raw_ep = getattr(svc, "endpoint", None) or (svc.get("endpoint") if isinstance(svc, dict) else None)
            if raw_ep:
                try:
                    parsed_h = urllib.parse.urlparse(str(raw_ep)).hostname
                    if parsed_h and not parsed_h.startswith("*"):
                        fe_hosts.add(parsed_h)
                except Exception:
                    pass
            for h in sorted(fe_hosts):
                fe_url = f"https://{h}/"
                if _is_safe_public_url(fe_url):
                    candidate_urls.append(fe_url)

        raw_ep = getattr(svc, "endpoint", None) or (svc.get("endpoint") if isinstance(svc, dict) else None)
        if raw_ep and _is_safe_public_url(str(raw_ep)):
            candidate_urls.append(str(raw_ep))
        locator = getattr(svc, "locator", None) or (svc.get("locator") if isinstance(svc, dict) else None)
        if locator and _is_safe_public_url(str(locator)):
            candidate_urls.append(str(locator))

        if isinstance(svc, dict):
            raw_routes = svc.get("routes")
        else:
            raw_routes = getattr(svc, "routes", None)
        routes = raw_routes if isinstance(raw_routes, list) else []
        for r in routes:
            method = r.get("method") if isinstance(r, dict) else getattr(r, "method", "GET")
            method_upper = str(method or "GET").upper()
            is_probe_safe_flag = (
                (isinstance(r, dict) and (r.get("probe_safe") is True or r.get("x-godview-probe-safe") is True))
                or getattr(r, "probe_safe", False)
                or getattr(r, "x_godview_probe_safe", False)
            )
            # Only probe routes whose method is GET (or explicitly flagged probe-safe)
            if method_upper != "GET" and not is_probe_safe_flag:
                continue

            r_url = r.get("url") if isinstance(r, dict) else getattr(r, "url", None)
            r_host = r.get("host") if isinstance(r, dict) else getattr(r, "host", None)
            path = r.get("path") if isinstance(r, dict) else getattr(r, "path", None)

            if not r_url and r_host and path and not str(r_host).startswith("*"):
                r_url = f"https://{r_host}/{str(path).lstrip('/')}"
            if not r_url and path and is_public_entrypoint:
                base_ep = str(raw_ep or locator or "").strip()
                if not base_ep and fe_hosts:
                    base_ep = f"https://{sorted(fe_hosts)[0]}"
                if base_ep.startswith("http"):
                    r_url = f"{base_ep.rstrip('/')}/{str(path).lstrip('/')}"

            is_probe_safe = (
                is_probe_safe_flag
                or (r_url and not str(r_url).endswith(".run.app") and ".run.app" not in str(r_url))
            )
            if r_url and (is_probe_safe or is_public_entrypoint):
                if _is_safe_public_url(str(r_url)):
                    candidate_urls.append(str(r_url))

            is_asset_path = bool(
                re.search(
                    r"\.(?:png|jpg|jpeg|svg|webp|ico|gif|css|js)$",
                    str(path or ""),
                    re.IGNORECASE,
                )
            ) or str(path or "").startswith("/assets/")
            if path and is_asset_path:
                if raw_ep:
                    asset_url = f"{str(raw_ep).rstrip('/')}/{str(path).lstrip('/')}"
                    if _is_safe_public_url(asset_url):
                        candidate_urls.append(asset_url)
                if r_host and not str(r_host).startswith("*"):
                    host_asset_url = f"https://{r_host}/{str(path).lstrip('/')}"
                    if _is_safe_public_url(host_asset_url):
                        candidate_urls.append(host_asset_url)

        for target_url in candidate_urls:
            add(build_probe_sentinel(target_url, target_agent_id=platform_agent_id))

        # 5. PubSub health sentinel for subscriptions
        if kind == "pubsub_subscription":
            add(build_pubsub_health_sentinel(name, target_agent_id=platform_agent_id))

        # 6. Invariants compiled from BusinessContext
        biz_ctx = svc.get("business_context") if isinstance(svc, dict) else getattr(svc, "business_context", None)
        if biz_ctx:
            raw_invariants = (
                biz_ctx.get("invariants") if isinstance(biz_ctx, dict) else getattr(biz_ctx, "invariants", None)
            )
            invariants = raw_invariants if isinstance(raw_invariants, list) else []
            for i, inv in enumerate(invariants):
                stmt = inv.get("check") or inv.get("statement") if isinstance(inv, dict) else str(inv)
                inv_id = f"{name}_inv_{i+1}"
                if stmt:
                    add(build_invariant_sentinel(inv_id, stmt, state="shadow"))

            raw_journeys = (
                biz_ctx.get("journeys") if isinstance(biz_ctx, dict) else getattr(biz_ctx, "journeys", None)
            )
            journeys = raw_journeys if isinstance(raw_journeys, list) else []
            for j in journeys:
                j_name = j.get("name") if isinstance(j, dict) else str(j)
                if j_name:
                    add(build_journey_conformance_sentinel(j_name))

            raw_kpis = (
                biz_ctx.get("kpis") if isinstance(biz_ctx, dict) else getattr(biz_ctx, "kpis", None)
            )
            kpis = raw_kpis if isinstance(raw_kpis, list) else []
            for k in kpis:
                k_name = k.get("name") if isinstance(k, dict) else str(k)
                if k_name:
                    add(build_kpi_changepoint_sentinel(k_name))

    return compiled
