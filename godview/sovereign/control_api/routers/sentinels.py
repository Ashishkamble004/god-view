"""Control API router for sentinels and synthetic probe execution (`/sentinels`, `/probes/run`).

Endpoints:
- GET /sentinels (list registered sentinels and check definitions)
- POST /sentinels (register a new sentinel definition or compile from invariant)
- PATCH /sentinels/{sentinel_id} (update sentinel lifecycle state: shadow, enforced, paused, retired)
- POST /probes/run (service-authenticated manual evaluation cycle)
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from godview_shared.contracts import SentinelDefinition, SentinelState
from pydantic import BaseModel, Field, ValidationError

from godview.sovereign.sentinel_engine import SentinelEngine


class SentinelPatchRequest(BaseModel):
    """Payload for updating a sentinel's lifecycle state."""

    state: SentinelState = Field(
        description="Target lifecycle state: shadow, enforced, paused, retired"
    )


class ProbeRunRequest(BaseModel):
    """Optional payload when manually triggering a probe/sentinel run."""

    source: str = Field(default="service_call", description="Triggering caller identity")
    action: str | None = Field(default=None, description="Optional action name")
    sentinel_id: str | None = Field(default=None, description="Optional target sentinel ID")


def _get_env_mode(ctx: Any) -> str:
    settings = getattr(ctx, "settings", None)
    if settings is not None and hasattr(settings, "env_mode"):
        return str(settings.env_mode)
    if hasattr(ctx, "env_mode") and ctx.env_mode:
        return str(ctx.env_mode)
    import os

    return os.environ.get("GODVIEW_ENV", "hermetic")


def _get_service_auth_dep(ctx: Any) -> Callable[..., Any]:
    """Retrieve require_service auth dependency from SovereignContext."""
    auth = getattr(ctx, "auth", None)
    if auth is not None and hasattr(auth, "require_service"):
        try:
            return auth.require_service()
        except TypeError:
            return auth.require_service

    async def _unauthenticated_service(request: Request) -> Any:
        env_mode = _get_env_mode(ctx)
        test_p = request.headers.get("x-godview-test-principal")
        if env_mode == "hermetic":
            return {"email": test_p or "service@godview.internal", "role": "service"}
        if test_p:
            raise HTTPException(status_code=401, detail="Header spoofing prohibited outside hermetic mode")
        raise HTTPException(
            status_code=401, detail="Unauthorized: authenticated service identity required"
        )

    return _unauthenticated_service


def _get_operator_dep(ctx: Any) -> Callable[..., Any]:
    """Retrieve require_operator auth dependency from SovereignContext."""
    auth = getattr(ctx, "auth", None)
    if auth is not None and hasattr(auth, "require_operator"):
        return auth.require_operator

    async def _unauthenticated_operator(request: Request) -> Any:
        env_mode = _get_env_mode(ctx)
        test_p = request.headers.get("x-godview-test-principal")
        if env_mode == "hermetic":
            return {"email": test_p or "operator@godview.internal", "role": "operator"}
        if test_p:
            raise HTTPException(status_code=401, detail="Header spoofing prohibited outside hermetic mode")
        raise HTTPException(
            status_code=401, detail="Unauthorized: authenticated operator identity required"
        )

    return _unauthenticated_operator


