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
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from godview_shared.contracts import ApprovalCard, TrustViolation
from godview_shared.fakes import InMemoryFirestore, InMemoryPubSub
from httpx import ASGITransport, AsyncClient

from godview.sovereign.control_api.routers.approvals import build_router
from godview.sovereign.remediation.registry import RemediationDispatcher
from godview.sovereign.remediation.types import ExecutionResult, VerificationCheck
from godview.sovereign.trust_ladder import TrustGate


class MockContext:
    def __init__(self) -> None:
        self.docs = InMemoryFirestore()
        self.bus = InMemoryPubSub()
        self.trust_gate = TrustGate(docs=self.docs, bus=self.bus)
        self.observed_resources = {"edge-security-policy"}
        self.env_mode = "hermetic"
        mock_armor = MagicMock()
        mock_armor.execute = AsyncMock(
            return_value=ExecutionResult(
                ok=True,
                before={"preview": True},
                after={"preview": False},
                verification=[VerificationCheck(check="waf_rule_enforced_with_sensitivity", passed=True)],
            )
        )
        self.dispatcher = RemediationDispatcher(custom_executors={"armor.set_waf_sensitivity": mock_armor})


@pytest.mark.unit
def test_approval_creation_with_30_min_ttl() -> None:
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    gate = TrustGate(docs=fs, bus=ps)

    card = gate.create_approval_request(
        incident_id="inc-test-01",
        agent_id="platform__env-01",
        capability_id="armor.set_waf_sensitivity",
        target_resource="edge-security-policy",
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
        target_resource="edge-security-policy",
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
        target_resource="edge-security-policy",
        approval_id=card.approval_id,
    )
    assert dec1.authorized is True
    assert dec1.effective_level == 2

    # Second call with the same approval_id must be rejected (single use)
    with pytest.raises(TrustViolation, match="already been consumed"):
        gate.authorize(
            agent_id="platform__env-01",
            capability_id="armor.set_waf_sensitivity",
            target_resource="edge-security-policy",
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
        target_resource="edge-security-policy",
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
            target_resource="edge-security-policy",
            approval_id=card.approval_id,
        )

    # Mismatched agent
    with pytest.raises(TrustViolation, match="bound to agent 'platform__env-01', not 'data__db-01'"):
        gate.authorize(
            agent_id="data__db-01",
            capability_id="armor.set_waf_sensitivity",
            target_resource="edge-security-policy",
            approval_id=card.approval_id,
        )

    # Mismatched target resource
    with pytest.raises(TrustViolation, match="bound to target 'edge-security-policy', not 'other-policy'"):
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
                "target_resource": "edge-security-policy",
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
                "target_resource": "edge-security-policy",
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


@pytest.mark.unit
async def test_approval_decided_hook_invoked() -> None:
    ctx = MockContext()
    ctx.env_mode = "hermetic"
    hook_called = []

    async def hook_listener(card: ApprovalCard) -> None:
        hook_called.append(card.approval_id)

    class Hooks:
        on_approval_decided = [hook_listener]

    ctx.hooks = Hooks()

    app = FastAPI()
    app.include_router(build_router(ctx))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_resp = await client.post(
            "/approvals",
            headers={"X-Goog-Authenticated-User-Email": "operator@example.com"},
            json={
                "incident_id": "inc-hook-001",
                "agent_id": "network_specialist",
                "capability_id": "armor.set_waf_sensitivity",
                "target_resource": "edge-security-policy",
                "trust_level_required": 2,
                "narrative": "Hook test",
            },
        )
        app_id = create_resp.json()["approval_id"]

        await client.post(
            f"/approvals/{app_id}:reject",
            headers={"X-Goog-Authenticated-User-Email": "operator@example.com"},
        )
        assert app_id in hook_called


@pytest.mark.unit
async def test_approval_trust_credit_order_and_bad_outcome_demotion() -> None:
    """Verifier-core #129 item 5: credit awarded only after verified execution; failure demotes via record_bad_outcome."""
    ctx = MockContext()
    ctx.env_mode = "hermetic"

    # Pre-promote agent to L2
    rec = ctx.trust_gate.get_or_create_record("platform__env-01", "armor.set_waf_sensitivity")
    ctx.trust_gate._transition(rec, 2, "pre-promoted")

    # Case 1: Execution fails verification -> bad outcome recorded, demoted to L1
    failing_armor = MagicMock()
    failing_armor.execute = AsyncMock(
        return_value=ExecutionResult(
            ok=False,
            before={"sensitivity": 4},
            after={"sensitivity": 1},
            verification=[VerificationCheck(check="probe", passed=False)],
            error="Canary probe timed out",
        )
    )
    ctx.dispatcher = RemediationDispatcher()
    ctx.dispatcher.register_executor("armor.set_waf_sensitivity", failing_armor)

    app = FastAPI()
    app.include_router(build_router(ctx))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create card
        create_resp = await client.post(
            "/approvals",
            headers={"X-Goog-Authenticated-User-Email": "operator@example.com"},
            json={
                "incident_id": "inc-bad-01",
                "agent_id": "platform__env-01",
                "capability_id": "armor.set_waf_sensitivity",
                "target_resource": "edge-security-policy",
                "trust_level_required": 2,
                "narrative": "Failing remediation",
            },
        )
        card_id = create_resp.json()["approval_id"]

        # Approve card
        approve_resp = await client.post(
            f"/approvals/{card_id}:approve",
            headers={"X-Goog-Authenticated-User-Email": "operator@example.com"},
        )
        assert approve_resp.status_code == 200
        # Demoted to L1 due to bad outcome
        rec_after = ctx.trust_gate.get_or_create_record("platform__env-01", "armor.set_waf_sensitivity")
        assert rec_after.level == 1
        assert rec_after.counters.regressions == 1
        assert rec_after.counters.approved_executions == 0
