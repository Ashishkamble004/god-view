"""Unit tests for Capabilities router, RemediationDispatcher, and Operator router (Plan §3.4)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from godview_shared.fakes import InMemoryAgentEngine, InMemoryFirestore, InMemoryPubSub
from httpx import ASGITransport, AsyncClient

from godview.sovereign.control_api.routers.capabilities import (
    build_router as build_capabilities_router,
)
from godview.sovereign.control_api.routers.operator import (
    build_router as build_operator_router,
)
from godview.sovereign.remediation import (
    BaseExecutor,
    ExecutionResult,
    RemediationDispatcher,
    VerificationCheck,
)
from godview.sovereign.trust_ladder import TrustGate


class MockContext:
    def __init__(self) -> None:
        self.docs = InMemoryFirestore()
        self.bus = InMemoryPubSub()
        self.agents = InMemoryAgentEngine()
        self.trust_gate = TrustGate(docs=self.docs, bus=self.bus)
        self.dispatcher = RemediationDispatcher()
        self.env_mode = "hermetic"


class DummyExecutor(BaseExecutor):
    def __init__(self, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.executed = False

    async def execute(
        self,
        ctx: Any,
        args: dict[str, Any],
        target_resource: str,
        **kwargs: Any,
    ) -> ExecutionResult:
        self.executed = True
        return ExecutionResult(
            ok=self.succeeds,
            before={"count": 0},
            after={"count": 1} if self.succeeds else {"count": 0},
            verification=[
                VerificationCheck(
                    check="dummy_check",
                    passed=self.succeeds,
                    evidence={"success": self.succeeds},
                )
            ],
            reverted=not self.succeeds,
            error=None if self.succeeds else "Execution check failed",
        )


@pytest.mark.unit
async def test_capabilities_invoke_success_and_trust_record_update() -> None:
    ctx = MockContext()
    dummy_exec = DummyExecutor(succeeds=True)
    ctx.dispatcher.register_executor("armor.set_waf_sensitivity", dummy_exec)

    # Pre-populate discovered_services so target is observed
    ctx.docs.set(
        "discovered_services",
        "edge-security-policy",
        {"name": "edge-security-policy", "kind": "security_policy", "labels": {}},
    )

    # Create & approve an approval card
    card = ctx.trust_gate.create_approval_request(
        incident_id="inc-test-01",
        agent_id="platform__env-01",
        capability_id="armor.set_waf_sensitivity",
        target_resource="edge-security-policy",
        trust_level_required=2,
        narrative="Tune WAF sensitivity",
        evidence_pack_id="evp-01",
        args={"sensitivity": 1},
    )
    ctx.trust_gate.decide_approval(card.approval_id, decision="approved", decided_by="admin@example.com")

    app = FastAPI()
    app.include_router(build_capabilities_router(ctx))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/capabilities/invoke",
            json={
                "capability_id": "armor.set_waf_sensitivity",
                "agent_id": "platform__env-01",
                "target_resource": "edge-security-policy",
                "approval_id": card.approval_id,
                "args": {"sensitivity": 1},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert dummy_exec.executed is True

        # TrustGate record updated: approved_executions incremented
        rec = ctx.trust_gate.get_or_create_record("platform__env-01", "armor.set_waf_sensitivity")
        assert rec.counters.approved_executions == 1


@pytest.mark.unit
async def test_capabilities_invoke_unregistered_raises_501_never_echo() -> None:
    ctx = MockContext()
    app = FastAPI()
    app.include_router(build_capabilities_router(ctx))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/capabilities/invoke",
            json={
                "capability_id": "nonexistent.capability",
                "agent_id": "platform__env-01",
                "target_resource": "res-01",
            },
        )
        # HTTP 501 Not Implemented (never echo {"ok": true, "echo_args": ...})
        assert resp.status_code == 501
        assert "No executor registered" in resp.json()["detail"]


@pytest.mark.unit
async def test_operator_kill_switch_lifecycle() -> None:
    ctx = MockContext()
    ctx.env_mode = "hermetic"
    ctx.discovered_services = [
        {"name": "edge-security-policy", "kind": "security_policy", "labels": {}}
    ]
    mock_armor = MagicMock()
    mock_armor.set_rule_preview = AsyncMock(
        return_value=ExecutionResult(
            ok=True,
            before={"preview": True},
            after={"preview": False},
            verification=[VerificationCheck(check="preview_toggled", passed=True)],
        )
    )
    ctx.armor_executor = mock_armor

    app = FastAPI()
    app.include_router(build_operator_router(ctx))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Engage kill switch
        resp = await client.post(
            "/operator/kill-switch",
            json={"enabled": True, "reason": "Severe attack"},
            headers={"X-Goog-Authenticated-User-Email": "operator@example.com"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["kill_switch_enabled"] is True
        assert ctx.trust_gate.global_kill_switch is True

        # State persisted in operator_state
        state_resp = await client.get(
            "/operator/state",
            headers={"X-Goog-Authenticated-User-Email": "operator@example.com"},
        )
        assert state_resp.status_code == 200
        assert state_resp.json()["kill_switch_enabled"] is True

        # New approval creation is auto-expired while kill switch is active
        card = ctx.trust_gate.create_approval_request(
            incident_id="inc-test-02",
            agent_id="platform__env-01",
            capability_id="armor.set_waf_sensitivity",
            target_resource="edge-security-policy",
            trust_level_required=2,
            narrative="Test during kill switch",
            evidence_pack_id="evp-02",
        )
        assert card.decision == "expired"

        # 2. Attempting to target observer control policy must be refused (safety non-negotiable)
        forbid_resp = await client.post(
            "/operator/kill-switch",
            json={"enabled": True, "security_policy": "god-view-control-policy"},
            headers={"X-Goog-Authenticated-User-Email": "operator@example.com"},
        )
        assert forbid_resp.status_code == 403

        # 3. Disengage kill switch
        resp_off = await client.post(
            "/operator/kill-switch",
            json={"enabled": False},
            headers={"X-Goog-Authenticated-User-Email": "operator@example.com"},
        )
        assert resp_off.status_code == 200
        assert resp_off.json()["kill_switch_enabled"] is False
        assert ctx.trust_gate.global_kill_switch is False


@pytest.mark.unit
async def test_operator_pause_resume_hibernate() -> None:
    ctx = MockContext()
    ctx.env_mode = "hermetic"
    ctx.discovered_admin_actions = {
        "tenant.pause": {"action_name": "tenant.pause", "operation_id": "pause", "method": "POST", "url": "https://api.example.com/admin/pause"},
        "tenant.resume": {"action_name": "tenant.resume", "operation_id": "resume", "method": "POST", "url": "https://api.example.com/admin/resume"},
    }
    # Deploy an agent to verify hibernate scales it down
    ctx.agents.deploy_agent("application__svc-01", "application", min_instances=1)

    app = FastAPI()
    app.include_router(build_operator_router(ctx))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Pause
        pause_resp = await client.post(
            "/operator/pause",
            json={"reason": "Operator maintenance"},
            headers={"X-Goog-Authenticated-User-Email": "operator@example.com"},
        )
        assert pause_resp.status_code == 200
        assert pause_resp.json()["tenant_paused"] is True

        # State check
        state = (await client.get("/operator/state", headers={"X-Goog-Authenticated-User-Email": "operator@example.com"})).json()
        assert state["tenant_paused"] is True

        # Resume
        resume_resp = await client.post(
            "/operator/resume",
            json={"reason": "Operator resumed"},
            headers={"X-Goog-Authenticated-User-Email": "operator@example.com"},
        )
        assert resume_resp.status_code == 200
        assert resume_resp.json()["tenant_paused"] is False

        # Hibernate
        hib_resp = await client.post(
            "/operator/hibernate",
            json={"enabled": True},
            headers={"X-Goog-Authenticated-User-Email": "operator@example.com"},
        )
        assert hib_resp.status_code == 200
        assert hib_resp.json()["hibernate_enabled"] is True

        # Agent min_instances cooled to 0
        agent_doc = ctx.agents.get_agent("application__svc-01")
        assert agent_doc["deployment_spec"]["min_instances"] == 0


@pytest.mark.unit
async def test_gcp_mode_rejects_spoofed_auth_headers() -> None:
    """In GCP mode without valid IAP/OIDC verification, header fallback is rejected with 401."""
    ctx = MockContext()
    ctx.env_mode = "gcp"

    app = FastAPI()
    app.include_router(build_operator_router(ctx))
    app.include_router(build_capabilities_router(ctx))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Operator route rejects spoofed header in GCP mode
        resp_op = await client.post(
            "/operator/kill-switch",
            json={"enabled": True},
            headers={"X-Goog-Authenticated-User-Email": "attacker@evil.com"},
        )
        assert resp_op.status_code == 401

        # Capabilities route rejects spoofed header in GCP mode
        resp_cap = await client.post(
            "/capabilities/invoke",
            json={"capability_id": "logging.query", "agent_id": "operator", "target_resource": "res"},
            headers={"X-Goog-Authenticated-User-Email": "attacker@evil.com"},
        )
        assert resp_cap.status_code == 401
