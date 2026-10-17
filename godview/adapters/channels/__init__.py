"""Google Chat and Email notification/approval channel adapters (`godview/adapters/channels/`).

Implements:
- `GoogleChatAdapter`: renders `ApprovalCard` as Google Chat `cardsV2` JSON with interactive
  `Approve`, `Reject`, and `Snooze` buttons, delivers via `httpx` webhook or injected sender,
  validates `approval_id` (`^apr-[\\w-]+$`) and authenticated `user.email`, and dispatches
  interaction callbacks to `/v1/approvals/{id}:approve`, `:reject`, and `:snooze`.
- `EmailChannelAdapter`: fallback channel when `GODVIEW_CHAT_ENABLED=false` (Decision A15)
  or when Google Chat delivery fails (SDD §9), plus customer notifications (`comms.customer_notify`).
- `ChannelsSenseAdapter`: `SenseAdapter` enforcing the `08:00–22:00 IST` (`Asia/Kolkata`)
  operating window queue, `TrustDecision` (I-06), and the L2 hard cap on `comms.customer_notify`.
"""

from __future__ import annotations

import inspect
import os
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from godview_shared.adapter_contract import (
    AdapterHealth,
    DiscoveryResult,
    SenseAdapter,
    SenseCategory,
)
from godview_shared.capabilities import CapabilitySpec, get_capability
from godview_shared.contracts import ApprovalCard, TrustDecision, TrustViolation

IST = ZoneInfo("Asia/Kolkata")
OPERATING_WINDOW_START_HOUR_IST = 8
OPERATING_WINDOW_END_HOUR_IST = 22
_APPROVAL_ID_PATTERN = re.compile(r"^apr-[A-Za-z0-9_-]+$")


def is_within_ist_operating_window(now: datetime | str | None = None) -> bool:
    """Return True if `now` falls within 08:00–22:00 IST (`Asia/Kolkata`)."""
    if now is None:
        dt = datetime.now(tz=IST)
    elif isinstance(now, str):
        parsed = datetime.fromisoformat(now.replace("Z", "+00:00"))
        dt = parsed.astimezone(IST) if parsed.tzinfo else parsed.replace(tzinfo=IST)
    else:
        dt = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
    return OPERATING_WINDOW_START_HOUR_IST <= dt.hour < OPERATING_WINDOW_END_HOUR_IST


