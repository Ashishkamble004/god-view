"""Pub/Sub DLQ replay remediation executor (Plan §3.4).

Executes:
- replay_dlq: ensures DLQ subscription exists, pulls unacknowledged messages,
  republishes them to the primary target topic, and acknowledges them from the DLQ.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from godview.sovereign.remediation.types import (
    BaseExecutor,
    ExecutionResult,
    VerificationCheck,
)

logger = logging.getLogger(__name__)

PUBSUB_API_BASE: str = "https://pubsub.googleapis.com/v1"


def _format_topic(project_id: str, topic: str) -> str:
    if topic.startswith("projects/"):
        return topic
    return f"projects/{project_id}/topics/{topic}"


def _format_sub(project_id: str, sub: str) -> str:
    if sub.startswith("projects/"):
        return sub
    return f"projects/{project_id}/subscriptions/{sub}"


class PubSubDlqExecutor(BaseExecutor):
    """Executor for Pub/Sub dead-letter queue replay."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client

    async def _get_auth_token(self, ctx: Any) -> str:
        if ctx and hasattr(ctx, "auth") and ctx.auth is not None:
            token_fn = getattr(ctx.auth, "access_token", None)
            if callable(token_fn):
                res = token_fn()
                if hasattr(res, "__await__"):
                    return str(await res)
                return str(res)
        return "mock-token"

    async def execute(
        self,
        ctx: Any,
        args: dict[str, Any],
        target_resource: str,
        *,
        agent_id: str | None = None,
        approval_id: str | None = None,
        **kwargs: Any,
    ) -> ExecutionResult:
        """Execute DLQ replay workflow."""
        project_id = args.get("project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        raw_topic = args.get("topic") or target_resource.split("/")[-1]
        dlq_topic = _format_topic(project_id, raw_topic)

        # Target topic for replayed messages
        raw_target_topic = (
            args.get("target_topic")
            or os.environ.get("NERVOUS_SYSTEM_TOPIC")
            or raw_topic.replace("-dlq", "")
        )
        target_topic = _format_topic(project_id, raw_target_topic)

        # DLQ subscription
        raw_sub = args.get("subscription") or f"{raw_topic}-sub"
        dlq_sub = _format_sub(project_id, raw_sub)

        max_messages = int(args.get("max_messages", 100))

        token = await self._get_auth_token(ctx)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        client = self._client or httpx.AsyncClient()

        checks: list[VerificationCheck] = []

        try:
            # 1. Ensure DLQ subscription exists (create if missing)
            sub_resp = await client.get(f"{PUBSUB_API_BASE}/{dlq_sub}", headers=headers)
            if sub_resp.status_code == 404:
                create_resp = await client.put(
                    f"{PUBSUB_API_BASE}/{dlq_sub}",
                    headers=headers,
                    json={"topic": dlq_topic},
                )
                create_resp.raise_for_status()
                checks.append(
                    VerificationCheck(
                        check="dlq_subscription_created",
                        passed=True,
                        evidence={"subscription": dlq_sub, "topic": dlq_topic},
                    )
                )
            else:
                checks.append(
                    VerificationCheck(
                        check="dlq_subscription_exists",
                        passed=sub_resp.status_code == 200,
                        evidence={"subscription": dlq_sub},
                    )
                )

            # 2. Pull messages from DLQ subscription
            pull_resp = await client.post(
                f"{PUBSUB_API_BASE}/{dlq_sub}:pull",
                headers=headers,
                json={"maxMessages": max_messages, "returnImmediately": True},
            )
            pull_resp.raise_for_status()
            pull_data = pull_resp.json()
            received_messages = pull_data.get("receivedMessages", [])

            pulled_count = len(received_messages)
            before = {"dlq_subscription": dlq_sub, "pulled_count": pulled_count}

            if pulled_count == 0:
                checks.append(
                    VerificationCheck(
                        check="dlq_messages_replayed",
                        passed=True,
                        evidence={"message": "No undelivered messages in DLQ"},
                    )
                )
                return ExecutionResult(
                    ok=True,
                    before=before,
                    after={"republished_count": 0, "acknowledged_count": 0},
                    verification=checks,
                )

            # 3. Republish messages to primary target topic
            messages_to_publish = []
            ack_ids = []
            for item in received_messages:
                msg = item.get("message", {})
                ack_ids.append(item.get("ackId"))
                pub_item: dict[str, Any] = {"data": msg.get("data", "")}
                if msg.get("attributes"):
                    pub_item["attributes"] = msg["attributes"]
                messages_to_publish.append(pub_item)

            pub_resp = await client.post(
                f"{PUBSUB_API_BASE}/{target_topic}:publish",
                headers=headers,
                json={"messages": messages_to_publish},
            )
            pub_resp.raise_for_status()
            pub_data = pub_resp.json()
            message_ids = pub_data.get("messageIds", [])
            republished_count = len(message_ids)

            # 4. Acknowledge messages on DLQ
            ack_resp = await client.post(
                f"{PUBSUB_API_BASE}/{dlq_sub}:acknowledge",
                headers=headers,
                json={"ackIds": ack_ids},
            )
            ack_resp.raise_for_status()

            checks.append(
                VerificationCheck(
                    check="dlq_messages_replayed",
                    passed=(republished_count == pulled_count),
                    evidence={
                        "pulled": pulled_count,
                        "republished": republished_count,
                        "acknowledged": len(ack_ids),
                    },
                )
            )

            after = {
                "target_topic": target_topic,
                "republished_count": republished_count,
                "acknowledged_count": len(ack_ids),
            }

            return ExecutionResult(ok=True, before=before, after=after, verification=checks)
        except Exception as e:
            logger.exception("Error executing replay_dlq on %s", dlq_topic)
            return ExecutionResult(
                ok=False,
                before={"dlq_topic": dlq_topic},
                after=None,
                verification=checks,
                reverted=False,
                error=str(e),
            )
        finally:
            if self._client is None:
                await client.aclose()


__all__ = ["PubSubDlqExecutor"]
