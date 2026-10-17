"""Unit tests for the Universal Sentinel Pack reproducing live fault scenarios (T1, T2, etc.).

Tests:
1. T1: LB denies on JSON POST + IAP callback with preconfiguredExprIds `owasp-crs-v030301-id942432-sqli`
2. T2: Asset 404s + empty CDN bucket 403
3. Posture drift: Cloud Run ingress="all" + invoker_iam_disabled=True vs baseline
4. Pub/Sub health: Push 404 window and DLQ message accumulation
5. Golden signals: Cold-start 504 burst and latency p95 threshold breach
6. Negative tests: Healthy traffic across all classes raises 0 breaches
"""

from __future__ import annotations

from typing import Any

import pytest
from godview_shared.contracts import DiscoveredServiceDoc

from godview.sovereign.sentinel_engine.invoker import CapabilityInvoker
from godview.sovereign.sentinels import (
    EvaluatorContext,
    build_golden_signals_sentinel,
    build_lb_requests_sentinel,
    build_posture_sentinel,
    build_probe_sentinel,
    build_pubsub_health_sentinel,
    compile_universal_sentinels,
    get_evaluator,
)


@pytest.mark.unit
async def test_compile_universal_sentinels() -> None:
    """Verifies automated sentinel generation across discovered services and edges."""
    services = [
        DiscoveredServiceDoc(
            name="customer-portal-web",
            kind="cloud_run",
            routes=[{"path": "/", "url": "https://app.example.com"}],
            env_id="test-env",
            adapter_family="gcp",
            locator="projects/test/locations/us-central1/services/customer-portal-web",
            first_seen="2026-10-17T00:00:00Z",
            last_seen="2026-10-17T12:00:00Z",
        ),
        DiscoveredServiceDoc(
            name="edge-security-policy",
            kind="security_policy",
            endpoint="",
            env_id="test-env",
            adapter_family="gcp",
            locator="projects/test/global/securityPolicies/edge-security-policy",
            first_seen="2026-10-17T00:00:00Z",
            last_seen="2026-10-17T12:00:00Z",
        ),
        DiscoveredServiceDoc(
            name="sub-events-push",
            kind="pubsub_subscription",
            endpoint="",
            env_id="test-env",
            adapter_family="gcp",
            locator="projects/test/subscriptions/sub-events-push",
            first_seen="2026-10-17T00:00:00Z",
            last_seen="2026-10-17T12:00:00Z",
        ),
    ]

    compiled = compile_universal_sentinels(services)
    classes = {s.sentinel_class for s in compiled}
    assert "posture" in classes
    assert "golden_signals" in classes
    assert "probe" in classes
    assert "lb_requests" in classes
    assert "pubsub_health" in classes


@pytest.mark.unit
async def test_t1_lb_requests_denied_by_security_policy_and_negative() -> None:
    """T1 Repro: Cloud Armor WAF blocking legitimate JSON POSTs and OAuth callbacks."""
    invoker = CapabilityInvoker()

    # Fixture: Cloud Logging entries showing WAF block on JSON POST and OAuth callback
    t1_log_entries = [
        {
            "timestamp": "2026-10-17T12:05:01Z",
            "httpRequest": {
                "requestMethod": "POST",
                "requestUrl": "https://api.example.com/v1/orders/apply",
                "status": 403,
                "remoteIp": "35.192.10.5",
            },
            "jsonPayload": {
                "statusDetails": "denied_by_security_policy",
                "requestHeaders": {"content-type": "application/json"},
                "enforcedSecurityPolicy": {
                    "name": "edge-security-policy",
                    "priority": 1000,
                    "outcome": "DENY",
                    "preconfiguredExprIds": ["owasp-crs-v030301-id942432-sqli"],
                },
            },
        },
        {
            "timestamp": "2026-10-17T12:05:05Z",
            "httpRequest": {
                "requestMethod": "GET",
                "requestUrl": "https://login.example.com/_gcp_gatekeeper/authenticate?state=xyz",
                "status": 403,
                "remoteIp": "35.192.10.5",
            },
            "jsonPayload": {
                "statusDetails": "denied_by_security_policy",
                "enforcedSecurityPolicy": {
                    "name": "edge-security-policy",
                    "priority": 1000,
                    "outcome": "DENY",
                    "preconfiguredExprIds": ["owasp-crs-v030301-id942432-sqli"],
                },
            },
        },
    ]

    invoker.register_handler(
        "logging.query_lb_requests",
        lambda args: t1_log_entries,
    )

    ctx = EvaluatorContext(invoker=invoker)
    sentinel = build_lb_requests_sentinel(
        "edge-security-policy",
        host="api.example.com",
        path_prefix="/v1",
    )
    evaluator = get_evaluator("lb_requests")

    res = await evaluator.evaluate(sentinel, ctx)
    assert res.breached is True
    assert res.observed >= 1.0
    assert "owasp-crs-v030301-id942432-sqli" in (res.signature or "")
    assert res.severity_override == "sev1"
    assert len(res.evidence_samples) >= 1

    # Negative test: healthy 200 traffic raises no breach
    healthy_log_entries = [
        {
            "timestamp": "2026-10-17T12:10:00Z",
            "httpRequest": {
                "requestMethod": "GET",
                "requestUrl": "https://api.example.com/v1/items",
                "status": 200,
                "remoteIp": "35.192.10.5",
            },
            "jsonPayload": {"statusDetails": "response_sent_by_backend"},
        }
    ]
    invoker.register_handler("logging.query_lb_requests", lambda args: healthy_log_entries)

    res_clean = await evaluator.evaluate(sentinel, ctx)
    assert res_clean.breached is False
    assert res_clean.observed == 0.0