def build_router(ctx: Any) -> APIRouter:
    """Build and return FastAPI router for `/sentinels` and `/probes/run`."""
    router = APIRouter(tags=["sentinels"])

    # Resolve SentinelEngine
    engine: SentinelEngine | None = getattr(ctx, "sentinel_engine", None)
    if engine is None:
        from godview.sovereign.sentinel_engine import install

        engine = install(ctx)

    service_dep = _get_service_auth_dep(ctx)
    operator_dep = _get_operator_dep(ctx)

    @router.get("/sentinels")
    async def get_sentinels() -> dict[str, Any]:
        """List all registered sentinels."""
        return {
            "sentinels": [
                {**s.model_dump(mode="json", by_alias=True), "id": s.sentinel_id}
                for s in engine.list_sentinels()
            ]
        }

    @router.post("/sentinels", dependencies=[Depends(operator_dep)])
    async def create_sentinel(payload: dict[str, Any]) -> dict[str, Any]:
        """Register a new sentinel or compile from an invariant reference."""
        fs = getattr(ctx, "docs", None) or getattr(engine, "_firestore", None)
        if "invariant_id" in payload and "sentinel_id" not in payload:
            inv_id = str(payload.get("invariant_id") or "").strip()
            if not inv_id:
                raise HTTPException(status_code=422, detail="invariant_id must not be empty")

            inv_doc = fs.get("invariants", inv_id) if fs and hasattr(fs, "get") else {}
            inv_doc = inv_doc or {}
            slug = inv_id.removeprefix("inv-")
            check_cfg = inv_doc.get("check") if isinstance(inv_doc.get("check"), dict) else {}
            wake_cfg = inv_doc.get("wake_condition") if isinstance(inv_doc.get("wake_condition"), dict) else {}
            stmt = str(
                check_cfg.get("statement")
                or "SELECT COUNT(*) AS violations FROM events WHERE http_status >= 500"
            )
            dialect = str(check_cfg.get("dialect") or "bigquery")
            target_agent = str(
                wake_cfg.get("target_agent_id")
                or payload.get("target_agent_id")
                or "business_line__inferred"
            )
            snt = SentinelDefinition.model_validate(
                {
                    "sentinel_id": f"snt-invariant_sql-{slug}",
                    "class": "invariant_sql",
                    "source_ref": inv_id,
                    "check": {"dialect": dialect, "statement": stmt, "window_s": 300},
                    "cadence_s": 10,
                    "baseline": {"model": "same_hour", "window_days": 7},
                    "wake_condition": {
                        "operator": str(wake_cfg.get("operator") or ">="),
                        "threshold": float(wake_cfg.get("threshold") or 1.0),
                        "consecutive": int(wake_cfg.get("consecutive") or 2),
                        "window_s": 300,
                        "target_agent_id": target_agent,
                    },
                    "target_agent_id": target_agent,
                    "state": "shadow",
                    "compiled_by": target_agent,
                    "compiled_at": datetime.now(tz=UTC).isoformat(),
                }
            )
        else:
            try:
                snt = SentinelDefinition.model_validate(payload)
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=exc.errors()) from exc

        saved = engine.register_sentinel(snt)
        return {**saved.model_dump(mode="json", by_alias=True), "id": saved.sentinel_id}

    @router.patch("/sentinels/{sentinel_id}", dependencies=[Depends(operator_dep)])
    async def patch_sentinel(
        sentinel_id: str,
        body: SentinelPatchRequest,
    ) -> dict[str, Any]:
        """Update a sentinel's lifecycle state (shadow, enforced, paused, retired)."""
        try:
            updated = engine.update_sentinel_state(sentinel_id, body.state)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {**updated.model_dump(mode="json", by_alias=True), "id": updated.sentinel_id}

    @router.post("/probes/run", dependencies=[Depends(service_dep)])
    async def run_probes(
        body: ProbeRunRequest | None = None,
    ) -> dict[str, Any]:
        """Trigger an immediate evaluation cycle across registered sentinels and probes."""
        now_iso = datetime.now(tz=UTC).isoformat()
        req_source = body.source if body else "service_call"
        req_action = (body.action if body and body.action else None) or "probe_run"

        docs_store = getattr(ctx, "docs", None)
        op_state = None
        if docs_store is not None:
            try:
                op_state = docs_store.get("operator_state", "current")
            except Exception:
                pass
        if not op_state and hasattr(ctx, "operator_state"):
            op_state = ctx.operator_state
        trust_gate = getattr(ctx, "trust_gate", None)
        is_paused = (
            (
                op_state
                and (
                    op_state.get("tenant_paused")
                    or op_state.get("kill_switch_enabled")
                    or op_state.get("hibernate_enabled")
                )
            )
            or (trust_gate and getattr(trust_gate, "global_kill_switch", False))
        )
        if is_paused:
            return {
                "status": "skipped",
                "action": req_action,
                "reason": "paused_or_hibernating",
                "sentinels_evaluated": 0,
                "wakes_triggered": 0,
                "checked_at": now_iso,
            }

        tick = await engine.evaluate_tick()
        return {
            "status": "ok",
            "action": req_action,
            "source": req_source,
            "sentinels_evaluated": tick.evaluated_count,
            "wakes_triggered": len(tick.wakes_sent),
            "checked_at": now_iso,
        }

    return router
