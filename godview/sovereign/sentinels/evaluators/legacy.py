"""Legacy sentinel evaluators for backwards compatibility with v4 tests."""

from __future__ import annotations

from typing import Any

from godview_shared.contracts import SentinelDefinition

from godview.sovereign.sentinels.evaluators.base import (
    BaseSentinelEvaluator,
    EvaluatorContext,
)
from godview.sovereign.sentinels.models import SentinelEvaluation


class LegacyEvaluator(BaseSentinelEvaluator):
    """Evaluates v4 check classes (invariant_sql, probe_plan, golden_signal_gate, drift_diff, budget, discovery)."""

    async def evaluate(
        self,
        sentinel: SentinelDefinition,
        ctx: EvaluatorContext,
    ) -> SentinelEvaluation:
        cls_name = sentinel.sentinel_class
        bq = ctx.bigquery
        fs = ctx.firestore

        observed = 0.0
        samples: list[dict[str, Any]] = []

        if cls_name == "invariant_sql":
            stmt = (sentinel.check.statement or "SELECT COUNT(*) FROM events").strip().rstrip(";")
            lower_stmt = stmt.lower()
            if "source" not in lower_stmt:
                if not any(kw in lower_stmt for kw in (" group by ", " order by ", " limit ", " join ")):
                    if " where " in lower_stmt:
                        stmt = f"{stmt} AND COALESCE(source, 'otel') != 'probe'"
                    else:
                        stmt = f"{stmt} WHERE COALESCE(source, 'otel') != 'probe'"
            if bq is not None and hasattr(bq, "query"):
                rows = bq.query(stmt)
                if rows:
                    first_row = rows[0]
                    raw_val = (
                        first_row.get("count")
                        or first_row.get("violations")
                        or next(iter(first_row.values()), 0.0)
                    )
                    observed = float(raw_val or 0.0)
                if hasattr(bq, "get_table"):
                    samples = [r for r in bq.get_table("events") if r.get("source") != "probe"][:5]

        elif cls_name == "probe_plan":
            if bq is not None and hasattr(bq, "get_table"):
                failed_probes = [
                    r
                    for r in bq.get_table("events")
                    if r.get("source") == "probe" and int(r.get("http_status") or 200) >= 500
                ]
                observed = float(len(failed_probes))
                samples = failed_probes[:5]

        elif cls_name == "golden_signal_gate":
            if bq is not None and hasattr(bq, "get_table"):
                breaches = [
                    r
                    for r in bq.get_table("events")
                    if r.get("source") != "probe" and int(r.get("http_status") or 200) >= 500
                ]
                observed = float(len(breaches))
                samples = breaches[:5]

        elif cls_name == "drift_diff":
            if fs is not None and hasattr(fs, "list_docs"):
                drift_docs = [
                    *fs.list_docs(
                        "cost_findings",
                        predicate=lambda d: d.get("finding_type")
                        in ("terraform_drift", "drift_diff", "iac_drift"),
                    ),
                    *fs.list_docs(
                        "change_events",
                        predicate=lambda d: d.get("kind") == "tfstate_change",
                    ),
                ]
                observed = float(len(drift_docs))
                samples = drift_docs[:5]

        elif cls_name == "budget":
            if fs is not None and hasattr(fs, "list_docs"):
                exhausted_docs = fs.list_docs(
                    "cost_findings",
                    predicate=lambda d: d.get("finding_type") == "budget_threshold",
                )
                observed = float(len(exhausted_docs))
                samples = exhausted_docs[:5]

        elif cls_name == "discovery":
            if fs is not None and hasattr(fs, "list_docs"):
                gone_docs = fs.list_docs(
                    "discovered_services",
                    predicate=lambda d: d.get("status") == "gone",
                )
                observed = float(len(gone_docs))
                samples = gone_docs[:5]

        op = sentinel.wake_condition.operator
        thresh = sentinel.wake_condition.threshold
        breached = False
        if op == ">=":
            breached = observed >= thresh
        elif op == ">":
            breached = observed > thresh
        elif op == "<=":
            breached = observed <= thresh
        elif op == "<":
            breached = observed < thresh
        elif op == "==":
            breached = observed == thresh
        elif op == "!=":
            breached = observed != thresh

        return SentinelEvaluation(
            observed=observed,
            breached=breached,
            evidence_samples=samples,
            signature=f"{cls_name}_{sentinel.source_ref}",
            resource_id=sentinel.source_ref,
            severity_override="sev2",
            details={"observed": observed, "threshold": thresh},
        )