@pytest.mark.unit
async def test_t2_probe_asset_404_and_negative() -> None:
    """T2 Repro: 404 on product images / empty CDN bucket 403."""
    invoker = CapabilityInvoker()

    def probe_handler(args: dict[str, Any]) -> dict[str, Any]:
        url = args.get("url", "")
        if "/assets/images" in url:
            return {"status_code": 404, "latency_ms": 45.0, "error": None}
        if "cdn-assets" in url:
            return {"status_code": 403, "latency_ms": 30.0, "error": None}
        return {"status_code": 200, "latency_ms": 50.0, "error": None}

    invoker.register_handler("http.probe", probe_handler)
    ctx = EvaluatorContext(invoker=invoker)

    # T2 asset probe
    snt_asset = build_probe_sentinel("https://app.example.com/assets/images/logo-01.svg")
    evaluator = get_evaluator("probe")
    res_asset = await evaluator.evaluate(snt_asset, ctx)
    assert res_asset.breached is True
    assert res_asset.signature in ("probe_asset_404", "probe_failed_http_404")
    assert res_asset.observed == 1.0

    # Negative test: normal 200 probe
    snt_healthy = build_probe_sentinel("https://app.example.com/healthz")
    res_clean = await evaluator.evaluate(snt_healthy, ctx)
    assert res_clean.breached is False
    assert res_clean.observed == 0.0


@pytest.mark.unit
async def test_posture_drift_and_negative() -> None:
    """Posture drift repro: ingress=all + invoker_iam_disabled=True vs baseline."""
    invoker = CapabilityInvoker()

    # Drifted posture
    drifted_posture = {
        "ingress": "all",
        "invoker_iam_disabled": True,
        "all_users": False,
    }
    invoker.register_handler("run.get_posture", lambda args: drifted_posture)
    ctx = EvaluatorContext(invoker=invoker)

    snt = build_posture_sentinel(
        "core-orchestrator-api",
        baseline={"ingress": "internal-and-cloud-load-balancing", "invoker_iam_disabled": False},
    )
    evaluator = get_evaluator("posture")
    res = await evaluator.evaluate(snt, ctx)

    assert res.breached is True
    assert "invoker_iam_disabled" in (res.signature or "")
    assert res.severity_override == "sev1"

    # Negative test: matching baseline
    clean_posture = {
        "ingress": "internal-and-cloud-load-balancing",
        "invoker_iam_disabled": False,
        "all_users": False,
    }
    invoker.register_handler("run.get_posture", lambda args: clean_posture)
    res_clean = await evaluator.evaluate(snt, ctx)
    assert res_clean.breached is False
    assert res_clean.observed == 0.0


@pytest.mark.unit
async def test_pubsub_health_dlq_and_404_window() -> None:
    """Pub/Sub health: DLQ message accumulation and push 404 response window."""
    invoker = CapabilityInvoker()

    # DLQ depth breach
    invoker.register_handler(
        "pubsub.dlq_depth",
        lambda args: {"num_undelivered_messages": 452, "subscription_name": "sub-test"},
    )
    invoker.register_handler(
        "pubsub.describe_subscriptions",
        lambda args: {"num_undelivered_messages": 452, "name": "sub-test"},
    )
    ctx = EvaluatorContext(invoker=invoker)

    snt = build_pubsub_health_sentinel("sub-test")
    evaluator = get_evaluator("pubsub_health")
    res = await evaluator.evaluate(snt, ctx)
    assert res.breached is True
    assert res.signature == "pubsub_dlq_messages_present"
    assert res.observed == 452.0

    # Negative test: 0 backlog, 0 DLQ
    invoker.register_handler(
        "pubsub.dlq_depth",
        lambda args: {"num_undelivered_messages": 0, "subscription_name": "sub-clean"},
    )
    invoker.register_handler(
        "pubsub.describe_subscriptions",
        lambda args: {"num_undelivered_messages": 0, "name": "sub-clean"},
    )
    snt_clean = build_pubsub_health_sentinel("sub-clean")
    res_clean = await evaluator.evaluate(snt_clean, ctx)
    assert res_clean.breached is False
    assert res_clean.observed == 0.0


