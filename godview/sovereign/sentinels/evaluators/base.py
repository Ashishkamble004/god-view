"""Base interfaces for sentinel check evaluators."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from godview_shared.contracts import SentinelDefinition

from godview.sovereign.sentinels.models import SentinelEvaluation


class EvaluatorContext:
    """Runtime context passed to sentinel evaluators."""

    def __init__(
        self,
        invoker: Any | None = None,
        firestore: Any | None = None,
        bigquery: Any | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.invoker = invoker
        self.firestore = firestore
        self.bigquery = bigquery
        self.clock = clock or (lambda: datetime.now(tz=UTC))

    async def invoke_capability(self, capability_id: str, args: dict[str, Any]) -> Any:
        if self.invoker is None:
            return None
        if hasattr(self.invoker, "invoke"):
            return await self.invoker.invoke(capability_id, args)
        if callable(self.invoker):
            res = self.invoker(capability_id, args)
            if hasattr(res, "__await__"):
                return await res
            return res
        return None


class BaseSentinelEvaluator(ABC):
    """Abstract base class for a sentinel evaluator."""

    @abstractmethod
    async def evaluate(
        self,
        sentinel: SentinelDefinition,
        ctx: EvaluatorContext,
    ) -> SentinelEvaluation:
        """Evaluate the check for the given sentinel definition."""
