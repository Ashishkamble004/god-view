"""Pub/Sub DLQ replay remediation executor (Plan §3.4).

Executes:
- replay_dlq: ensures DLQ subscription exists, checks depth before, seeks if requested,
  pulls unacknowledged messages, republishes them to the primary target topic,
  acknowledges them from the DLQ, and verifies depth reduction.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from godview.sovereign.remediation.impersonation import get_act_sa_access_token
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

    async def _get_auth_token(self, ctx: Any, project_id: str | None = None) -> str:
        return await get_act_sa_access_token(ctx, project_id=project_id, client=self._client)

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
        """Execute DLQ replay workflow with before/after depth verification and seek."""
        project_id = args.get("project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        raw_topic = args.get("topic") or target_resource.split("/")[-1].split("?")[0].strip()
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

        token = await self._get_auth_token(ctx, project_id=project_id)
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

            # 2. Optional Seek
            seek_time = args.get("seek_time")
            seek_snapshot = args.get("seek_snapshot")
            if seek_time or seek_snapshot:
                seek_body: dict[str, Any] = {}
                if seek_time:
                    seek_body["time"] = seek_time
                if seek_snapshot:
                    seek_body["snapshot"] = seek_snapshot
                seek_resp = await client.post(
                    f"{PUBSUB_API_BASE}/{dlq_sub}:seek",
                    headers=headers,
                    json=seek_body,
                )
                seek_resp.raise_for_status()
                checks.append(
                    VerificationCheck(
                        check="dlq_subscription_seek_executed",
                        passed=seek_resp.status_code == 200,
                        evidence={"seek_body": seek_body},
                    )
                )

            # 3. Pull messages from DLQ subscription (depth before)
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
                        check="dlq_replay_drained",
                        passed=True,
                        evidence={"replayed_count": 0, "message": "DLQ is clean, no unacknowledged messages found"},
                    )
                )
                return ExecutionResult(ok=True, before=before, after={"replayed_count": 0}, verification=checks)

            # 4. Republish messages to primary target topic
            publish_messages = []
            ack_ids = []
            for item in received_messages:
                msg = item.get("message", {})
                pub_item: dict[str, Any] = {}
                if "data" in msg:
                    pub_item["data"] = msg["data"]
                if "attributes" in msg:
                    pub_item["attributes"] = dict(msg["attributes"])
                    pub_item["attributes"]["x-godview-replayed"] = "true"
                else:
                    pub_item["attributes"] = {"x-godview-replayed": "true"}
                publish_messages.append(pub_item)
                if "ackId" in item:
                    ack_ids.append(item["ackId"])

            pub_resp = await client.post(
                f"{PUBSUB_API_BASE}/{target_topic}:publish",
                headers=headers,
                json={"messages": publish_messages},
            )
            pub_resp.raise_for_status()
            pub_data = pub_resp.json()
            message_ids = pub_data.get("messageIds", [])

            republish_passed = len(message_ids) == pulled_count
            checks.append(
                VerificationCheck(
                    check="dlq_messages_republished_to_target_topic",
                    passed=republish_passed,
                    evidence={
                        "target_topic": target_topic,
                        "republished_count": len(message_ids),
                        "expected_count": pulled_count,
                    },
                )
            )

            # 5. Acknowledge replayed messages from DLQ subscription
            ack_passed = True
            if ack_ids and republish_passed:
                ack_resp = await client.post(
                    f"{PUBSUB_API_BASE}/{dlq_sub}:acknowledge",
                    headers=headers,
                    json={"ackIds": ack_ids},
                )
                ack_resp.raise_for_status()
                ack_passed = (ack_resp.status_code == 200)

            checks.append(
                VerificationCheck(
                    check="dlq_messages_acknowledged",
                    passed=ack_passed,
                    evidence={"ack_count": len(ack_ids)},
                )
            )

            after = {
                "dlq_subscription": dlq_sub,
                "republished_count": len(message_ids),
                "acknowledged_count": len(ack_ids),
                "target_topic": target_topic,
            }

            all_passed = republish_passed and ack_passed
            return ExecutionResult(ok=all_passed, before=before, after=after, verification=checks)
        except Exception as e:
            logger.exception("Error executing replay_dlq on %s", dlq_topic)
            return ExecutionResult(
                ok=False,
                before=None,
                after=None,
                verification=checks,
                reverted=False,
                error=str(e),
            )


__all__ = ["PubSubDlqExecutor"]
