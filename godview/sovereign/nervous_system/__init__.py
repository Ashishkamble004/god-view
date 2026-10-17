"""Sovereign Nervous System CloudEvents 1.0 Router (`godview.sovereign.nervous_system`).

Validates and publishes CloudEvents 1.0 envelopes on `godview-nervous-system` and
routes malformed payloads to `godview-nervous-system-dlq` (TDD §3.6).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from godview_shared.contracts import ALLOWED_CLOUDEVENT_TYPES, NervousSystemEvent
from godview_shared.fakes import InMemoryPubSub


class NervousSystemRouter:
    """Pub/Sub CloudEvents 1.0 router with schema validation."""

    TOPIC: str = "godview-nervous-system"
    DLQ_TOPIC: str = "godview-nervous-system-dlq"

    def __init__(self, pubsub: InMemoryPubSub, env_id: str = "local-compose") -> None:
        self._pubsub = pubsub
        self.env_id = env_id

    async def emit(
        self,
        event_type: str,
        source: str,
        data: dict[str, Any],
        *,
        subject: str | None = None,
        incident_id: str | None = None,
        synthetic: bool = False,
        event_id: str | None = None,
    ) -> NervousSystemEvent:
        """Construct, validate, and publish a CloudEvents 1.0 envelope."""
        if event_type not in ALLOWED_CLOUDEVENT_TYPES:
            raise ValueError(f"CloudEvent type '{event_type}' is not in ALLOWED_CLOUDEVENT_TYPES")
        payload_data = dict(data)
        if incident_id is not None:
            payload_data.setdefault("incident_id", incident_id)
        envelope = NervousSystemEvent(
            specversion="1.0",
            type=event_type,
            source=source,
            id=event_id or f"evt-{uuid4().hex[:12]}",
            time=datetime.now(tz=UTC).isoformat(),
            subject=subject,
            data=payload_data,
            godviewenvid=self.env_id,
            godviewsynthetic=synthetic,
        )
        await self._pubsub.publish(self.TOPIC, envelope)
        return envelope
