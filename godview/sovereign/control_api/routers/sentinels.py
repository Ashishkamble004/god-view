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

from fastapi import APIRouter, Depends, HTTPException
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
    sentinel_id: str | None = Field(default=None, description="Optional target sentinel ID")


def _get_service_auth_dep(ctx: Any) -> Callable[..., Any]:
    """Retrieve require_service auth dependency from SovereignContext if configured."""
    auth = getattr(ctx, "auth", None)
    if auth is not None and hasattr(auth, "require_service"):
        try:
            return auth.require_service(audience="godview")
        except TypeError:
            return auth.require_service

    async def _optional_service() -> dict[str, str]:
        return {"email": "godview-probe-sa@godview.internal", "role": "service"}

    return _optional_service


def build_router(ctx: Any) -> APIRouter:
    """Build and return FastAPI router for `/sentinels` and `/probes/run`."""
    router = APIRouter(tags=["sentinels"])

    # Resolve SentinelEngine
    engine: SentinelEngine | None = getattr(ctx, "sentinel_engine", None)
    if engine is None:
        engine = SentinelEngine(
            ctx=ctx,
            firestore=getattr(ctx, "docs", None),
            bigquery=getattr(ctx, "ledger", None),
            pubsub=getattr(ctx, "bus", None),
            agent_engine=getattr(ctx, "agents", None),
            clock=getattr(ctx, "clock", None),
        )

    service_dep = _get_service_auth_dep(ctx)

    @router.get("/sentinels")
    async def get_sentinels() -> dict[str, Any]:
        """List all registered sentinels."""
        return {
            "sentinels": [
                {**s.model_dump(mode="json", by_alias=True), "id": s.sentinel_id}
                for s in engine.list_sentinels()
            ]
        }

    @router.post("/sentinels")
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

    @router.patch("/sentinels/{sentinel_id}")
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
        tick = await engine.evaluate_tick()
        return {
            "status": "ok",
            "action": "probe_run",
            "source": req_source,
            "sentinels_evaluated": tick.evaluated_count,
            "wakes_triggered": len(tick.wakes_sent),
            "checked_at": now_iso,
        }

    return router
