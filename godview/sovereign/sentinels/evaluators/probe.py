"""Sentinel evaluator for synthetic GET-only HTTP probes (`probe`).

Performs safe GET-only probes of:
- Discovered public hosts and entrypoints
- OpenAPI routes flagged `x-godview-probe-safe` or read-only GET routes
- Static asset URLs referenced in API responses (e.g. fields ending in `_url`, `image_url`, `/assets/*`)

Breaches on:
- 4xx responses (e.g. 404 Not Found on asset files, 403 on unconfigured CDN bucket)
- 5xx responses (service unavailable, gateway timeout)
- High latency (> threshold_ms) or connection timeout
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from godview_shared.contracts import SentinelDefinition

from godview.sovereign.sentinels.evaluators.base import (
    BaseSentinelEvaluator,
    EvaluatorContext,
)
from godview.sovereign.sentinels.models import SentinelEvaluation


class ProbeEvaluator(BaseSentinelEvaluator):
    """Executes synthetic GET probes against endpoints and assets to verify availability."""

    async def evaluate(
        self,
        sentinel: SentinelDefinition,
        ctx: EvaluatorContext,
    ) -> SentinelEvaluation:
        spec = sentinel.check.spec or {}
        urls: list[str] = []
        if "target_url" in spec:
            urls.append(spec["target_url"])
        if "target_urls" in spec and isinstance(spec["target_urls"], list):
            urls.extend(spec["target_urls"])
        if not urls and sentinel.source_ref.startswith(("http://", "https://")):
            urls.append(sentinel.source_ref)

        timeout_s = float(spec.get("timeout_s", 5.0))
        max_latency_ms = float(spec.get("max_latency_ms", 5000.0))
        expected_status = spec.get("expected_status", [200, 204, 301, 302])

        failed_probes: list[dict[str, Any]] = []
        all_transcripts: list[dict[str, Any]] = []

        for target_url in urls:
            probe_result = await ctx.invoke_capability(
                "http.probe",
                {
                    "url": target_url,
                    "method": "GET",
                    "timeout_s": timeout_s,
                    "headers": {"X-Synthetic": "true", "User-Agent": "GodView-Sentinel/5.0"},
                },
            )
            if not isinstance(probe_result, dict):
                continue

            status_code = int(probe_result.get("status_code") or 0)
            latency_ms = float(probe_result.get("latency_ms") or 0.0)
            error = probe_result.get("error")

            transcript = {
                "url": target_url,
                "status_code": status_code,
                "latency_ms": latency_ms,
                "error": error,
                "response_headers": probe_result.get("headers", {}),
            }
            all_transcripts.append(transcript)

            is_failed = False
            fail_reason = ""

            if error:
                is_failed = True
                fail_reason = f"Network or transport error: {error}"
            elif status_code not in expected_status:
                is_failed = True
                fail_reason = f"Unexpected HTTP status {status_code} (expected {expected_status})"
            elif latency_ms > max_latency_ms:
                is_failed = True
                fail_reason = f"Probe latency {latency_ms:.1f}ms exceeded threshold {max_latency_ms:.1f}ms"

            if is_failed:
                transcript["fail_reason"] = fail_reason
                failed_probes.append(transcript)
            else:
                # T2 static asset discovery from API response bodies
                discovered_asset_urls: list[str] = []
                resp_body = probe_result.get("json") or probe_result.get("body")

                def _extract_asset_urls(data: Any, found: list[str], base_url: str) -> None:
                    if isinstance(data, dict):
                        for k, v in data.items():
                            if isinstance(v, str):
                                if str(k).endswith(("_url", "image_url", "asset_url")) or v.startswith("/assets/"):
                                    found.append(urllib.parse.urljoin(base_url, v))
                            elif isinstance(v, (dict, list)):
                                _extract_asset_urls(v, found, base_url)
                    elif isinstance(data, list):
                        for item in data:
                            _extract_asset_urls(item, found, base_url)

                if resp_body:
                    _extract_asset_urls(resp_body, discovered_asset_urls, target_url)

                for asset_url in discovered_asset_urls[:5]:
                    asset_res = await ctx.invoke_capability(
                        "http.probe",
                        {
                            "url": asset_url,
                            "method": "GET",
                            "timeout_s": timeout_s,
                            "headers": {"X-Synthetic": "true", "User-Agent": "GodView-Sentinel/5.0"},
                        },
                    )
                    if isinstance(asset_res, dict):
                        a_status = int(asset_res.get("status_code") or 0)
                        a_err = asset_res.get("error")
                        a_trans = {
                            "url": asset_url,
                            "status_code": a_status,
                            "latency_ms": float(asset_res.get("latency_ms") or 0.0),
                            "error": a_err,
                            "parent_url": target_url,
                            "is_discovered_asset": True,
                        }
                        all_transcripts.append(a_trans)
                        if a_err or a_status not in expected_status:
                            a_trans["fail_reason"] = (
                                a_err or f"Static asset returned HTTP {a_status} (expected {expected_status})"
                            )
                            failed_probes.append(a_trans)

        if not all_transcripts and urls:
            return SentinelEvaluation(
                observed=0.0,
                breached=False,
                details={"status": "unknown", "error": "No probe executions succeeded"},
            )

        if failed_probes:
            first_fail = failed_probes[0]
            status = first_fail.get("status_code", 0)
            is_asset = first_fail.get("is_discovered_asset", False) or "/assets/" in first_fail.get("url", "")
            if is_asset and status == 404:
                sig = "probe_asset_404"
                severity = "sev2"
            elif is_asset and status == 403:
                sig = "probe_asset_403_cdn"
                severity = "sev2"
            else:
                sig = f"probe_failed_http_{status}" if status else "probe_failed_connection"
                severity = "sev2" if (400 <= status < 500) else "sev1"

            return SentinelEvaluation(
                observed=float(len(failed_probes)),
                breached=True,
                evidence_samples=failed_probes[:5],
                signature=sig,
                resource_id=first_fail["url"],
                severity_override=severity,
                details={
                    "total_probed": len(all_transcripts),
                    "failed_count": len(failed_probes),
                    "failed_urls": [f["url"] for f in failed_probes],
                    "sample_transcript": first_fail,
                },
            )

        return SentinelEvaluation(
            observed=0.0,
            breached=False,
            details={"total_probed": len(urls), "transcripts": all_transcripts[:5]},
        )
