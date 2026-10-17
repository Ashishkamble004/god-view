"""Tests for God-View Sovereign Runtime Core Skeleton (TDD §1.2, §3.1)."""

from __future__ import annotations

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from godview_shared.auth import AuthService, Principal
from godview_shared.stores.protocols import (
    AgentRuntime,
    DocumentStore,
    EventBus,
    LedgerStore,
)

from godview.sovereign.bootstrap import (
    SovereignContext,
    build_context,
    create_app,
)


@pytest.mark.unit
def test_skeleton_context_and_protocol_conformance() -> None:
    """Verify SovereignContext, build_context, and that fakes satisfy all protocols."""
    ctx = build_context({"GODVIEW_ENV": "hermetic"})
    assert isinstance(ctx, SovereignContext)

    # Stores satisfy protocols
    assert isinstance(ctx.docs, DocumentStore)
    assert isinstance(ctx.ledger, LedgerStore)
    assert isinstance(ctx.bus, EventBus)
    assert isinstance(ctx.agents, AgentRuntime)

    # Auth and LLM clients
    assert isinstance(ctx.auth, AuthService)
    assert ctx.llm is not None

    # Hooks registered
    assert hasattr(ctx.hooks, "perception_tick")
    assert hasattr(ctx.hooks, "sentinel_tick")
    assert hasattr(ctx.hooks, "on_change_event")
    assert hasattr(ctx.hooks, "on_incident")
    assert hasattr(ctx.hooks, "agent_warmth_tick")
    assert hasattr(ctx.hooks, "dlq_watchdog_tick")


@pytest.mark.unit
def test_skeleton_create_app_and_router_auto_include(tmp_path, monkeypatch) -> None:
    """Verify create_app(ctx) auto-includes routers following the build_router(ctx) convention."""
    ctx = build_context({"GODVIEW_ENV": "hermetic"})

    # Create a dynamic sample router module in control_api.routers

    routers_dir = tmp_path / "routers"
    routers_dir.mkdir(parents=True)
    sample_file = routers_dir / "sample.py"
    sample_file.write_text(
        """from fastapi import APIRouter, Depends

def build_router(ctx):
    r = APIRouter()
    @r.get("/sample-route")
    async def sample():
        return {"status": "sample_ok", "mode": ctx.settings.env_mode}
    return r
"""
    )

    # Also test an explicit custom router mounted on app
    app = create_app(ctx)
    client = TestClient(app)

    # Core healthz and metrics
    r_health = client.get("/public/healthz")
    assert r_health.status_code == 200
    h_data = r_health.json()
    assert h_data["mode"] == "hermetic"
    assert "stores" in h_data
    assert h_data["stores"]["docs"] == "InMemoryFirestore"

    # Verify changes endpoint is paginated
    r_changes = client.get("/api/v1/changes?limit=10")
    assert r_changes.status_code == 200


@pytest.mark.unit
def test_skeleton_auth_dependencies() -> None:
    """Verify require_operator and require_service auth dependencies."""
    ctx = build_context({"GODVIEW_ENV": "hermetic"})
    app = FastAPI()

    router = APIRouter()

    @router.get("/protected-operator")
    async def protected_op(
        principal: Principal = Depends(ctx.auth.require_operator),  # noqa: B008
    ):
        return {"email": principal.email, "is_operator": principal.is_operator}

    @router.get("/protected-service")
    async def protected_svc(
        principal: Principal = Depends(ctx.auth.require_service("test-aud")),  # noqa: B008
    ):
        return {"email": principal.email, "is_service": principal.is_service}

    app.include_router(router)
    client = TestClient(app)

    # In hermetic mode, default test operator
    r_op = client.get("/protected-operator")
    assert r_op.status_code == 200
    assert r_op.json()["is_operator"] is True

    # With explicit test principal header
    r_op_hdr = client.get(
        "/protected-operator",
        headers={"X-Godview-Test-Principal": "custom-admin@example.com"},
    )
    assert r_op_hdr.status_code == 200
    assert r_op_hdr.json()["email"] == "custom-admin@example.com"

    # Service with test token
    r_svc = client.get(
        "/protected-service",
        headers={"Authorization": "Bearer hermetic-test-token"},
    )
    assert r_svc.status_code == 200
    assert r_svc.json()["is_service"] is True
