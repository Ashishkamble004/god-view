"""Hermetic unit tests for Google Chat and Email channel adapters (`godview/adapters/channels/`).

Covers TDD §1 (I-06 TrustDecision before mutating calls, L2 cap on customer notifications),
§2.4 (ApprovalCard), §3.4 (/v1/approvals callbacks), §3.7 (comms.chat_card, comms.customer_notify),
§6.2 #2 (08:00–22:00 IST operating window queueing, Approve/Reject/Snooze callbacks, email fallback),
and Coordinator Decision A15 (`GODVIEW_CHAT_ENABLED=false` feature flag).
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from godview_shared.capabilities import get_capability
from godview_shared.contracts import ApprovalCard, TrustDecision, TrustViolation

from godview.adapters.channels import (
    ChannelsSenseAdapter,
    EmailChannelAdapter,
    GoogleChatAdapter,
)

IST = ZoneInfo("Asia/Kolkata")


def _sample_approval_card() -> ApprovalCard:
    return ApprovalCard(
        approval_id="apr-inc-20261016-0003-2",
        incident_id="inc-20261016-0003",
        agent_id="business_line__origination_workflow",
        action_type="biz.reevaluate_application",
        capability="biz.reevaluate_application",
        target_resource="applications WHERE rules_version='a1b2c3d' AND decision='APPROVED'",
        trust_level_required=4,
        narrative="Re-evaluate 212 applications approved under the regressed scoring threshold",
        evidence_pack_id="evp-20261016-0007",
        impact_amount=18400000,
        currency="USD",
        financial_impact=18400000,
        reversal_recipe={"capability": "biz.compensate_journey", "args": {"cohort": "a1b2c3d"}},
        requested_at="2026-10-16T08:36:00Z",
        expires_at="2026-10-16T09:36:00Z",
        channel="chat",
    )


def _make_trust_decision(
    capability_id: str,
    *,
    level: int = 2,
    approval_id: str | None = "apr-inc-20261016-0003-2",
    authorized: bool = True,
) -> TrustDecision:
    spec = get_capability(capability_id)
    return TrustDecision(
        authorized=authorized,
        agent_id="business_line__origination_workflow",
        action_type=capability_id,
        capability_id=capability_id,
        effective_level=level,
        required_level=spec.min_level,
        approval_id=approval_id,
        sa_to_impersonate="godview-act-sa",
        blast_radius_resources=1,
        reason="unit test channel authorization",
        decided_at=datetime.now(tz=UTC),
    )


@pytest.mark.unit
def test_approval_card_renders_google_chat_card_v2_json() -> None:
    """ApprovalCard renders to Google Chat cardsV2 JSON with Approve, Reject, and Snooze callbacks."""
    chat = GoogleChatAdapter(chat_enabled=True)
    card = _sample_approval_card()
    payload = chat.render_approval_card(card)

    assert "cardsV2" in payload
    card_v2 = payload["cardsV2"][0]
    assert card_v2["cardId"] == card.approval_id
    header = card_v2["card"]["header"]
    assert card.incident_id in header["subtitle"]

    serialized = str(payload)
    assert "18,400,000" in serialized or "18400000" in serialized
    assert card.narrative in serialized
    assert card.evidence_pack_id in serialized

    # Verify interactive buttons: Approve, Reject, Snooze
    buttons_section = card_v2["card"]["sections"][-1]
    buttons = buttons_section["widgets"][0]["buttonList"]["buttons"]
    button_texts = [b["text"] for b in buttons]
    assert button_texts == ["Approve", "Reject", "Snooze"]


@pytest.mark.unit
async def test_chat_callback_routes_approve_reject_snooze_to_sovereign_approvals() -> None:
    """Approve/Reject/Snooze callbacks parse Chat interaction events and map to /v1/approvals."""
    forwarded: list[dict[str, object]] = []

    async def fake_sovereign_dispatcher(path: str, body: dict[str, object]) -> dict[str, object]:
        forwarded.append({"path": path, "body": body})
        return {"status": "ok", "path": path, "decision": body["decision"]}

    chat = GoogleChatAdapter(
        chat_enabled=True, approval_callback_dispatcher=fake_sovereign_dispatcher
    )

    for action, expected_suffix, expected_decision in [
        ("approve", ":approve", "approved"),
        ("reject", ":reject", "rejected"),
        ("snooze", ":snooze", "snoozed"),
    ]:
        event = {
            "type": "CARD_CLICKED",
            "eventTime": "2026-10-16T08:40:00Z",
            "user": {"email": "sre-lead@example.com", "displayName": "SRE Lead"},
            "common": {
                "invokedFunction": f"approval_{action}",
                "parameters": {
                    "approval_id": "apr-inc-20261016-0003-2",
                    "incident_id": "inc-20261016-0003",
                    "action": action,
                },
            },
        }
        result = await chat.handle_interaction_callback(event)
        assert result["decision"] == expected_decision
        assert result["approver"]["principal"] == "sre-lead@example.com"
        assert result["approver"]["channel"] == "chat"
        assert str(result["endpoint"]).endswith(
            f"/v1/approvals/apr-inc-20261016-0003-2{expected_suffix}"
        )

    assert len(forwarded) == 3


@pytest.mark.unit
async def test_operating_window_0800_to_2200_ist_queueing_and_flush() -> None:
    """Cards inside 08:00–22:00 IST send immediately; cards outside window queue until 08:00 IST."""
    sent_cards: list[dict[str, object]] = []

    async def fake_sender(payload: dict[str, object]) -> dict[str, object]:
        sent_cards.append(payload)
        return {"message_id": f"spaces/godview/messages/{len(sent_cards)}"}

    email_adapter = EmailChannelAdapter()
    adapter = ChannelsSenseAdapter(
        chat_adapter=GoogleChatAdapter(chat_enabled=True, webhook_sender=fake_sender),
        email_adapter=email_adapter,
    )
    td = _make_trust_decision("comms.chat_card", level=1)
    card = _sample_approval_card()

    # Direction 1: Outside window (23:15 IST -> queued, not sent)
    late_night_ist = datetime(2026, 10, 16, 23, 15, tzinfo=IST)
    res_queued = await adapter.execute_capability(
        "comms.chat_card",
        {"approval_card": card.model_dump(mode="json"), "now": late_night_ist.isoformat()},
        trust_decision=td,
    )
    assert res_queued["status"] == "queued"
    assert res_queued["window_ist"] == "08:00-22:00"
    assert len(sent_cards) == 0
    assert adapter.queued_count == 1

    # Direction 2: Inside window (09:10 IST next morning -> flushes queue and sends immediately)
    morning_ist = datetime(2026, 10, 17, 9, 10, tzinfo=IST)
    flushed = await adapter.flush_queued_cards(now=morning_ist)
    assert len(flushed) == 1
    assert len(sent_cards) == 1
    assert adapter.queued_count == 0


@pytest.mark.unit
async def test_chat_disabled_feature_flag_and_outage_fallback_to_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decision A15 & SDD §9: GODVIEW_CHAT_ENABLED=false or Chat failure routes to email fallback."""
    monkeypatch.setenv("GODVIEW_CHAT_ENABLED", "false")
    email_adapter = EmailChannelAdapter(default_recipient="ops-owner@example.com")
    adapter = ChannelsSenseAdapter.from_env(email_adapter=email_adapter)

    health = await adapter.health()
    assert health.state == "partial"
    assert health.evidence["chat_enabled"] is False
    assert health.evidence["email_fallback"] == "available"

    td = _make_trust_decision("comms.chat_card", level=1)
    card = _sample_approval_card()
    daytime_ist = datetime(2026, 10, 16, 14, 0, tzinfo=IST)

    res = await adapter.execute_capability(
        "comms.chat_card",
        {"approval_card": card.model_dump(mode="json"), "now": daytime_ist.isoformat()},
        trust_decision=td,
    )
    assert res["status"] == "sent"
    assert res["channel_used"] == "email_fallback"
    assert len(email_adapter.sent_emails) == 1
    assert card.approval_id in email_adapter.sent_emails[0]["subject"]


