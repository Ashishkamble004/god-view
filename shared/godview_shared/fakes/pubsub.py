"""In-memory Pub/Sub fake for godview-nervous-system and DLQ (TDD §3.6)."""

from __future__ import annotations

import copy
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from godview_shared.contracts import NervousSystemEvent

DEFAULT_TOPIC = "godview-nervous-system"
DEFAULT_DLQ_TOPIC = "godview-nervous-system-dlq"

SubscriberHandler = Callable[[dict[str, Any]], Any | Awaitable[Any]]


class InMemoryPubSub:
    """Hermetic Pub/Sub broker validating CloudEvents 1.0 and routing failures to DLQ."""

    def __init__(self) -> None:
        self._topics: dict[str, list[dict[str, Any]]] = {
            DEFAULT_TOPIC: [],
            DEFAULT_DLQ_TOPIC: [],
        }
        self._subscribers: dict[str, list[SubscriberHandler]] = {
            DEFAULT_TOPIC: [],
            DEFAULT_DLQ_TOPIC: [],
        }

    def subscribe(self, topic: str, handler: SubscriberHandler) -> None:
        """Register a subscriber callback on topic."""
        self._subscribers.setdefault(topic, []).append(handler)

    async def publish(
        self,
        topic: str,
        event: NervousSystemEvent | dict[str, Any],
    ) -> str:
        """Validate CloudEvent and dispatch to all topic subscribers; route handler errors to DLQ."""
        if isinstance(event, NervousSystemEvent):
            validated = event
        else:
            validated = NervousSystemEvent.model_validate(event)
        payload = validated.model_dump(mode="json")
        self._topics.setdefault(topic, []).append(copy.deepcopy(payload))

        for handler in self._subscribers.get(topic, []):
            try:
                res = handler(copy.deepcopy(payload))
                if inspect.isawaitable(res):
                    await res
            except Exception as exc:
                dlq_entry = {
                    "original_topic": topic,
                    "event": copy.deepcopy(payload),
                    "error": str(exc),
                }
                self._topics.setdefault(DEFAULT_DLQ_TOPIC, []).append(dlq_entry)
                raise
        return validated.id

    def get_published(self, topic: str = DEFAULT_TOPIC) -> list[dict[str, Any]]:
        """Return all events published to topic."""
        return copy.deepcopy(self._topics.get(topic, []))

    def get_dlq_messages(self) -> list[dict[str, Any]]:
        """Return dead-lettered messages."""
        return copy.deepcopy(self._topics.get(DEFAULT_DLQ_TOPIC, []))
