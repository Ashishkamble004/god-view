"""Sentinel evaluator for HTTP Load Balancer request logs (`lb_requests`).

Analyzes Cloud Logging entries for `resource.type="http_load_balancer"`.
Uses a zero-tenant-knowledge WAF classifier to distinguish benign traffic blocked
by Cloud Armor from scanner/attack traffic:
1. Side-effect-free canary probe (`GET /?a=b-c-d`) against discovered host
2. Signature family specificity (CRS low-specificity character anomaly vs pattern match)
3. Client session history (prior 2xx/3xx on the same host)
4. Contract & callback route shapes (/api/, /v1/, IdP callbacks)
5. Scanner heuristics (path breadth, miss ratio, scanner user-agents, traversal probes)
6. Separation of IAP 403s (outcome != DENY) from WAF policy denies
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

LOW_SPECIFICITY_CRS_PATTERNS = (
    "942430",
    "942431",
    "942432",
    "942421",
    "942420",
    "942460",
    "920",
    "sqli",
    "anomaly",
)

SCANNER_UA_SUBSTRINGS = ("sqlmap", "nikto", "masscan", "zgrab", "nmap", "gobuster")
TRAVERSAL_PATH_PATTERNS = ("../", "/.env", "/wp-", "/.git", "etc/passwd", "win.ini")


def classify_signature(sec_policy: dict[str, Any]) -> tuple[str, str, int]:
    """Classify preconfigured WAF rules into (canonical_family, specificity, priority)."""
    expr_ids = sec_policy.get("preconfiguredExprIds") or []
    priority = int(sec_policy.get("priority") or 1000)

    for eid in expr_ids:
        eid_str = str(eid).lower()
        if any(pat in eid_str for pat in LOW_SPECIFICITY_CRS_PATTERNS):
            return ("anomaly-sqli-pl4", "low", priority)
        if "941" in eid_str or "xss" in eid_str:
            return ("xss-pattern", "high", priority)
        if "930" in eid_str or "lfi" in eid_str or "traversal" in eid_str:
            return ("traversal-pattern", "high", priority)
        if "942" in eid_str:
            return ("sqli-pattern", "high", priority)

    return ("waf-rule", "low", priority)


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
        policy_filter = spec.get("security_policy")
        if not policy_filter and "policy" in sentinel.source_ref.lower():
            policy_filter = sentinel.source_ref
        ratio_threshold = spec.get("error_ratio_threshold", 0.05)
        min_denies = spec.get("min_denies", 1)

        # Build logging query filter
        filter_parts = ['resource.type="http_load_balancer"']
        if host_filter:
            filter_parts.append(f'httpRequest.requestUrl:"{host_filter}"')
        elif policy_filter:
            filter_parts.append(f'jsonPayload.enforcedSecurityPolicy.name="{policy_filter}"')
        filter_str = " AND ".join(filter_parts)

        # 1. Query general LB requests via capability (window_s lookback, no 1h stale block)
        raw_entries = await ctx.invoke_capability(
            "logging.query_lb_requests",
            {
                "filter": filter_str,
                "window_s": window_s,
                "page_size": spec.get("page_size", 100),
            },
        )
        if raw_entries is None or (isinstance(raw_entries, dict) and "error" in raw_entries):
            err = (
                raw_entries.get("error", "No LB request data")
                if isinstance(raw_entries, dict)
                else "No LB request data returned"
            )
            return SentinelEvaluation(
                observed=0.0,
                breached=False,
                details={"status": "unknown", "error": err, "filter": filter_str},
            )

        # 2. Targeted query for security policy denies or 5xx over the evaluation window
        deny_filter_parts = [
            'resource.type="http_load_balancer"',
            '(jsonPayload.statusDetails="denied_by_security_policy" OR httpRequest.status>=500)',
        ]
        if host_filter:
            deny_filter_parts.append(f'httpRequest.requestUrl:"{host_filter}"')
        elif policy_filter:
            deny_filter_parts.append(f'jsonPayload.enforcedSecurityPolicy.name="{policy_filter}"')
        deny_filter_str = " AND ".join(deny_filter_parts)

        deny_entries = await ctx.invoke_capability(
            "logging.query_lb_requests",
            {
                "filter": deny_filter_str,
                "window_s": window_s,
                "page_size": 100,
            },
        )

        combined_entries: list[dict[str, Any]] = (
            list(raw_entries) if isinstance(raw_entries, list) else []
        )
        if isinstance(deny_entries, list) and deny_entries:
            seen_hashes = {
                (
                    e.get("insertId")
                    or f"{e.get('timestamp')}_{e.get('httpRequest', {}).get('requestUrl')}"
                )
                for e in combined_entries
            }
            for de in deny_entries:
                deh = (
                    de.get("insertId")
                    or f"{de.get('timestamp')}_{de.get('httpRequest', {}).get('requestUrl')}"
                )
                if deh not in seen_hashes:
                    seen_hashes.add(deh)
                    combined_entries.append(de)

        total_requests = len(combined_entries)
        status_5xx_count = 0
        iap_or_backend_403_count = 0

        # Pass 1: Build client IP profiles (session history, path breadth, miss ratio)
        ip_statuses: dict[str, list[int]] = {}
        ip_paths: dict[str, set[str]] = {}
        ip_uas: dict[str, str] = {}

        for entry in combined_entries:
            http_req = entry.get("httpRequest") or {}
            json_payload = entry.get("jsonPayload") or {}
            status_code = int(http_req.get("status") or json_payload.get("status") or 0)
            url = str(http_req.get("requestUrl") or json_payload.get("url") or "")
            remote_ip = str(http_req.get("remoteIp") or json_payload.get("remoteIp") or "")
            ua = str(http_req.get("userAgent") or json_payload.get("userAgent") or "")

            if remote_ip and status_code > 0:
                ip_statuses.setdefault(remote_ip, []).append(status_code)
                try:
                    parsed_p = urlparse(url).path
                except Exception:
                    parsed_p = url
                ip_paths.setdefault(remote_ip, set()).add(parsed_p)
                if ua and remote_ip not in ip_uas:
                    ip_uas[remote_ip] = ua

        # Scanner profiling per IP
        scanner_ips: set[str] = set()
        prior_2xx_ips: set[str] = set()

        for ip, statuses in ip_statuses.items():
            if any(200 <= s < 400 for s in statuses):
                prior_2xx_ips.add(ip)

            paths = ip_paths.get(ip, set())
            miss_count = sum(1 for s in statuses if s in (403, 404))
            ua_lower = ip_uas.get(ip, "").lower()

            is_scanner = False
            # R6 scanner rules: scanner scans >= 20 distinct paths with high miss ratio,
            # or uses known scanner tools, or probes dotfiles/traversal
            if len(paths) >= 20 and (miss_count / max(1, len(statuses))) >= 0.5:
                is_scanner = True
            elif any(ua_pat in ua_lower for ua_pat in SCANNER_UA_SUBSTRINGS):
                is_scanner = True
            elif any(any(t_pat in p for t_pat in TRAVERSAL_PATH_PATTERNS) for p in paths):
                is_scanner = True

            # If client hits <= 3 paths without scanner UA/traversal, do not treat as scanner
            if is_scanner and len(paths) <= 3 and not any(ua_pat in ua_lower for ua_pat in SCANNER_UA_SUBSTRINGS):
                is_scanner = False

            if is_scanner:
                scanner_ips.add(ip)

        # 3. Optional side-effect-free canary probe (R1)
        canary_denied = False
        canary_transcript: dict[str, Any] | None = None
        target_canary_host = host_filter
        if not target_canary_host and combined_entries:
            for e in combined_entries:
                u = str(
                    (e.get("httpRequest") or {}).get("requestUrl")
                    or (e.get("jsonPayload") or {}).get("url")
                    or ""
                )
                if u:
                    try:
                        h = urlparse(u).hostname
                        if (
                            h
                            and not h.startswith(("10.", "172.", "192.168.", "127.", "0."))
                            and not h.replace(".", "").isdigit()
                        ):
                            target_canary_host = h
                            break
                    except Exception:
                        pass
            if not target_canary_host:
                first_url = str(
                    (combined_entries[0].get("httpRequest") or {}).get("requestUrl")
                    or (combined_entries[0].get("jsonPayload") or {}).get("url")
                    or ""
                )
                if first_url:
                    try:
                        target_canary_host = urlparse(first_url).hostname
                    except Exception:
                        pass

        if target_canary_host:
            canary_url = f"https://{target_canary_host}/?a=b-c-d"
            canary_probe = await ctx.invoke_capability(
                "http.probe",
                {
                    "url": canary_url,
                    "method": "GET",
                    "timeout_s": 3.0,
                    "headers": {"X-Synthetic": "true", "User-Agent": "GodView-Sentinel/5.0"},
                },
            )
            if isinstance(canary_probe, dict):
                c_status = int(canary_probe.get("status_code") or 0)
                c_err = canary_probe.get("error")
                if c_status == 403 or (isinstance(c_err, str) and "403" in c_err):
                    canary_denied = True
                canary_transcript = {
                    "url": canary_url,
                    "status_code": c_status,
                    "denied": canary_denied,
                }

        # Pass 2: Evaluate WAF denies with classifier
        waf_denies: list[dict[str, Any]] = []
        legit_samples: list[dict[str, Any]] = []
        attack_samples: list[dict[str, Any]] = []

        total_legit_contrib = 0.0
        total_attack_contrib = 0.0

        for entry in combined_entries:
            http_req = entry.get("httpRequest") or {}
            json_payload = entry.get("jsonPayload") or {}
            status_code = int(http_req.get("status") or json_payload.get("status") or 0)
            url = str(http_req.get("requestUrl") or json_payload.get("url") or "")
            method = str(http_req.get("requestMethod") or json_payload.get("method") or "GET").upper()
            remote_ip = str(http_req.get("remoteIp") or json_payload.get("remoteIp") or "")

            if path_prefix:
                try:
                    if not urlparse(url).path.startswith(path_prefix):
                        continue
                except Exception:
                    pass

            if 500 <= status_code < 600:
                status_5xx_count += 1

            status_details = str(
                json_payload.get("statusDetails") or entry.get("statusDetails") or ""
            )
            sec_policy = (
                json_payload.get("enforcedSecurityPolicy")
                or entry.get("enforcedSecurityPolicy")
                or {}
            )
            sec_outcome = str(sec_policy.get("outcome") or "").upper()

            # Rule R8: Non-WAF 403s (like IAP 403 with outcome ACCEPT) are not WAF denies
            is_waf_deny = (
                "denied_by_security_policy" in status_details or sec_outcome == "DENY"
            )
            if sec_outcome and sec_outcome != "DENY":
                is_waf_deny = False

            if status_code == 403 and not is_waf_deny:
                iap_or_backend_403_count += 1
                continue

            if not is_waf_deny:
                continue

            waf_denies.append(entry)
            canonical_family, specificity, priority = classify_signature(sec_policy)
            req_size = int(http_req.get("requestSize") or json_payload.get("requestSize") or 0)
            size_ok = req_size <= 65536

            # Check route shape (contract vs callback vs arbitrary)
            is_contract = bool(re.search(r"/(?:v\d+|api)/[a-zA-Z0-9_\-/]+", url))
            is_cb = (
                "/_gcp_gatekeeper/" in url
                or "/oauth2/callback" in url
                or "/signin-oidc" in url
                or bool(re.search(r"[?&]state=", url))
            )

            is_scanner = remote_ip in scanner_ips
            has_sess = remote_ip in prior_2xx_ips

            entry_record = {
                "url": url,
                "method": method,
                "status": status_code,
                "status_details": status_details,
                "enforced_security_policy": sec_policy,
                "remote_ip": remote_ip,
                "timestamp": entry.get("timestamp"),
                "canonical_family": canonical_family,
                "priority": priority,
            }

            if is_scanner:
                total_attack_contrib += 1.0
                attack_samples.append(entry_record)
            else:
                # Scoring contributions
                legit_item = 0.0
                attack_item = 0.0

                if canary_denied:
                    legit_item += 1.0

                if is_contract and size_ok and has_sess:
                    legit_item += 0.8
                elif is_contract and size_ok:
                    legit_item += 0.6

                if is_cb:
                    legit_item += 0.8

                if specificity == "low":
                    legit_item += 0.4
                elif specificity == "high":
                    attack_item += 0.5

                if req_size > 65536 or (method == "POST" and not is_contract and not is_cb):
                    attack_item += 0.4

                total_legit_contrib += legit_item
                total_attack_contrib += attack_item

                if legit_item >= attack_item:
                    legit_samples.append(entry_record)
                else:
                    attack_samples.append(entry_record)

        # Decision: Check WAF benign traffic block (T1 priority)
        d_count = len(waf_denies)
        if d_count > 0:
            legit_score = total_legit_contrib / max(1, d_count)
            attack_score = total_attack_contrib / max(1, d_count)

            first_deny = waf_denies[0]
            first_sec_policy = (
                (first_deny.get("jsonPayload") or {}).get("enforcedSecurityPolicy")
                or first_deny.get("enforcedSecurityPolicy")
                or {}
            )
            canonical_family, _, priority = classify_signature(first_sec_policy)
            expr_ids = first_sec_policy.get("preconfiguredExprIds") or []
            policy_name = first_sec_policy.get("name") or sentinel.source_ref

            sig = f"{policy_name}:{priority}:{canonical_family}"

            # Open incident if benign traffic is being blocked:
            # Canary denied (R1) OR (legit_score >= 0.6 and legit_score > attack_score)
            is_breached = (
                canary_denied or (legit_score >= 0.6 and legit_score > attack_score)
            ) and d_count >= min_denies

            if is_breached:
                return SentinelEvaluation(
                    observed=float(len(legit_samples) or d_count),
                    breached=True,
                    evidence_samples=(legit_samples or waf_denies)[:5],
                    signature=sig,
                    resource_id=policy_name,
                    severity_override="sev1",
                    details={
                        "total_requests": total_requests,
                        "waf_denies_count": d_count,
                        "legit_count": len(legit_samples),
                        "attack_count": len(attack_samples),
                        "legit_score": round(legit_score, 2),
                        "attack_score": round(attack_score, 2),
                        "policy_name": policy_name,
                        "priority": priority,
                        "canonical_family": canonical_family,
                        "preconfigured_expr_ids": expr_ids,
                        "canary_transcript": canary_transcript,
                        "sample_url": (legit_samples or waf_denies)[0].get("url"),
                        "sample_method": (legit_samples or waf_denies)[0].get("method"),
                    },
                )
            else:
                # Attack score dominated or below threshold: record informational posture note
                posture_note = (
                    f"WAF active: {d_count} denies, {len(scanner_ips)} scanner ips, "
                    f"families {[canonical_family]}"
                )
                return SentinelEvaluation(
                    observed=0.0,
                    breached=False,
                    details={
                        "total_requests": total_requests,
                        "waf_denies_count": d_count,
                        "legit_score": round(legit_score, 2),
                        "attack_score": round(attack_score, 2),
                        "posture_note": posture_note,
                        "iap_or_backend_403_count": iap_or_backend_403_count,
                    },
                )

        # Check for 5xx ratio breach
        error_ratio = (status_5xx_count / total_requests) if total_requests > 0 else 0.0
        if error_ratio > ratio_threshold and status_5xx_count >= 2:
            return SentinelEvaluation(
                observed=round(error_ratio, 4),
                breached=True,
                evidence_samples=[
                    e
                    for e in raw_entries
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
            details={
                "total_requests": total_requests,
                "5xx_count": status_5xx_count,
                "iap_or_backend_403_count": iap_or_backend_403_count,
            },
        )
