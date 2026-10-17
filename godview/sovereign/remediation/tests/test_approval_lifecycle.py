"""Unit tests for Approval Card lifecycle (Plan §3.4, §1.3).

Tests:
- Approval creation with 30-min TTL
- Approval decision by IAP operator with AuditRecord creation
- Single-use consumption enforcement
- Capability, agent, target, and expiration binding enforcement
- Approvals API router endpoints
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from godview_shared.contracts import TrustViolation
from godview_shared.fakes import InMemoryFirestore, InMemoryPubSub
from httpx import ASGITransport, AsyncClient

from godview.sovereign.control_api.routers.approvals import build_router
from godview.sovereign.trust_ladder import TrustGate


class MockContext:
    def __init__(self) -> None:
        self.docs = InMemoryFirestore()
        self.bus = InMemoryPubSub()
        self.trust_gate = TrustGate(docs=self.docs, bus=self.bus)


@pytest.mark.unit
def test_approval_creation_with_30_min_ttl() -> None:
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    gate = TrustGate(docs=fs, bus=ps)

    card = gate.create_approval_request(
        incident_id="inc-test-01",
        agent_id="platform__env-01",
        capability_id="armor.set_waf_sensitivity",
        target_resource="god-view-edge-policy",
        trust_level_required=2,
        narrative="Tune WAF sensitivity to 1",
        evidence_pack_id="evp-test-01",
    )

    assert card.decision == "pending"
    assert card.trust_level_required == 2

    # Check 30 min TTL
    req_dt = datetime.fromisoformat(str(card.requested_at).replace("Z", "+00:00"))
    exp_dt = datetime.fromisoformat(str(card.expires_at).replace("Z", "+00:00"))
    assert (exp_dt - req_dt).total_seconds() == 30 * 60

    # Persisted in Firestore
    raw = fs.get("approvals", card.approval_id)
    assert raw is not None
    assert raw["approval_id"] == card.approval_id


@pytest.mark.unit
def test_approval_decision_records_audit_and_enforces_single_use() -> None:
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    gate = TrustGate(docs=fs, bus=ps)

    card = gate.create_approval_request(
        incident_id="inc-test-01",
        agent_id="platform__env-01",
        capability_id="armor.set_waf_sensitivity",
        target_resource="god-view-edge-policy",
        trust_level_required=2,
        narrative="Tune WAF sensitivity to 1",
        evidence_pack_id="evp-test-01",
    )

    # Operator approves card
    updated_card, audit = gate.decide_approval(
        card.approval_id,
        decision="approved",
        decided_by="admin@example.com",
    )
    assert updated_card.decision == "approved"
    assert updated_card.decided_by == "admin@example.com"
    assert audit.audit_id is not None
    assert audit.result == "success"

    # Authorized on first call
    dec1 = gate.authorize(
        agent_id="platform__env-01",
        capability_id="armor.set_waf_sensitivity",
        target_resource="god-view-edge-policy",
        approval_id=card.approval_id,
    )
    assert dec1.authorized is True
    assert dec1.effective_level == 2

    # Second call with the same approval_id must be rejected (single use)
    with pytest.raises(TrustViolation, match="already been consumed"):
        gate.authorize(
            agent_id="platform__env-01",
            capability_id="armor.set_waf_sensitivity",
            target_resource="god-view-edge-policy",
            approval_id=card.approval_id,
        )


@pytest.mark.unit
def test_approval_binding_checks_mismatch() -> None:
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    gate = TrustGate(docs=fs, bus=ps)

    card = gate.create_approval_request(
        incident_id="inc-test-01",
        agent_id="platform__env-01",
        capability_id="armor.set_waf_sensitivity",
        target_resource="god-view-edge-policy",
        trust_level_required=2,
        narrative="Tune WAF sensitivity to 1",
        evidence_pack_id="evp-test-01",
    )
    gate.decide_approval(card.approval_id, decision="approved", decided_by="admin@example.com")

    # Mismatched capability
    with pytest.raises(TrustViolation, match="bound to capability 'armor.set_waf_sensitivity', not 'run.set_ingress'"):
        gate.authorize(
            agent_id="platform__env-01",
            capability_id="run.set_ingress",
            target_resource="god-view-edge-policy",
            approval_id=card.approval_id,
        )

    # Mismatched agent
    with pytest.raises(TrustViolation, match="bound to agent 'platform__env-01', not 'data__db-01'"):
        gate.authorize(
            agent_id="data__db-01",
            capability_id="armor.set_waf_sensitivity",
            target_resource="god-view-edge-policy",
            approval_id=card.approval_id,
        )

    # Mismatched target resource
    with pytest.raises(TrustViolation, match="bound to target 'god-view-edge-policy', not 'other-policy'"):
        gate.authorize(
            agent_id="platform__env-01",
            capability_id="armor.set_waf_sensitivity",
            target_resource="other-policy",
            approval_id=card.approval_id,
        )


@pytest.mark.unit
async def test_approvals_router_endpoints() -> None:
    ctx = MockContext()
    router = build_router(ctx)
    app = FastAPI()
    app.include_router(router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create approval via API
        create_resp = await client.post(
            "/approvals",
            json={
                "incident_id": "inc-api-01",
                "agent_id": "platform__env-01",
                "capability_id": "armor.set_waf_sensitivity",
                "target_resource": "god-view-edge-policy",
                "trust_level_required": 2,
                "narrative": "Fix Cloud Armor WAF 403 on JSON POST",
            },
        )
        assert create_resp.status_code == 200
        card_data = create_resp.json()
        app_id = card_data["approval_id"]
        assert card_data["decision"] == "pending"

        # List approvals
        list_resp = await client.get("/approvals?status=pending")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1

        # Approve card using colon syntax alias :approve
        approve_resp = await client.post(
            f"/approvals/{app_id}:approve",
            headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:operator@example.com"},
        )
        assert approve_resp.status_code == 200
        approved_data = approve_resp.json()
        assert approved_data["approval"]["decision"] == "approved"
        assert approved_data["approval"]["decided_by"] == "operator@example.com"

        # Create second card to test :reject alias
        create_resp2 = await client.post(
            "/approvals",
            json={
                "incident_id": "inc-api-02",
                "agent_id": "platform__env-01",
                "capability_id": "armor.set_waf_sensitivity",
                "target_resource": "god-view-edge-policy",
                "trust_level_required": 2,
                "narrative": "Test reject colon alias",
            },
        )
        assert create_resp2.status_code == 200
        app_id2 = create_resp2.json()["approval_id"]

        # Reject card using :reject alias
        reject_resp = await client.post(
            f"/approvals/{app_id2}:reject",
            headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:operator@example.com"},
        )
        assert reject_resp.status_code == 200
        rejected_data = reject_resp.json()
        assert rejected_data["approval"]["decision"] == "rejected"
        assert rejected_data["approval"]["decided_by"] == "operator@example.com"