class GoogleChatAdapter:
    """Google Chat `cardsV2` renderer, webhook sender, and interaction callback router."""

    def __init__(
        self,
        *,
        chat_enabled: bool = False,
        webhook_url: str | None = None,
        sovereign_base_url: str = "http://localhost:8090",
        http_transport: httpx.AsyncBaseTransport | None = None,
        webhook_sender: (
            Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]] | None
        ) = None,
        approval_callback_dispatcher: (
            Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]] | None
        ) = None,
    ) -> None:
        self.chat_enabled = chat_enabled
        self.webhook_url = (
            webhook_url or os.environ.get("GODVIEW_CHAT_WEBHOOK_URL", "").strip() or None
        )
        self.sovereign_base_url = sovereign_base_url.rstrip("/")
        self._http_transport = http_transport
        self._webhook_sender = webhook_sender
        self._approval_callback_dispatcher = approval_callback_dispatcher
        self.sent_cards: list[dict[str, Any]] = []

    def _http_client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"timeout": 10.0}
        if self._http_transport is not None:
            kwargs["transport"] = self._http_transport
        return httpx.AsyncClient(**kwargs)

    def render_approval_card(self, card: ApprovalCard) -> dict[str, Any]:
        """Render an `ApprovalCard` contract into a Google Chat `cardsV2` payload."""
        widgets: list[dict[str, Any]] = [
            {
                "decoratedText": {
                    "topLabel": "Narrative",
                    "text": card.narrative,
                    "wrapText": True,
                }
            },
            {
                "decoratedText": {
                    "topLabel": "Target Resource",
                    "text": card.target_resource,
                    "wrapText": True,
                }
            },
            {
                "decoratedText": {
                    "topLabel": "Evidence Pack",
                    "text": card.evidence_pack_id,
                }
            },
        ]
        impact = getattr(card, "financial_impact", None) or getattr(card, "impact_amount", None)
        curr = getattr(card, "currency", None) or "USD"
        if impact is not None and impact != 0:
            widgets.append(
                {
                    "decoratedText": {
                        "topLabel": f"Estimated Impact ({curr})",
                        "text": f"{curr} {impact:,} ({impact})",
                    }
                }
            )
        widgets.append(
            {
                "decoratedText": {
                    "topLabel": "Expires At",
                    "text": card.expires_at,
                }
            }
        )

        action_buttons = [
            ("Approve", "approval_approve", "approve"),
            ("Reject", "approval_reject", "reject"),
            ("Snooze", "approval_snooze", "snooze"),
        ]
        buttons_payload = [
            {
                "text": label,
                "onClick": {
                    "action": {
                        "function": func_name,
                        "parameters": [
                            {"key": "approval_id", "value": card.approval_id},
                            {"key": "incident_id", "value": card.incident_id},
                            {"key": "action", "value": action_key},
                        ],
                    }
                },
            }
            for label, func_name, action_key in action_buttons
        ]

        return {
            "cardsV2": [
                {
                    "cardId": card.approval_id,
                    "card": {
                        "header": {
                            "title": f"Approval Required: {card.capability}",
                            "subtitle": (
                                f"Incident {card.incident_id} | "
                                f"Agent {card.agent_id} | "
                                f"Trust L{card.trust_level_required}"
                            ),
                        },
                        "sections": [
                            {
                                "header": "Remediation Details",
                                "widgets": widgets,
                            },
                            {
                                "widgets": [
                                    {
                                        "buttonList": {
                                            "buttons": buttons_payload,
                                        }
                                    }
                                ]
                            },
                        ],
                    },
                }
            ]
        }

    async def send_card(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Deliver a `cardsV2` or text payload to Google Chat when enabled."""
        if not self.chat_enabled:
            raise RuntimeError("Google Chat channel is disabled (GODVIEW_CHAT_ENABLED=false)")
        if self._webhook_sender is not None:
            res = self._webhook_sender(payload)
            if inspect.isawaitable(res):
                res = await res
            self.sent_cards.append(payload)
            return dict(res)

        if not self.webhook_url:
            raise RuntimeError(
                "GoogleChatAdapter.send_card requires webhook_url (or GODVIEW_CHAT_WEBHOOK_URL) or webhook_sender when chat_enabled=True"
            )
        async with self._http_client() as client:
            resp = await client.post(self.webhook_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            self.sent_cards.append(payload)
            return dict(data) if isinstance(data, dict) else {"response": data}

    async def handle_interaction_callback(self, event: dict[str, Any]) -> dict[str, Any]:
        """Parse a Google Chat `CARD_CLICKED` callback and route to Sovereign `/v1/approvals`."""
        common = event.get("common", {})
        raw_params = common.get("parameters", {})
        if isinstance(raw_params, list):
            params = {str(item.get("key", "")): str(item.get("value", "")) for item in raw_params}
        elif isinstance(raw_params, dict):
            params = {str(k): str(v) for k, v in raw_params.items()}
        else:
            params = {}

        invoked_fn = str(common.get("invokedFunction", ""))
        action = params.get("action") or invoked_fn.removeprefix("approval_")
        action = action.lower().strip()

        action_map: dict[str, tuple[str, str]] = {
            "approve": (":approve", "approved"),
            "reject": (":reject", "rejected"),
            "snooze": (":snooze", "snoozed"),
        }
        if action not in action_map:
            raise ValueError(f"Unsupported Google Chat approval callback action: '{action}'")

        approval_id = params.get("approval_id", "").strip()
        if not approval_id or not _APPROVAL_ID_PATTERN.match(approval_id):
            raise ValueError(
                f"Invalid or missing 'approval_id' in Google Chat callback parameters (must match ^apr-[\\w-]+$, got {approval_id!r})"
            )
        incident_id = params.get("incident_id", "").strip()
        suffix, decision = action_map[action]
        endpoint = f"{self.sovereign_base_url}/v1/approvals/{approval_id}{suffix}"

        user = event.get("user")
        user_email = str(user.get("email", "")).strip() if isinstance(user, dict) else ""
        if not user_email or "@" not in user_email:
            raise ValueError("Missing authenticated user.email in Google Chat interaction callback")
        display_name = (
            str(user.get("displayName") or user_email) if isinstance(user, dict) else user_email
        )

        approver = {
            "principal": user_email,
            "display_name": display_name,
            "channel": "chat",
            "decided_at": str(event.get("eventTime") or datetime.now(tz=UTC).isoformat()),
        }
        body: dict[str, Any] = {
            "approval_id": approval_id,
            "incident_id": incident_id,
            "decision": decision,
            "approver": approver,
        }

        if self._approval_callback_dispatcher is not None:
            res = self._approval_callback_dispatcher(endpoint, body)
            if inspect.isawaitable(res):
                res = await res
            dispatch_response = dict(res)
        else:
            async with self._http_client() as client:
                resp = await client.post(endpoint, json=body)
                resp.raise_for_status()
                data = resp.json()
                dispatch_response = dict(data) if isinstance(data, dict) else {"response": data}

        return {
            "status": "ok",
            "approval_id": approval_id,
            "incident_id": incident_id,
            "decision": decision,
            "approver": approver,
            "endpoint": endpoint,
            "dispatch_response": dispatch_response,
        }


class EmailChannelAdapter:
    """Email fallback adapter for operator approval cards and L2 customer notifications."""

    def __init__(self, *, default_recipient: str = "ops-fallback@example.com") -> None:
        self.default_recipient = default_recipient
        self.sent_emails: list[dict[str, Any]] = []

    def send_approval_email(
        self,
        card: ApprovalCard,
        *,
        recipient: str | None = None,
        reason: str = "chat_disabled",
    ) -> dict[str, Any]:
        """Deliver an `ApprovalCard` via email fallback (Decision A15, SDD §9)."""
        to_addr = recipient or self.default_recipient
        subject = f"[God-View Approval {card.approval_id}] Incident {card.incident_id} ({card.capability})"
        impact = getattr(card, "financial_impact", None) or getattr(card, "impact_amount", None)
        curr = getattr(card, "currency", None) or "USD"
        body = (
            f"Narrative: {card.narrative}\n"
            f"Target Resource: {card.target_resource}\n"
            f"Evidence Pack: {card.evidence_pack_id}\n"
            f"Financial Impact: {curr} {impact}\n"
            f"Expires At: {card.expires_at}\n"
            f"Approve: /v1/approvals/{card.approval_id}:approve\n"
            f"Reject:  /v1/approvals/{card.approval_id}:reject\n"
            f"Snooze:  /v1/approvals/{card.approval_id}:snooze\n"
        )
        email_record: dict[str, Any] = {
            "to": to_addr,
            "subject": subject,
            "body": body,
            "approval_id": card.approval_id,
            "incident_id": card.incident_id,
            "reason": reason,
            "sent_at": datetime.now(tz=UTC).isoformat(),
        }
        self.sent_emails.append(email_record)
        return email_record

    def send_notification_email(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        approval_id: str,
        customer_id: str | None = None,
    ) -> dict[str, Any]:
        """Deliver an L2 human-approved customer or operator notification email."""
        email_record: dict[str, Any] = {
            "to": recipient,
            "subject": subject,
            "body": body,
            "approval_id": approval_id,
            "customer_id": customer_id,
            "sent_at": datetime.now(tz=UTC).isoformat(),
        }
        self.sent_emails.append(email_record)
        return email_record


class ChannelsSenseAdapter(SenseAdapter):
    """SenseAdapter for `comms.chat_card` (L1) and `comms.customer_notify` (L2 cap)."""

    family = "channels"
    name = "gcp-channels"

    def __init__(
        self,
        *,
        chat_adapter: GoogleChatAdapter | None = None,
        email_adapter: EmailChannelAdapter | None = None,
    ) -> None:
        self.chat_adapter = chat_adapter or GoogleChatAdapter(chat_enabled=False)
        self.email_adapter = email_adapter or EmailChannelAdapter()
        self._queued_cards: list[dict[str, Any]] = []

    @classmethod
    def from_env(
        cls,
        *,
        email_adapter: EmailChannelAdapter | None = None,
    ) -> ChannelsSenseAdapter:
        """Construct `ChannelsSenseAdapter` honoring `GODVIEW_CHAT_ENABLED` (default `false`, A15)."""
        raw_flag = os.environ.get("GODVIEW_CHAT_ENABLED", "false").strip().lower()
        chat_enabled = raw_flag in ("1", "true", "yes")
        return cls(
            chat_adapter=GoogleChatAdapter(chat_enabled=chat_enabled),
            email_adapter=email_adapter or EmailChannelAdapter(),
        )

    @property
    def queued_count(self) -> int:
        """Number of non-urgent cards currently queued for 08:00 IST delivery."""
        return len(self._queued_cards)

    async def discover(self) -> DiscoveryResult:
        return DiscoveryResult(
            evidence={
                "chat_enabled": self.chat_adapter.chat_enabled,
                "email_fallback": "available",
                "operating_window_ist": "08:00-22:00",
            }
        )

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.HUMAN_DOCS_CHAT]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {
            "comms.chat_card": get_capability("comms.chat_card"),
            "comms.customer_notify": get_capability("comms.customer_notify"),
        }

    async def health(self) -> AdapterHealth:
        state = "available" if self.chat_adapter.chat_enabled else "partial"
        return AdapterHealth(
            adapter_name=self.name,
            family=self.family,
            state=state,
            evidence={
                "chat_enabled": self.chat_adapter.chat_enabled,
                "email_fallback": "available",
                "operating_window_ist": "08:00-22:00",
            },
            checked_at=datetime.now(tz=UTC),
        )

    async def flush_queued_cards(
        self,
        *,
        now: datetime | str | None = None,
    ) -> list[dict[str, Any]]:
        """Flush queued cards once inside the 08:00–22:00 IST operating window."""
        if not is_within_ist_operating_window(now):
            return []
        delivered: list[dict[str, Any]] = []
        while self._queued_cards:
            item = self._queued_cards.pop(0)
            res = await self._deliver_chat_or_email(item)
            delivered.append(res)
        return delivered

    async def _deliver_chat_or_email(self, args: dict[str, Any]) -> dict[str, Any]:
        raw_card = args.get("approval_card")
        card = (
            raw_card
            if isinstance(raw_card, ApprovalCard)
            else ApprovalCard.model_validate(raw_card)
            if isinstance(raw_card, dict)
            else None
        )
        if self.chat_adapter.chat_enabled:
            try:
                payload = (
                    self.chat_adapter.render_approval_card(card)
                    if card is not None
                    else {"text": str(args.get("text", ""))}
                )
                sent = await self.chat_adapter.send_card(payload)
                return {
                    "status": "sent",
                    "channel_used": "chat",
                    "delivery": sent,
                }
            except Exception as exc:
                # SDD §9: Google Chat outage falls back to email channel
                if card is not None:
                    email_rec = self.email_adapter.send_approval_email(
                        card, reason=f"chat_error:{exc}"
                    )
                else:
                    email_rec = self.email_adapter.send_notification_email(
                        recipient=self.email_adapter.default_recipient,
                        subject="[God-View Alert Fallback]",
                        body=str(args.get("text", "")),
                        approval_id="chat-fallback",
                    )
                return {
                    "status": "sent",
                    "channel_used": "email_fallback",
                    "delivery": email_rec,
                }

        # GODVIEW_CHAT_ENABLED=false -> email fallback (Decision A15)
        if card is not None:
            email_rec = self.email_adapter.send_approval_email(card, reason="chat_disabled")
        else:
            email_rec = self.email_adapter.send_notification_email(
                recipient=self.email_adapter.default_recipient,
                subject="[God-View Alert]",
                body=str(args.get("text", "")),
                approval_id="chat-disabled",
            )
        return {
            "status": "sent",
            "channel_used": "email_fallback",
            "delivery": email_rec,
        }

    async def _execute_capability_impl(
        self,
        capability_id: str,
        args: dict[str, Any],
        trust_decision: TrustDecision | None = None,
    ) -> dict[str, Any]:
        if capability_id == "comms.chat_card":
            now = args.get("now")
            urgent_override = bool(args.get("urgent_override", False))
            if not urgent_override and not is_within_ist_operating_window(now):
                self._queued_cards.append(dict(args))
                return {
                    "status": "queued",
                    "window_ist": "08:00-22:00",
                    "queued_count": len(self._queued_cards),
                }
            return await self._deliver_chat_or_email(args)

        if capability_id == "comms.customer_notify":
            if trust_decision is None or not trust_decision.approval_id:
                raise TrustViolation(
                    "comms.customer_notify is capped at L2 and requires an explicit approval_id"
                )
            recipient = str(args.get("email") or self.email_adapter.default_recipient)
            customer_id = str(args.get("customer_id", ""))
            message = str(args.get("message", ""))
            email_rec = self.email_adapter.send_notification_email(
                recipient=recipient,
                subject=f"[Customer Notification] Reference {trust_decision.approval_id}",
                body=message,
                approval_id=trust_decision.approval_id,
                customer_id=customer_id,
            )
            return {
                "status": "sent",
                "channel_used": "email",
                "approval_id": trust_decision.approval_id,
                "delivery": email_rec,
            }

        raise KeyError(f"Unsupported capability '{capability_id}' on {self.name}")


__all__ = [
    "IST",
    "ChannelsSenseAdapter",
    "EmailChannelAdapter",
    "GoogleChatAdapter",
    "is_within_ist_operating_window",
]
