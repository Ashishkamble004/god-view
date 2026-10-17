"""Sentinel evaluator for KPI changepoints and anomaly detection (`kpi_changepoint`).

Detects sudden shifts (CUSUM, z-score spikes) in rolling business KPIs over the ledger.
"""

from __future__ import annotations

from typing import Any

from godview_shared.contracts import SentinelDefinition

from godview.sovereign.sentinels.evaluators.base import (
    BaseSentinelEvaluator,
    EvaluatorContext,
)
from godview.sovereign.sentinels.models import SentinelEvaluation


class KpiChangepointEvaluator(BaseSentinelEvaluator):
    """Evaluates KPI time series for statistical changepoints and threshold anomalies."""

    async def evaluate(
        self,
        sentinel: SentinelDefinition,
        ctx: EvaluatorContext,
    ) -> SentinelEvaluation:
        kpi_name = sentinel.source_ref
        threshold = sentinel.wake_condition.threshold

        if ctx.bigquery is None:
            return SentinelEvaluation(
                observed=0.0,
                breached=False,
                details={"status": "unknown", "error": "No ledger configured"},
            )

        rows: list[dict[str, Any]] = []
        if hasattr(ctx.bigquery, "get_table"):
            all_rows = ctx.bigquery.get_table("kpi_rollups")
            rows = [r for r in all_rows if r.get("kpi") == kpi_name]

        if not rows:
            return SentinelEvaluation(observed=0.0, breached=False)

        latest_row = rows[-1]
        observed_val = float(latest_row.get("zscore") or latest_row.get("value") or 0.0)

        breached = False
        op = sentinel.wake_condition.operator
        if op == ">=":
            breached = observed_val >= threshold
        elif op == ">":
            breached = observed_val > threshold
        elif op == "<=":
            breached = observed_val <= threshold
        elif op == "<":
            breached = observed_val < threshold
        elif op == "==":
            breached = observed_val == threshold
        elif op == "!=":
            breached = observed_val != threshold

        if breached:
            sig = f"kpi_anomaly_{kpi_name}"
            return SentinelEvaluation(
                observed=observed_val,
                breached=True,
                evidence_samples=rows[-3:],
                signature=sig,
                resource_id=kpi_name,
                severity_override="sev2",
                details={
                    "kpi": kpi_name,
                    "observed_value": observed_val,
                    "threshold": threshold,
                    "operator": op,
                },
            )

        return SentinelEvaluation(
            observed=observed_val,
            breached=False,
            details={"kpi": kpi_name, "observed": observed_val},
        )
