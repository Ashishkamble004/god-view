"""Sentinel evaluator for HTTP Load Balancer request logs (`lb_requests`).

Analyzes Cloud Logging entries for `resource.type="http_load_balancer"`.
Detects:
1. Legitimate traffic blocked by Cloud Armor / Security Policy:
   - JSON POST/PUT/PATCH requests to API routes blocked by security policy
   - OAuth / Identity callbacks (e.g. `/_gcp_gatekeeper/*`, `?state=`) blocked
   - Interleaving success/deny patterns from same source IP
2. High 5xx ratio (> threshold) or 504 / 502 error bursts.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from godview_shared.contracts import SentinelDefinition

from godview.sovereign.sentinels.evaluators.base import (
    BaseSentinelEvaluator,
    EvaluatorContext,
)
from godview.sovereign.sentinels.models import SentinelEvaluation


class LbRequestsEvaluator(BaseSentinelEvaluator):
    """Evaluates load balancer request logs for security policy blocks and 5xx ratios."""

    async def evaluate(
        self,
        sentinel: SentinelDefinition,
        ctx: EvaluatorContext,
    ) -> SentinelEvaluation:
        spec = sentinel.check.spec or {}
        window_s = sentinel.check.window_s or sentinel.wake_condition.window_s or 300
        host_filter = spec.get("host")
        path_prefix = spec.get("path_prefix")
        ratio_threshold = spec.get("error_ratio_threshold", 0.05)

        # Build logging query filter
        filter_parts = ['resource.type="http_load_balancer"']
        if host_filter:
            filter_parts.append(f'httpRequest.requestUrl:"{host_filter}"')
        filter_str = " AND ".join(filter_parts)

        # Query LB logs via capability
        raw_entries = await ctx.invoke_capability(
            "logging.query_lb_requests",
            {
                "filter": filter_str,
                "window_s": window_s,
                "page_size": spec.get("page_size", 100),
            },
        )
        if not raw_entries or not isinstance(raw_entries, list):
            return SentinelEvaluation(observed=0.0, breached=False)

        total_requests = len(raw_entries)
        status_5xx_count = 0
        legitimate_blocked: list[dict[str, Any]] = []
        ip_outcomes: dict[str, list[int]] = {}

        for entry in raw_entries:
            http_req = entry.get("httpRequest") or {}
            json_payload = entry.get("jsonPayload") or {}
            status_code = int(http_req.get("status") or json_payload.get("status") or 0)
            url = str(http_req.get("requestUrl") or json_payload.get("url") or "")
            method = str(http_req.get("requestMethod") or json_payload.get("method") or "GET").upper()
            remote_ip = str(http_req.get("remoteIp") or json_payload.get("remoteIp") or "")

            if remote_ip and status_code > 0:
                ip_outcomes.setdefault(remote_ip, []).append(status_code)

            if path_prefix:
                parsed = urlparse(url)
                if not parsed.path.startswith(path_prefix):
                    continue

            if 500 <= status_code < 600:
                status_5xx_count += 1

            status_details = str(
                json_payload.get("statusDetails")
                or entry.get("statusDetails")
                or ""
            )
            sec_policy = (
                json_payload.get("enforcedSecurityPolicy")
                or entry.get("enforcedSecurityPolicy")
                or {}
            )
            sec_outcome = str(sec_policy.get("outcome") or "").upper()
            is_policy_deny = (
                "denied_by_security_policy" in status_details
                or sec_outcome == "DENY"
                or (status_code == 403 and bool(sec_policy))
            )

            if is_policy_deny:
                # Check if request appears to be legitimate
                is_legit = False
                headers = json_payload.get("requestHeaders") or entry.get("headers") or {}
                content_type = str(
                    headers.get("content-type")
                    or headers.get("Content-Type")
                    or json_payload.get("contentType")
                    or ""
                ).lower()

                # Rule A: JSON POST/PUT to API routes
                if method in ("POST", "PUT", "PATCH"):
                    if "application/json" in content_type or "/v1/" in url or "/api/" in url:
                        is_legit = True

                # Rule B: OAuth / identity callback routes
                if "/_gcp_gatekeeper/" in url or re.search(r"[?&]state=", url):
                    is_legit = True

                # Rule C: Interleaved success from same IP
                prior_codes = ip_outcomes.get(remote_ip, [])
                if any(200 <= c < 400 for c in prior_codes):
                    is_legit = True

                if is_legit:
                    legitimate_blocked.append(
                        {
                            "url": url,
                            "method": method,
                            "status": status_code,
                            "status_details": status_details,
                            "enforced_security_policy": sec_policy,
                            "remote_ip": remote_ip,
                            "timestamp": entry.get("timestamp"),
                        }
                    )

        # Check for legitimate blocked traffic breach (T1 priority)
        if legitimate_blocked:
            first_block = legitimate_blocked[0]
            sec_pol = first_block.get("enforced_security_policy") or {}
            expr_ids = sec_pol.get("preconfiguredExprIds") or []
            expr_sig = expr_ids[0] if expr_ids else "waf"
            policy_name = sec_pol.get("name") or sentinel.source_ref

            sig = f"denied_by_security_policy_{expr_sig}"
            return SentinelEvaluation(
                observed=float(len(legitimate_blocked)),
                breached=True,
                evidence_samples=legitimate_blocked[:5],
                signature=sig,
                resource_id=policy_name,
                severity_override="sev1",
                details={
                    "total_requests": total_requests,
                    "blocked_count": len(legitimate_blocked),
                    "policy_name": policy_name,
                    "preconfigured_expr_ids": expr_ids,
                    "sample_url": first_block.get("url"),
                    "sample_method": first_block.get("method"),
                },
            )

        # Check for 5xx ratio breach
        error_ratio = (status_5xx_count / total_requests) if total_requests > 0 else 0.0
        if error_ratio > ratio_threshold and status_5xx_count >= 2:
            return SentinelEvaluation(
                observed=round(error_ratio, 4),
                breached=True,
                evidence_samples=[
                    e for e in raw_entries
                    if 500 <= int((e.get("httpRequest") or {}).get("status") or 0) < 600
                ][:5],
                signature="5xx_ratio_exceeded",
                resource_id=sentinel.source_ref,
                severity_override="sev2",
                details={
                    "total_requests": total_requests,
                    "5xx_count": status_5xx_count,
                    "error_ratio": round(error_ratio, 4),
                    "threshold": ratio_threshold,
                },
            )

        return SentinelEvaluation(
            observed=0.0,
            breached=False,
            details={"total_requests": total_requests, "5xx_count": status_5xx_count},
        )
