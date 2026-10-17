"""Universal sentinel template pack and automated registration.

Auto-compiles and registers sentinel definitions per discovered resource and edge
during perception cycles without tenant-specific knowledge.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from godview_shared.contracts import (
    SentinelBaseline,
    SentinelDefinition,
    SentinelState,
    SentinelWakeCondition,
)


def _agent_id_for_service(service_name: str, kind: str = "cloud_run") -> str:
    """Generate canonical target agent ID without hardcoding domain roles."""
    clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", service_name)
    if kind in ("lb_frontend", "backend_service", "security_policy", "platform"):
        return "platform__env"
    return f"application__{clean_name}"


def build_lb_requests_sentinel(
    resource_id: str,
    *,
    host: str | None = None,
    path_prefix: str | None = None,
    error_ratio_threshold: float = 0.05,
    target_agent_id: str = "platform__env",
    state: SentinelState = "enforced",
    compiled_by: str = "sentinels_pack",
) -> SentinelDefinition:
    """Build an `lb_requests` sentinel for an edge or host+path route."""
    clean_id = re.sub(r"[^a-zA-Z0-9_-]", "_", resource_id)
    sentinel_id = f"snt-lb-{clean_id}"
    now_iso = datetime.now(tz=UTC).isoformat()

    return SentinelDefinition.model_validate(
        {
            "sentinel_id": sentinel_id,
            "class": "lb_requests",
            "source_ref": resource_id,
            "check": {
                "dialect": "python",
                "statement": f"Evaluate Cloud Logging LB requests for {resource_id}",
                "spec": {
                    "host": host,
                    "path_prefix": path_prefix,
                    "error_ratio_threshold": error_ratio_threshold,
                },
                "window_s": 300,
            },
            "cadence_s": 10,
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

    default_baseline: dict[str, Any] = {}
    if resource_kind == "cloud_run":
        default_baseline = {
            "ingress": "internal-and-cloud-load-balancing",
            "invoker_iam_disabled": False,
            "all_users": False,
        }
    elif resource_kind in ("backend_service", "lb_backend"):
        default_baseline = {
            "iap_enabled": True,
            "require_security_policy": True,
        }

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
            "cadence_s": 10,
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
            "cadence_s": 10,
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
) -> list[SentinelDefinition]:
    """Auto-compile universal sentinels for all discovered services, edges, and invariants."""
    compiled: list[SentinelDefinition] = []
    seen_ids: set[str] = set()

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
            add(build_posture_sentinel(name, resource_kind=kind))

        # 2. Golden signals sentinel for compute services
        if kind in ("cloud_run", "gke_workload", "gce_instance", "gce_vm"):
            add(build_golden_signals_sentinel(name))

        # 3. Load balancer requests sentinel for edge & public entrypoints
        if kind in ("lb_frontend", "backend_service", "security_policy"):
            add(build_lb_requests_sentinel(name))

        # 4. Probe sentinel for public endpoints and entrypoints
        candidate_urls: list[str] = []
        raw_ep = getattr(svc, "endpoint", None) or (svc.get("endpoint") if isinstance(svc, dict) else None)
        if raw_ep:
            candidate_urls.append(str(raw_ep))
        locator = getattr(svc, "locator", None) or (svc.get("locator") if isinstance(svc, dict) else None)
        if locator and str(locator).startswith(("http://", "https://")):
            candidate_urls.append(str(locator))
        routes = getattr(svc, "routes", []) or (svc.get("routes") if isinstance(svc, dict) else [])
        for r in routes:
            r_url = r.get("url") if isinstance(r, dict) else getattr(r, "url", None)
            if r_url and str(r_url).startswith(("http://", "https://")):
                candidate_urls.append(str(r_url))

        for target_url in candidate_urls:
            add(build_probe_sentinel(target_url))

        # 5. PubSub health sentinel for subscriptions
        if kind == "pubsub_subscription":
            add(build_pubsub_health_sentinel(name))

        # 6. Invariants compiled from BusinessContext
        biz_ctx = getattr(svc, "business_context", None) or (
            svc.get("business_context") if isinstance(svc, dict) else None
        )
        if biz_ctx:
            invariants = getattr(biz_ctx, "invariants", []) or (
                biz_ctx.get("invariants", []) if isinstance(biz_ctx, dict) else []
            )
            for i, inv in enumerate(invariants):
                stmt = inv.get("check") or inv.get("statement") if isinstance(inv, dict) else str(inv)
                inv_id = f"{name}_inv_{i+1}"
                if stmt:
                    add(build_invariant_sentinel(inv_id, stmt, state="shadow"))

            journeys = getattr(biz_ctx, "journeys", []) or (
                biz_ctx.get("journeys", []) if isinstance(biz_ctx, dict) else []
            )
            for j in journeys:
                j_name = j.get("name") if isinstance(j, dict) else str(j)
                if j_name:
                    add(build_journey_conformance_sentinel(j_name))

            kpis = getattr(biz_ctx, "kpis", []) or (
                biz_ctx.get("kpis", []) if isinstance(biz_ctx, dict) else []
            )
            for k in kpis:
                k_name = k.get("name") if isinstance(k, dict) else str(k)
                if k_name:
                    add(build_kpi_changepoint_sentinel(k_name))

    return compiled