@pytest.mark.unit
async def test_trust_decision_required_and_customer_notify_capped_at_l2() -> None:
    """TDD I-06: comms.chat_card and comms.customer_notify require TrustDecision; customer_notify requires L2 approval_id."""
    adapter = ChannelsSenseAdapter(
        chat_adapter=GoogleChatAdapter(chat_enabled=False),
        email_adapter=EmailChannelAdapter(),
    )

    # Missing TrustDecision raises TrustViolation
    with pytest.raises(TrustViolation):
        await adapter.execute_capability("comms.chat_card", {"text": "Alert"}, trust_decision=None)

    with pytest.raises(TrustViolation):
        await adapter.execute_capability(
            "comms.customer_notify",
            {"customer_id": "cust-001", "message": "Your application status updated"},
            trust_decision=None,
        )

    # Customer comms without explicit human approval_id raises TrustViolation (never whitelisted in v1)
    td_no_approval = _make_trust_decision("comms.customer_notify", level=3, approval_id=None)
    with pytest.raises(TrustViolation, match="approval_id"):
        await adapter.execute_capability(
            "comms.customer_notify",
            {"customer_id": "cust-001", "message": "Your application status updated"},
            trust_decision=td_no_approval,
        )

    # With L2 approval_id succeeds
    td_approved = _make_trust_decision(
        "comms.customer_notify",
        level=2,
        approval_id="apr-inc-20261016-0003-9",
    )
    notify_res = await adapter.execute_capability(
        "comms.customer_notify",
        {"customer_id": "cust-001", "email": "customer@example.com", "message": "Status updated"},
        trust_decision=td_approved,
    )
    assert notify_res["status"] == "sent"
    assert notify_res["approval_id"] == "apr-inc-20261016-0003-9"


