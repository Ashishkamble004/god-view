"""Production Pub/Sub EventBus implementation for God-View Sovereign."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from godview_shared.contracts import NervousSystemEvent

logger = logging.getLogger("godview.stores.pubsub")


class PubSubBus:
    """Production Cloud Pub/Sub EventBus for godview-nervous-system."""

    def __init__(
        self,
        project: str | None = None,
        topic: str | None = None,
        publisher: Any = None,
    ) -> None:
        self.project = (
            project
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or ""
        )
        self.default_topic = (
            topic
            or os.environ.get("NERVOUS_SYSTEM_TOPIC")
            or "godview-nervous-system"
        )
        self._publisher = publisher
        self._subscribers: dict[str, list[Callable[[dict[str, Any]], Any]]] = {}

    def _get_publisher(self) -> Any:
        if self._publisher is None:
            from google.cloud import pubsub_v1

            self._publisher = pubsub_v1.PublisherClient()
        return self._publisher

    async def publish(
        self,
        topic: str,
        event: Any,
    ) -> str:
        """Publish an event to a Pub/Sub topic."""
        target_topic = topic or self.default_topic
        publisher = self._get_publisher()

        if isinstance(event, NervousSystemEvent):
            validated = event
        elif isinstance(event, dict):
            validated = NervousSystemEvent.model_validate(event)
        else:
            raise ValueError(f"Event must be NervousSystemEvent or dict, got {type(event)}")

        payload_bytes = validated.model_dump_json().encode("utf-8")
        topic_path = (
            publisher.topic_path(self.project, target_topic)
            if "/" not in target_topic and self.project
            else target_topic
        )

        # Full CloudEvents 1.0 binary mode attributes
        now_iso = datetime.now(tz=UTC).isoformat()
        attrs = {
            "ce-specversion": "1.0",
            "ce-type": str(validated.type),
            "ce-source": str(validated.source),
            "ce-id": str(validated.id),
            "ce-time": str(getattr(validated, "time", None) or now_iso),
            "datacontenttype": "application/json",
        }
        if validated.subject:
            attrs["ce-subject"] = str(validated.subject)

        # Run publish async with timeout
        future = publisher.publish(topic_path, payload_bytes, **attrs)
        msg_id = await asyncio.to_thread(future.result, 10.0)

        # In GCP mode, events are delivered via Cloud Pub/Sub push subscription (/nervous-system/push).
        # Triggering in-process subscribers in GCP mode would cause duplicate processing.
        is_gcp = os.environ.get("GODVIEW_ENV") == "gcp" or bool(os.environ.get("K_SERVICE"))
        if not is_gcp and target_topic in self._subscribers:
            data_dict = validated.model_dump(mode="json")
            for handler in self._subscribers[target_topic]:
                try:
                    res = handler(data_dict)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as exc:
                    logger.warning("PubSub subscriber handler error: %s", exc)

        return str(msg_id or validated.id)

    def subscribe(
        self,
        topic: str,
        handler: Callable[[dict[str, Any]], Any],
    ) -> None:
        """Register a subscriber callback on topic."""
        self._subscribers.setdefault(topic, []).append(handler)
