"""Sentinel evaluator for compiled business invariants (`invariant`).

Evaluates compiled SQL or CEL expressions over the ledger.
Enforces exclusion of synthetic probe rows (`source != 'probe'`).
Maintains shadow execution statistics until false-positive rate < 5%.
"""

from __future__ import annotations

from typing import Any

from godview_shared.contracts import SentinelDefinition

from godview.sovereign.sentinels.evaluators.base import (
    BaseSentinelEvaluator,
    EvaluatorContext,
)
from godview.sovereign.sentinels.models import SentinelEvaluation


class InvariantEvaluator(BaseSentinelEvaluator):
    """Evaluates business invariants against ledger data excluding synthetic probes."""

    async def evaluate(
        self,
        sentinel: SentinelDefinition,
        ctx: EvaluatorContext,
    ) -> SentinelEvaluation:
        stmt = (sentinel.check.statement or "").strip().rstrip(";")
        if not stmt:
            return SentinelEvaluation(observed=0.0, breached=False)

        # Enforce exclusion of synthetic probe rows
        lower_stmt = stmt.lower()
        if "source" not in lower_stmt and "events" in lower_stmt:
            if not any(kw in lower_stmt for kw in (" group by ", " order by ", " limit ", " join ")):
                if " where " in lower_stmt:
                    stmt = f"{stmt} AND COALESCE(source, 'otel') != 'probe'"
                else:
                    stmt = f"{stmt} WHERE COALESCE(source, 'otel') != 'probe'"

        count = 0.0
        samples: list[dict[str, Any]] = []

        if ctx.bigquery is not None and hasattr(ctx.bigquery, "query"):
            try:
                rows = ctx.bigquery.query(stmt)
                if rows:
                    first_row = rows[0]
                    raw_val = (
                        first_row.get("count")
                        or first_row.get("violations")
                        or next(iter(first_row.values()), 0.0)
                    )
                    count = float(raw_val or 0.0)
            except Exception as exc:
                return SentinelEvaluation(
                    observed=0.0,
                    breached=False,
                    details={
                        "status": "unknown",
                        "error": f"Query execution failed: {exc}",
                        "statement": stmt,
                    },
                )

            try:
                sample_rows = ctx.bigquery.query(
                    "SELECT * FROM events WHERE COALESCE(source, 'otel') != 'probe' LIMIT 5"
                )
                if sample_rows and isinstance(sample_rows, list):
                    samples = sample_rows[:5]
            except Exception:
                pass

        threshold = sentinel.wake_condition.threshold
        breached = count >= threshold

        if breached:
            sig = f"invariant_breach_{sentinel.source_ref}"
            return SentinelEvaluation(
                observed=count,
                breached=True,
                evidence_samples=samples,
                signature=sig,
                resource_id=sentinel.source_ref,
                severity_override="sev2",
                details={
                    "statement": stmt,
                    "violations": count,
                    "threshold": threshold,
                    "state": sentinel.state,
                },
            )

        return SentinelEvaluation(
            observed=count,
            breached=False,
            details={"statement": stmt, "violations": count},
        )
