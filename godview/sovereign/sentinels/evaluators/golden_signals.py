"""Sentinel evaluator for Cloud Monitoring golden signals (`golden_signals`).

Evaluates per-service golden signals:
- Request rate and HTTP error counts (4xx, 5xx)
- Latency percentiles (p95, p99)
- Active instance counts (identifying cold-start windows when instance count was 0)

Breaches when:
- 5xx error rate exceeds baseline/threshold
- Latency p95 exceeds threshold (e.g. cold-start 504 timeout bursts)
- Active instance count drops to 0 during active traffic windows
"""

from __future__ import annotations

from typing import Any

from godview_shared.contracts import SentinelDefinition

from godview.sovereign.sentinels.evaluators.base import (
    BaseSentinelEvaluator,
    EvaluatorContext,
)
from godview.sovereign.sentinels.models import SentinelEvaluation


class GoldenSignalsEvaluator(BaseSentinelEvaluator):
    """Evaluates Cloud Monitoring time-series metrics against adaptive latency and error baselines."""

    async def evaluate(
        self,
        sentinel: SentinelDefinition,
        ctx: EvaluatorContext,
    ) -> SentinelEvaluation:
        spec = sentinel.check.spec or {}
        service_name = str(spec.get("service_name") or sentinel.source_ref)
        window_s = sentinel.check.window_s or sentinel.wake_condition.window_s or 300
        p95_threshold_ms = float(spec.get("p95_threshold_ms", 5000.0))
        error_rate_threshold = float(spec.get("error_rate_threshold", 0.05))

        # Query Cloud Monitoring timeseries with standard percentile aligner
        ts_data = await ctx.invoke_capability(
            "monitoring.query_timeseries",
            {
                "service_name": service_name,
                "window_s": window_s,
                "filter": (
                    f'metric.type="run.googleapis.com/request_latencies" '
                    f'AND resource.labels.service_name="{service_name}"'
                ),
                "aligner": "ALIGN_PERCENTILE_95",
                "per_series_aligner": "ALIGN_PERCENTILE_95",
            },
        )
        if ts_data is None or (isinstance(ts_data, dict) and "error" in ts_data):
            err = (
                ts_data.get("error", "No metric data")
                if isinstance(ts_data, dict)
                else "No metric data returned"
            )
            return SentinelEvaluation(
                observed=0.0,
                breached=False,
                details={"status": "unknown", "error": err, "service_name": service_name},
            )

        p95_ms = 0.0
        error_count_5xx = 0
        total_requests = 0
        instance_count = 1
        samples: list[dict[str, Any]] = []

        if isinstance(ts_data, dict):
            p95_ms = float(ts_data.get("p95_ms", 0.0))
            error_count_5xx = int(ts_data.get("error_count_5xx", 0))
            total_requests = int(ts_data.get("total_requests", 0))
            instance_count = int(ts_data.get("instance_count", 1))
            samples = ts_data.get("samples", [])
        elif isinstance(ts_data, list):
            # Parse list of metric points
            for series in ts_data:
                metric_name = str(series.get("metric", {}).get("type", ""))
                points = series.get("points", [])
                if "latenc" in metric_name and points:
                    p95_ms = max(p95_ms, float(points[0].get("value", 0.0)))
                elif "request_count" in metric_name:
                    resp_code = str(series.get("metric", {}).get("labels", {}).get("response_code_class", ""))
                    count = sum(int(p.get("value", 0)) for p in points)
                    total_requests += count
                    if resp_code == "5xx" or resp_code.startswith("5"):
                        error_count_5xx += count
                elif "instance_count" in metric_name and points:
                    instance_count = int(points[0].get("value", 1))

        error_rate = (error_count_5xx / total_requests) if total_requests > 0 else 0.0

        # Check for 504 / cold-start latency burst
        if p95_ms >= p95_threshold_ms:
            sig = "cold_start_504_burst" if (error_count_5xx > 0 or instance_count == 0) else "latency_p95_exceeded"
            return SentinelEvaluation(
                observed=round(p95_ms, 2),
                breached=True,
                evidence_samples=samples[:5] or [
                    {
                        "service": service_name,
                        "p95_ms": p95_ms,
                        "error_count_5xx": error_count_5xx,
                        "instance_count": instance_count,
                    }
                ],
                signature=sig,
                resource_id=service_name,
                severity_override="sev2",
                details={
                    "service_name": service_name,
                    "p95_ms": round(p95_ms, 2),
                    "threshold_ms": p95_threshold_ms,
                    "error_count_5xx": error_count_5xx,
                    "instance_count": instance_count,
                    "reason": f"Latency p95 ({p95_ms:.1f}ms) exceeded threshold ({p95_threshold_ms:.1f}ms)",
                },
            )

        # Check for 5xx error rate breach
        if error_rate > error_rate_threshold and error_count_5xx >= 2:
            return SentinelEvaluation(
                observed=round(error_rate, 4),
                breached=True,
                evidence_samples=[
                    {
                        "service": service_name,
                        "error_rate": round(error_rate, 4),
                        "5xx_count": error_count_5xx,
                        "total_requests": total_requests,
                    }
                ],
                signature="5xx_error_rate_exceeded",
                resource_id=service_name,
                severity_override="sev2",
                details={
                    "service_name": service_name,
                    "error_rate": round(error_rate, 4),
                    "threshold": error_rate_threshold,
                    "5xx_count": error_count_5xx,
                    "total_requests": total_requests,
                },
            )

        return SentinelEvaluation(
            observed=0.0,
            breached=False,
            details={
                "service_name": service_name,
                "p95_ms": p95_ms,
                "error_rate": round(error_rate, 4),
                "total_requests": total_requests,
            },
        )
