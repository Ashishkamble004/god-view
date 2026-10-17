"""Sentinel evaluator for Pub/Sub subscription and dead-letter queue health (`pubsub_health`).

Evaluates:
- Push subscription response status classes (2xx, 4xx, 5xx)
- Undelivered message backlog counts
- Dead-Letter Queue (DLQ) depth (including the observer's own DLQs)

Breaches when:
- Push endpoint returns 4xx errors (e.g. 404 route missing window)
- Push endpoint returns 5xx errors
- DLQ depth > 0 (messages being routed to dead letter)
- Undelivered message backlog exceeds threshold
"""

from __future__ import annotations

from typing import Any

from godview_shared.contracts import SentinelDefinition

from godview.sovereign.sentinels.evaluators.base import (
    BaseSentinelEvaluator,
    EvaluatorContext,
)
from godview.sovereign.sentinels.models import SentinelEvaluation


class PubSubHealthEvaluator(BaseSentinelEvaluator):
    """Monitors Pub/Sub subscription delivery health, push HTTP error classes, and DLQ depth."""

    async def evaluate(
        self,
        sentinel: SentinelDefinition,
        ctx: EvaluatorContext,
    ) -> SentinelEvaluation:
        spec = sentinel.check.spec or {}
        sub_name = str(spec.get("subscription_name") or sentinel.source_ref)
        backlog_threshold = int(spec.get("backlog_threshold", 50))

        # 1. Query DLQ depth (specifically from pubsub.dlq_depth capability)
        dlq_info = await ctx.invoke_capability(
            "pubsub.dlq_depth",
            {"subscription_name": sub_name},
        )
        dlq_depth = 0
        if isinstance(dlq_info, dict):
            dlq_depth = int(
                dlq_info.get("dlq_depth")
                or dlq_info.get("num_undelivered_messages")
                or dlq_info.get("dead_letter_depth")
                or 0
            )

        # 2. Query subscription details
        sub_desc = await ctx.invoke_capability(
            "pubsub.describe_subscriptions",
            {"subscription_name": sub_name},
        )
        if sub_desc is None or (isinstance(sub_desc, dict) and "error" in sub_desc):
            err = (
                sub_desc.get("error", "No subscription data")
                if isinstance(sub_desc, dict)
                else "No subscription data returned"
            )
            return SentinelEvaluation(
                observed=0.0,
                breached=False,
                details={"status": "unknown", "error": err, "subscription_name": sub_name},
            )

        target_sub: dict[str, Any] | None = None
        if isinstance(sub_desc, list):
            target_sub = next(
                (
                    s for s in sub_desc
                    if s.get("name") == sub_name
                    or s.get("id") == sub_name
                    or sub_name in str(s.get("name", ""))
                ),
                None,
            )
        elif isinstance(sub_desc, dict):
            target_sub = sub_desc

        if not target_sub:
            return SentinelEvaluation(
                observed=0.0,
                breached=False,
                details={
                    "status": "unknown",
                    "error": f"Subscription '{sub_name}' not found",
                    "subscription_name": sub_name,
                },
            )

        undelivered_count = int(target_sub.get("num_undelivered_messages") or 0)
        push_errors = target_sub.get("push_error_classes") or {}
        count_4xx = int(push_errors.get("4xx") or target_sub.get("push_4xx_count") or 0)
        count_5xx = int(push_errors.get("5xx") or target_sub.get("push_5xx_count") or 0)

        # 3. Query Cloud Monitoring timeseries for push_request_count grouped by response code/class
        ts_data = await ctx.invoke_capability(
            "monitoring.query_timeseries",
            {
                "filter": (
                    f'metric.type="pubsub.googleapis.com/subscription/push_request_count" '
                    f'AND resource.labels.subscription_id="{sub_name}"'
                ),
                "window_s": 300,
            },
        )
        if isinstance(ts_data, dict):
            count_4xx += int(ts_data.get("4xx_count") or ts_data.get("push_4xx_count") or 0)
            count_5xx += int(ts_data.get("5xx_count") or ts_data.get("push_5xx_count") or 0)
        elif isinstance(ts_data, list):
            for s in ts_data:
                labels = s.get("metric", {}).get("labels", {})
                resp_code = str(labels.get("response_code") or labels.get("response_class") or "")
                pts = s.get("points", [])
                total_pts = sum(int(p.get("value", 0)) for p in pts)
                if resp_code.startswith("4") or resp_code == "4xx":
                    count_4xx += total_pts
                elif resp_code.startswith("5") or resp_code == "5xx":
                    count_5xx += total_pts

        # Breach condition A: DLQ contains messages
        if dlq_depth > 0:
            return SentinelEvaluation(
                observed=float(dlq_depth),
                breached=True,
                evidence_samples=[
                    {
                        "subscription": sub_name,
                        "dlq_depth": dlq_depth,
                        "dead_letter_policy": target_sub.get("deadLetterPolicy"),
                    }
                ],
                signature="pubsub_dlq_messages_present",
                resource_id=sub_name,
                severity_override="sev2",
                details={
                    "subscription_name": sub_name,
                    "dlq_depth": dlq_depth,
                    "reason": f"Dead-letter queue contains {dlq_depth} unhandled messages",
                },
            )

        # Breach condition B: Push endpoint 4xx window (e.g. 404 endpoint not found)
        if count_4xx > 0:
            return SentinelEvaluation(
                observed=float(count_4xx),
                breached=True,
                evidence_samples=[
                    {
                        "subscription": sub_name,
                        "push_4xx_count": count_4xx,
                        "push_config": target_sub.get("pushConfig"),
                    }
                ],
                signature="pubsub_push_http_4xx",
                resource_id=sub_name,
                severity_override="sev2",
                details={
                    "subscription_name": sub_name,
                    "push_4xx_count": count_4xx,
                    "push_endpoint": (target_sub.get("pushConfig") or {}).get("pushEndpoint"),
                    "reason": f"Push subscription endpoint returning HTTP 4xx errors ({count_4xx} errors)",
                },
            )

        # Breach condition C: Push endpoint 5xx
        if count_5xx > 0:
            return SentinelEvaluation(
                observed=float(count_5xx),
                breached=True,
                evidence_samples=[
                    {
                        "subscription": sub_name,
                        "push_5xx_count": count_5xx,
                        "push_config": target_sub.get("pushConfig"),
                    }
                ],
                signature="pubsub_push_http_5xx",
                resource_id=sub_name,
                severity_override="sev2",
                details={
                    "subscription_name": sub_name,
                    "push_5xx_count": count_5xx,
                    "push_endpoint": (target_sub.get("pushConfig") or {}).get("pushEndpoint"),
                    "reason": f"Push subscription endpoint returning HTTP 5xx errors ({count_5xx} errors)",
                },
            )

        # Breach condition D: Undelivered backlog exceeds threshold
        if undelivered_count > backlog_threshold:
            return SentinelEvaluation(
                observed=float(undelivered_count),
                breached=True,
                evidence_samples=[
                    {
                        "subscription": sub_name,
                        "num_undelivered_messages": undelivered_count,
                        "threshold": backlog_threshold,
                    }
                ],
                signature="pubsub_backlog_exceeded",
                resource_id=sub_name,
                severity_override="sev3",
                details={
                    "subscription_name": sub_name,
                    "num_undelivered_messages": undelivered_count,
                    "threshold": backlog_threshold,
                },
            )

        return SentinelEvaluation(
            observed=float(undelivered_count),
            breached=False,
            details={
                "subscription_name": sub_name,
                "num_undelivered_messages": undelivered_count,
                "dlq_depth": dlq_depth,
            },
        )