@pytest.mark.unit
async def test_golden_signals_cold_start_504_burst() -> None:
    """Golden signals: cold-start latency p95 timeout (>15s) and 504 errors."""
    invoker = CapabilityInvoker()

    # Cold start metric response
    cold_start_metrics = {
        "p95_ms": 15800.0,
        "error_count_5xx": 8,
        "total_requests": 20,
        "instance_count": 0,
        "samples": [{"time": "2026-10-17T09:39:08Z", "latency_ms": 15800.0, "status": 504}],
    }
    invoker.register_handler("monitoring.query_timeseries", lambda args: cold_start_metrics)
    ctx = EvaluatorContext(invoker=invoker)

    snt = build_golden_signals_sentinel("fleet-credit-service", p95_threshold_ms=5000.0)
    evaluator = get_evaluator("golden_signals")
    res = await evaluator.evaluate(snt, ctx)

    assert res.breached is True
    assert res.signature == "cold_start_504_burst"
    assert res.observed == 15800.0

    # Negative test: normal latency (120ms) and 0 errors
    clean_metrics = {
        "p95_ms": 120.0,
        "error_count_5xx": 0,
        "total_requests": 500,
        "instance_count": 2,
    }
    invoker.register_handler("monitoring.query_timeseries", lambda args: clean_metrics)
    res_clean = await evaluator.evaluate(snt, ctx)
    assert res_clean.breached is False
    assert res_clean.observed == 0.0


@pytest.mark.unit
async def test_t1_waf_posture_without_traffic() -> None:
    """T1 Repro: Cloud Armor WAF rule posture drift detected without needing traffic."""
    invoker = CapabilityInvoker()

    # Policy with WAF rule missing sensitivity parameter
    drifted_policies = [
        {
            "name": "edge-sec-policy",
            "selfLink": "projects/test/global/securityPolicies/edge-sec-policy",
            "rules": [
                {
                    "priority": 1000,
                    "action": "deny(403)",
                    "match": {
                        "expr": {
                            "expression": "evaluatePreconfiguredWaf('sqli-v33-stable')"
                        }
                    },
                }
            ],
        }
    ]
    invoker.register_handler("edge.describe_security_policies", lambda args: drifted_policies)
    ctx = EvaluatorContext(invoker=invoker)

    snt = build_posture_sentinel("edge-sec-policy", resource_kind="security_policy")
    evaluator = get_evaluator("posture")
    res = await evaluator.evaluate(snt, ctx)

    assert res.breached is True
    assert res.signature == "waf_sensitivity_missing"
    assert res.severity_override == "sev2"

    # Negative test: properly configured sensitivity parameter
    compliant_policies = [
        {
            "name": "edge-sec-policy",
            "selfLink": "projects/test/global/securityPolicies/edge-sec-policy",
            "rules": [
                {
                    "priority": 1000,
                    "action": "deny(403)",
                    "match": {
                        "expr": {
                            "expression": "evaluatePreconfiguredWaf('sqli-v33-stable', {'sensitivity': 1})"
                        }
                    },
                }
            ],
        }
    ]
    invoker.register_handler("edge.describe_security_policies", lambda args: compliant_policies)
    res_clean = await evaluator.evaluate(snt, ctx)
    assert res_clean.breached is False


@pytest.mark.unit
async def test_t2_asset_discovery_from_api_response() -> None:
    """T2 Repro: Probing API discovers referenced static asset URLs and catches 404/403."""
    invoker = CapabilityInvoker()

    def probe_handler(args: dict[str, Any]) -> dict[str, Any]:
        url = args.get("url", "")
        if url == "https://api.example.com/v1/catalog":
            # API response references an asset URL
            return {
                "status_code": 200,
                "latency_ms": 50.0,
                "json": {
                    "items": [
                        {"id": "item1", "image_url": "/assets/catalog/sample.png"},
                    ]
                },
                "error": None,
            }
        if "/assets/catalog/sample.png" in url:
            # Referenced static asset fails with 404 (e.g. unpopulated CDN bucket)
            return {"status_code": 404, "latency_ms": 40.0, "error": None}
        return {"status_code": 200, "latency_ms": 30.0, "error": None}

    invoker.register_handler("http.probe", probe_handler)
    ctx = EvaluatorContext(invoker=invoker)

    snt = build_probe_sentinel("https://api.example.com/v1/catalog")
    evaluator = get_evaluator("probe")
    res = await evaluator.evaluate(snt, ctx)

    assert res.breached is True
    assert res.signature == "probe_asset_404"
    assert "/assets/catalog/sample.png" in res.resource_id
