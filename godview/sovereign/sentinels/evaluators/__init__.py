"""Registry of sentinel evaluators."""

from __future__ import annotations

from godview.sovereign.sentinels.evaluators.base import (
    BaseSentinelEvaluator,
    EvaluatorContext,
)
from godview.sovereign.sentinels.evaluators.golden_signals import GoldenSignalsEvaluator
from godview.sovereign.sentinels.evaluators.invariant import InvariantEvaluator
from godview.sovereign.sentinels.evaluators.journey_conformance import (
    JourneyConformanceEvaluator,
)
from godview.sovereign.sentinels.evaluators.kpi_changepoint import (
    KpiChangepointEvaluator,
)
from godview.sovereign.sentinels.evaluators.lb_requests import LbRequestsEvaluator
from godview.sovereign.sentinels.evaluators.legacy import LegacyEvaluator
from godview.sovereign.sentinels.evaluators.posture import PostureEvaluator
from godview.sovereign.sentinels.evaluators.probe import ProbeEvaluator
from godview.sovereign.sentinels.evaluators.pubsub_health import PubSubHealthEvaluator

EVALUATOR_REGISTRY: dict[str, BaseSentinelEvaluator] = {
    "lb_requests": LbRequestsEvaluator(),
    "posture": PostureEvaluator(),
    "probe": ProbeEvaluator(),
    "pubsub_health": PubSubHealthEvaluator(),
    "golden_signals": GoldenSignalsEvaluator(),
    "journey_conformance": JourneyConformanceEvaluator(),
    "kpi_changepoint": KpiChangepointEvaluator(),
    "invariant": InvariantEvaluator(),
    "invariant_sql": LegacyEvaluator(),
    "probe_plan": LegacyEvaluator(),
    "golden_signal_gate": LegacyEvaluator(),
    "drift_diff": LegacyEvaluator(),
    "budget": LegacyEvaluator(),
    "discovery": LegacyEvaluator(),
}


def get_evaluator(sentinel_class: str) -> BaseSentinelEvaluator:
    """Return the evaluator instance for a sentinel class."""
    if sentinel_class in EVALUATOR_REGISTRY:
        return EVALUATOR_REGISTRY[sentinel_class]
    raise ValueError(f"Unsupported sentinel class '{sentinel_class}'")


__all__ = [
    "EVALUATOR_REGISTRY",
    "BaseSentinelEvaluator",
    "EvaluatorContext",
    "GoldenSignalsEvaluator",
    "InvariantEvaluator",
    "JourneyConformanceEvaluator",
    "KpiChangepointEvaluator",
    "LbRequestsEvaluator",
    "LegacyEvaluator",
    "PostureEvaluator",
    "ProbeEvaluator",
    "PubSubHealthEvaluator",
    "get_evaluator",
]
