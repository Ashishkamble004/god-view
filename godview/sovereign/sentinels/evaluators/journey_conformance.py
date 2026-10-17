"""Sentinel evaluator for business journey conformance (`journey_conformance`).

Analyzes ledger journey traces for out-of-order execution, skipped steps,
infinite loops, or stalled flows.
"""

from __future__ import annotations

from typing import Any

from godview_shared.contracts import SentinelDefinition

from godview.sovereign.sentinels.evaluators.base import (
    BaseSentinelEvaluator,
    EvaluatorContext,
)
from godview.sovereign.sentinels.models import SentinelEvaluation


class JourneyConformanceEvaluator(BaseSentinelEvaluator):
    """Evaluates journey execution conformance against expected workflow DAGs."""

    async def evaluate(
        self,
        sentinel: SentinelDefinition,
        ctx: EvaluatorContext,
    ) -> SentinelEvaluation:
        spec = sentinel.check.spec or {}
        journey_name = spec.get("journey_name") or sentinel.source_ref

        breaches: list[dict[str, Any]] = []
        if ctx.bigquery is not None and hasattr(ctx.bigquery, "get_table"):
            rows = ctx.bigquery.get_table("journeys")
            for r in rows:
                if r.get("is_synthetic"):
                    continue
                if journey_name and r.get("name") and r.get("name") != journey_name:
                    continue
                conf = str(r.get("conformance") or "").upper()
                if conf in ("SKIPPED", "REORDERED", "LOOPED", "STALLED"):
                    breaches.append(r)

        if breaches:
            first_breach = breaches[0]
            sig = f"journey_violation_{first_breach.get('conformance', 'UNKNOWN').lower()}"
            return SentinelEvaluation(
                observed=float(len(breaches)),
                breached=True,
                evidence_samples=breaches[:5],
                signature=sig,
                resource_id=journey_name,
                severity_override="sev2",
                details={
                    "journey_name": journey_name,
                    "breach_count": len(breaches),
                    "sample_conformance": first_breach.get("conformance"),
                },
            )

        return SentinelEvaluation(
            observed=0.0,
            breached=False,
            details={"journey_name": journey_name, "status": "conformant"},
        )
