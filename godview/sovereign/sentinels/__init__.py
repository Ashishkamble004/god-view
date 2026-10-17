"""Universal sentinel pack and detection system (`godview.sovereign.sentinels`)."""

from __future__ import annotations

from typing import Any

from godview.sovereign.sentinels.evaluators import (
    EVALUATOR_REGISTRY,
    BaseSentinelEvaluator,
    EvaluatorContext,
    get_evaluator,
)
from godview.sovereign.sentinels.models import SentinelEvaluation
from godview.sovereign.sentinels.pack import (
    build_golden_signals_sentinel,
    build_invariant_sentinel,
    build_journey_conformance_sentinel,
    build_kpi_changepoint_sentinel,
    build_lb_requests_sentinel,
    build_posture_sentinel,
    build_probe_sentinel,
    build_pubsub_health_sentinel,
    compile_universal_sentinels,
)


def install(ctx: Any) -> Any:
    """Wire up SentinelEngine and IncidentManager into SovereignContext lifecycle."""
    from godview.sovereign.sentinel_engine import install as _install

    return _install(ctx)


__all__ = [
    "EVALUATOR_REGISTRY",
    "BaseSentinelEvaluator",
    "EvaluatorContext",
    "SentinelEvaluation",
    "build_golden_signals_sentinel",
    "build_invariant_sentinel",
    "build_journey_conformance_sentinel",
    "build_kpi_changepoint_sentinel",
    "build_lb_requests_sentinel",
    "build_posture_sentinel",
    "build_probe_sentinel",
    "build_pubsub_health_sentinel",
    "compile_universal_sentinels",
    "get_evaluator",
    "install",
]