@pytest.mark.unit
async def test_chat_callback_rejects_path_traversal_and_missing_approver_email() -> None:
    """Path traversal in approval_id or missing user.email raises ValueError (#110 #11)."""
    chat = GoogleChatAdapter(chat_enabled=True)

    # Path traversal in approval_id
    with pytest.raises(ValueError, match="approval_id"):
        await chat.handle_interaction_callback(
            {
                "user": {"email": "sre-lead@example.com"},
                "common": {
                    "invokedFunction": "approval_approve",
                    "parameters": {
                        "approval_id": "../../admin/secret",
                        "action": "approve",
                    },
                },
            }
        )

    # Missing user.email
    with pytest.raises(ValueError, match="user.email"):
        await chat.handle_interaction_callback(
            {
                "user": {},
                "common": {
                    "invokedFunction": "approval_approve",
                    "parameters": {
                        "approval_id": "apr-inc-20261016-0003-2",
                        "action": "approve",
                    },
                },
            }
        )


@pytest.mark.unit
async def test_chat_httpx_transport_and_missing_webhook_url_error() -> None:
    """send_card and handle_interaction_callback use httpx transport and fail loudly without webhook_url."""
    chat_no_url = GoogleChatAdapter(chat_enabled=True, webhook_url="")
    with pytest.raises(RuntimeError, match="webhook_url"):
        await chat_no_url.send_card({"text": "hello"})

    seen_requests: list[tuple[str, str]] = []

    class _MockAsyncTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            seen_requests.append((request.method, str(request.url)))
            return httpx.Response(200, json={"name": "spaces/s1/messages/m1", "status": "ok"})

    chat_http = GoogleChatAdapter(
        chat_enabled=True,
        webhook_url="https://chat.googleapis.com/v1/spaces/s1/messages?key=k",
        sovereign_base_url="https://sovereign.internal",
        http_transport=_MockAsyncTransport(),
    )
    send_res = await chat_http.send_card({"text": "hello"})
    assert send_res["name"] == "spaces/s1/messages/m1"

    cb_res = await chat_http.handle_interaction_callback(
        {
            "user": {"email": "sre-lead@example.com"},
            "common": {
                "invokedFunction": "approval_approve",
                "parameters": {
                    "approval_id": "apr-inc-20261016-0003-2",
                    "action": "approve",
                },
            },
        }
    )
    assert cb_res["decision"] == "approved"
    assert len(seen_requests) == 2
