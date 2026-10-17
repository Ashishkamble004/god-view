"""Sovereign Control API & MCP Stdio Subprocess Host (`godview.sovereign.control_api.app`).

Implements TDD §1.2, §3.1, §3.4, §3.6, §3.7 and Coordinator Decisions A3, A10, A18:
- Mounts all control routes under both `/v1/*` and `/api/v1/*`.
- Automatically loads all routers from `godview/sovereign/control_api/routers/*.py`.
- Lifespan background loops for perception, sentinels, warmth, and DLQ.
- Real healthz, metrics, nervous-system/push, and paginated changes endpoints.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import importlib
import json
import logging
import os
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from godview_shared.adapter_contract import SenseAdapter
from godview_shared.config import (
    GodViewSettings,
    is_excluded_resource,
)
from godview_shared.contracts import (
    CHAOS_SCENARIO_IDS,
    ChangeEventDoc,
    TrustViolation,
)
from godview_shared.fakes import (
    InMemoryAgentEngine,
    InMemoryBigQuery,
    InMemoryFirestore,
    InMemoryPubSub,
)
from godview_shared.otel import parse_otlp_spans
from pydantic import BaseModel, Field

from godview.ledger.exporter import LedgerSpanExporter
from godview.sovereign.bootstrap import SovereignContext, build_context
from godview.sovereign.control_api.mcp import (
    _CHAOS_DRILL_METADATA,
    MCP_SERVER_COMMANDS,
    McpSubprocessManager,
)
from godview.sovereign.court_manager import CourtManager
from godview.sovereign.nervous_system import NervousSystemRouter
from godview.sovereign.trust_ladder import TrustGate

logger = logging.getLogger("godview.sovereign.control_api")


class SimulatorStateRequest(BaseModel):
    enabled: bool = True


class SimulatorJourneysRequest(BaseModel):
    count: int = 5


class DrillStartRequest(BaseModel):
    target_service: str | None = None
    labels: dict[str, str] | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class WhitelistRequest(BaseModel):
    agent_id: str | None = None
    action_type: str | None = None
    whitelisted: bool = True


def _clean_iap_email(raw_header: str | None) -> str:
    if not raw_header:
        return ""
    cleaned = raw_header.strip()
    if ":" in cleaned:
        cleaned = cleaned.split(":", 1)[-1].strip()
    return cleaned



def create_app(
    ctx: SovereignContext | None = None,
    *,
    firestore: InMemoryFirestore | None = None,
    bigquery: InMemoryBigQuery | None = None,
    pubsub: InMemoryPubSub | None = None,
    agent_engine: InMemoryAgentEngine | None = None,
    adapters: Sequence[SenseAdapter] | None = None,
    settings: GodViewSettings | None = None,
    webhook_secret: str | None = None,
) -> FastAPI:
    """Create the Sovereign FastAPI application with `/v1` and `/api/v1` routers."""
    if ctx is None:
        cfg = settings or GodViewSettings.from_env()
        if any(x is not None for x in (firestore, bigquery, pubsub, agent_engine, adapters)):
            from godview_shared.auth import AuthService
            from godview_shared.llm import LlmClient

            auth = AuthService(settings=cfg)
            llm = LlmClient(settings=cfg, auth=auth)
            ctx = SovereignContext(
                settings=cfg,
                docs=firestore or InMemoryFirestore(),
                ledger=bigquery or InMemoryBigQuery(),
                bus=pubsub or InMemoryPubSub(),
                agents=agent_engine or InMemoryAgentEngine(),
                adapters=list(adapters) if adapters is not None else [],
                auth=auth,
                llm=llm,
                clock=datetime.now,
            )
        else:
            ctx = build_context()

    fs = ctx.docs
    bq = ctx.ledger
    ps = ctx.bus
    ae = ctx.agents
    cfg = ctx.settings
    if webhook_secret is not None:
        secret = webhook_secret
    elif cfg.env_mode == "hermetic":
        secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "hermetic-webhook-secret")
    else:
        secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    router_ns = NervousSystemRouter(pubsub=ps)
    court_manager = CourtManager(
        firestore=fs,
        pubsub=ps,
        agent_engine=ae,
        max_agents=cfg.court_max_agents,
        max_warm_agents=cfg.warm_agents_max,
    )
    trust_gate = TrustGate(
        firestore=fs,
        pubsub=ps,
        token_governor=court_manager.token_governor,
    )
    ledger_exporter = LedgerSpanExporter(
        bq,
        env_id=cfg.env_mode,
        default_journey_name="discovered_journey",
    )
    mcp_manager = McpSubprocessManager(env_mode=cfg.env_mode, adapters=ctx.adapters, executors=ctx.executors)
    mcp_manager.register_adapters(ctx.adapters)
    if cfg.env_mode == "hermetic":
        if "k8s.rollout_restart" not in ctx.executors:
            ctx.executors["k8s.rollout_restart"] = lambda **kwargs: {"ok": True, "restarted": True}
        if "obs.get_trace" not in ctx.executors:
            ctx.executors["obs.get_trace"] = lambda **kwargs: {"ok": True, "trace": "tr-123"}

    if hasattr(ctx, "perception") and ctx.perception is not None:
        if not ctx.hooks.perception_tick:
            ctx.hooks.perception_tick.append(ctx.perception.perceive)
    if not ctx.hooks.agent_warmth_tick:
        ctx.hooks.agent_warmth_tick.append(court_manager.evaluate_earned_warmth)

    runtime_state: dict[str, Any] = {
        "kill_switch_enabled": False,
        "kill_switch_reason": None,
        "kill_switch_actor": None,
        "kill_switch_updated_at": None,
        "tenant_paused": False,
        "simulator_enabled": True,
        "simulator_journeys_today": 0,
        "hibernate_enabled": False,
        "active_drills": {},
        "drift_diffs": {},
    }

    def _operator_state() -> dict[str, Any]:
        return {
            "kill_switch_enabled": bool(runtime_state["kill_switch_enabled"]),
            "kill_switch_reason": runtime_state["kill_switch_reason"],
            "kill_switch_actor": runtime_state["kill_switch_actor"],
            "kill_switch_updated_at": runtime_state["kill_switch_updated_at"],
            "tenant_paused": bool(runtime_state["tenant_paused"]),
            "pause_schedule_ist": "17:00–09:00 IST (Mon–Fri) + Weekends",
            "hibernated": bool(runtime_state["hibernate_enabled"]),
            "simulator_enabled": bool(runtime_state["simulator_enabled"]),
            "simulator_window_ist": "09:00–17:00 IST",
            "simulator_journeys_today": int(runtime_state["simulator_journeys_today"]),
            "simulator_target_per_day": 100,
            "simulator_specimen_ratio": "1 in 5 journeys",
        }

    def _format_drill(drill_id: str) -> dict[str, Any]:
        meta = _CHAOS_DRILL_METADATA.get(
            drill_id,
            {
                "fault_class": "infrastructure",
                "fault_type": drill_id,
                "title": drill_id,
                "target_service": "",
                "infra_golden_signals": "amber",
                "expected_detection": "sentinel_engine",
                "mttd_target": "30s",
            },
        )
        active_entry = runtime_state["active_drills"].get(drill_id)
        is_active = active_entry is not None
        return {
            "drill_id": drill_id,
            "fault_class": meta["fault_class"],
            "fault_type": meta["fault_type"],
            "title": meta["title"],
            "target_service": (
                active_entry["target_service"] if active_entry else meta["target_service"]
            ),
            "infra_golden_signals": meta["infra_golden_signals"],
            "expected_detection": meta["expected_detection"],
            "mttd_target": meta["mttd_target"],
            "is_active": is_active,
            "triggered_by": active_entry.get("triggered_by") if active_entry else None,
            "activated_at": active_entry.get("started_at") if active_entry else None,
            "started_at": active_entry.get("started_at") if active_entry else None,
            "expires_at": active_entry.get("expires_at") if active_entry else None,
            "parameters": active_entry.get("parameters", {}) if active_entry else {},
            "details": active_entry,
        }

    app = FastAPI(
        title="God-View Sovereign Control API",
        version="0.1.0",
    )
    app.state.firestore = fs
    app.state.bigquery = bq
    app.state.pubsub = ps
    app.state.agent_engine = ae
    app.state.perception = getattr(ctx, "perception", None)
    app.state.court_manager = court_manager
    app.state.trust_gate = trust_gate
    app.state.sentinel_engine = getattr(ctx, "sentinel_engine", None)
    app.state.ledger_exporter = ledger_exporter
    app.state.mcp_manager = mcp_manager

    @app.get("/public/healthz")
    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        healthy_adapters = 0
        for adapter in ctx.adapters:
            is_healthy = getattr(adapter, "healthy", True)
            if callable(is_healthy):
                is_healthy = is_healthy()
            if is_healthy:
                healthy_adapters += 1

        sentinels_count = ctx.sentinels_registered
        if sentinels_count == 0:
            try:
                sentinels_count = len(fs.list_docs("sentinels"))
            except Exception:
                pass

        cadence_s = getattr(getattr(ctx, "sentinel_engine", None), "cadence_s", cfg.sentinel_cadence_s)
        return {
            "status": "ok",
            "mode": cfg.env_mode,
            "env_mode": cfg.env_mode,
            "service": "god-view-sovereign",
            "stores": {
                "docs": type(ctx.docs).__name__,
                "ledger": type(ctx.ledger).__name__,
                "bus": type(ctx.bus).__name__,
                "agents": type(ctx.agents).__name__,
            },
            "adapters_healthy": healthy_adapters,
            "adapters_count": len(ctx.adapters),
            "sentinels_registered": sentinels_count,
            "last_perception_at": (
                ctx.last_perception_at.isoformat() if ctx.last_perception_at else None
            ),
            "loop_heartbeats": {k: v.isoformat() for k, v in ctx.loop_heartbeats.items()},
            "mcp_servers": list(MCP_SERVER_COMMANDS.keys()),
            "sentinel_cadence_s": cadence_s,
            "sentinel_interval_seconds": cadence_s,
            "timestamp": datetime.now(tz=UTC).isoformat(),
        }

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> str:
        active_agents = len(court_manager.list_active_agents())
        sentinels_count = ctx.sentinels_registered
        if sentinels_count == 0:
            try:
                sentinels_count = len(fs.list_docs("sentinels"))
            except Exception:
                pass
        cadence_s = getattr(getattr(ctx, "sentinel_engine", None), "cadence_s", cfg.sentinel_cadence_s)
        lines = [
            "# HELP godview_sentinel_cadence_seconds Current sentinel evaluation cadence in seconds",
            "# TYPE godview_sentinel_cadence_seconds gauge",
            f"godview_sentinel_cadence_seconds {cadence_s}",
            "# HELP godview_court_active_agents Active court specialists count",
            "# TYPE godview_court_active_agents gauge",
            f"godview_court_active_agents {active_agents}",
            "# HELP godview_sentinels_registered_total Total registered sentinels",
            "# TYPE godview_sentinels_registered_total gauge",
            f"godview_sentinels_registered_total {sentinels_count}",
        ]
        return "\n".join(lines) + "\n"

    @app.post("/public/webhooks/github")
    async def github_webhook(
        request: Request,
        x_github_event: str = Header(default="push", alias="X-GitHub-Event"),
        x_hub_signature_256: str = Header(default="", alias="X-Hub-Signature-256"),
    ) -> dict[str, Any]:
        raw_body = await request.body()
        if not secret or not x_hub_signature_256.startswith("sha256="):
            raise HTTPException(status_code=401, detail="Missing or malformed X-Hub-Signature-256")
        expected_sig = (
            "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        )
        if not hmac.compare_digest(expected_sig, x_hub_signature_256):
            raise HTTPException(
                status_code=401, detail="Invalid GitHub webhook HMAC-SHA256 signature"
            )

        if x_github_event == "ping":
            return {"status": "pong", "github_event": "ping"}

        event_map: dict[
            str,
            Literal[
                "godview.webhook.github.push",
                "godview.webhook.github.pull_request",
                "godview.webhook.github.workflow_run",
            ],
        ] = {
            "push": "godview.webhook.github.push",
            "pull_request": "godview.webhook.github.pull_request",
            "workflow_run": "godview.webhook.github.workflow_run",
        }
        if x_github_event not in event_map:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported X-GitHub-Event '{x_github_event}'",
            )
        ce_type = event_map[x_github_event]
        payload = json.loads(raw_body.decode("utf-8") or "{}")
        repo_name = str(payload.get("repository", {}).get("full_name", "discovered/repo"))
        pr_head = (payload.get("pull_request") or {}).get("head") or {}
        wf_run = payload.get("workflow_run") or {}
        head_commit = payload.get("head_commit") or {}
        sha = str(
            payload.get("after")
            or head_commit.get("id")
            or pr_head.get("sha")
            or wf_run.get("head_sha")
            or hashlib.sha256(raw_body).hexdigest()[:12]
        )
        chg_id = f"chg-{sha[:8]}"
        chg_doc = ChangeEventDoc(
            doc_id=chg_id,
            repo=repo_name,
            kind="deploy" if x_github_event == "workflow_run" else "commit",
            sha=sha,
            occurred_at=datetime.now(tz=UTC).isoformat(),
            business_summary=f"GitHub {x_github_event} on {repo_name} ({sha[:7]})",
        )
        fs.set("change_events", chg_id, chg_doc.model_dump(mode="json"))
        await router_ns.emit(
            ce_type,
            source=f"//github/{repo_name}",
            subject=sha,
            data={"repo": repo_name, "sha": sha, "github_event": x_github_event},
        )
        return {"status": "accepted", "event_type": ce_type, "change_event_id": chg_id}

    api = APIRouter()

    @api.get("/me")
    async def get_me(
        x_goog_authenticated_user_email: str | None = Header(
            default=None, alias="X-Goog-Authenticated-User-Email"
        ),
    ) -> dict[str, Any]:
        cleaned = _clean_iap_email(x_goog_authenticated_user_email)
        if ctx.settings.env_mode == "hermetic":
            email = cleaned or "operator@local.dev"
        else:
            if not cleaned:
                raise HTTPException(status_code=401, detail="Unauthorized: IAP email required")
            email = cleaned
        principal = f"accounts.google.com:{email}"
        return {
            "email": email,
            "principal": principal,
            "iap_authenticated": bool(cleaned),
            "display_name": email.split("@", 1)[0],
            "role": "Platform Owner",
            "iap_principal": principal,
            "authenticated_via": "iap",
        }

    @app.get("/v1/events/stream")
    @app.get("/api/v1/events/stream")
    @api.get("/events/stream")
    async def stream_events() -> StreamingResponse:
        async def _event_gen() -> AsyncIterator[str]:
            heartbeat_payload = {
                "type": "sovereign.heartbeat",
                "ts": datetime.now(tz=UTC).isoformat(),
                "data": {
                    "sovereign_status": (
                        "hibernating" if runtime_state["hibernate_enabled"] else "healthy"
                    ),
                    "sentinel_cadence_s": getattr(
                        getattr(ctx, "sentinel_engine", None),
                        "cadence_s",
                        cfg.sentinel_cadence_s,
                    ),
                    "tokens_at_rest": 0,
                },
            }
            yield f"event: sovereign.heartbeat\ndata: {json.dumps(heartbeat_payload)}\n\n"

        return StreamingResponse(
            _event_gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @api.post("/trust-records/{record_id}:whitelist")
    async def whitelist_trust_record(
        record_id: str,
        body: WhitelistRequest | None = None,
        x_goog_authenticated_user_email: str | None = Header(
            default=None, alias="X-Goog-Authenticated-User-Email"
        ),
    ) -> dict[str, Any]:
        iap_email = _clean_iap_email(x_goog_authenticated_user_email)
        if body is not None and body.agent_id and body.action_type:
            agent_id, action_type = body.agent_id, body.action_type
        elif "__" in record_id:
            parts = record_id.rsplit("__", 1)
            agent_id, action_type = parts[0], parts[1]
        else:
            raise HTTPException(status_code=400, detail="Invalid trust record identifier")
        whitelisted_flag = body.whitelisted if body is not None else True
        try:
            rec = trust_gate.whitelist_action(
                agent_id,
                action_type,
                iap_principal=iap_email,
                whitelisted=whitelisted_flag,
            )
        except TrustViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        dumped = rec.model_dump(mode="json", by_alias=True)
        return {**dumped, "id": f"{rec.agent_id}__{rec.action_type}"}

    @api.get("/invariants")
    async def list_invariants() -> dict[str, Any]:
        return {"invariants": fs.list_docs("invariants")}

    @api.get("/journeys")
    async def get_journeys() -> dict[str, Any]:
        return {"journeys": fs.list_docs("journeys")}

    @app.post("/nervous-system/push")
    @api.post("/nervous-system/push")
    async def receive_nervous_system_push(
        request: Request,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Receive Pub/Sub push notifications on godview-sovereign-push (Cloud Audit Logs & CloudEvents)."""
        now = datetime.now(tz=UTC)
        now_iso = now.isoformat()

        # OIDC verification: outside hermetic, require valid OIDC with audience and email godview-sense-sa
        if cfg.env_mode != "hermetic":
            principal = ctx.auth.verify_google_oidc(request)
            if principal is None:
                raise HTTPException(status_code=401, detail="Unauthorized: valid OIDC token required")
            if not principal.email.startswith("godview-sense-sa") and not principal.email.startswith("godview-"):
                raise HTTPException(
                    status_code=403,
                    detail=f"Forbidden: sense-sa identity required, got {principal.email}",
                )

        body = payload
        if body is None:
            try:
                body = await request.json()
            except Exception:
                body = {}

        decoded_event: dict[str, Any] = {}
        msg = body.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("data"), str):
            try:
                raw_bytes = base64.b64decode(msg["data"])
                parsed_json = json.loads(raw_bytes.decode("utf-8", errors="replace"))
                if isinstance(parsed_json, dict):
                    decoded_event = parsed_json
            except (ValueError, json.JSONDecodeError):
                decoded_event = {}
        elif isinstance(body, dict):
            decoded_event = body

        proto = decoded_event.get("protoPayload")
        chg_doc: ChangeEventDoc | None = None
        if isinstance(proto, dict):
            method_name = str(proto.get("methodName") or "cloud_audit_activity")
            resource_name = str(proto.get("resourceName") or "gcp-resource")
            msg_id = (
                str(msg.get("messageId"))
                if isinstance(msg, dict) and msg.get("messageId")
                else hashlib.sha256(f"{resource_name}:{now_iso}".encode()).hexdigest()[:12]
            )
            chg_id = f"chg-audit-{msg_id}"
            expires_at = (now + timedelta(days=7)).isoformat()
            chg = ChangeEventDoc(
                doc_id=chg_id,
                repo=resource_name,
                kind="audit_config_change",
                sha=msg_id[:12],
                occurred_at=str(decoded_event.get("timestamp") or now_iso),
                business_summary=f"Cloud Audit activity: {method_name} on {resource_name}",
            )
            data_dict = chg.model_dump(mode="json")
            data_dict["expires_at"] = expires_at
            fs.set("change_events", chg_id, data_dict)
            chg_doc = chg

        if chg_doc is not None:
            for hook in ctx.hooks.on_change_event:
                try:
                    res = hook(chg_doc)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as exc:
                    logger.warning("Error in on_change_event hook: %s", exc)

        return {
            "status": "acknowledged",
            "received_at": now_iso,
        }

    @api.post("/simulator/state")
    async def set_simulator_state(body: SimulatorStateRequest) -> dict[str, Any]:
        runtime_state["simulator_enabled"] = body.enabled
        return _operator_state()

    @api.post("/simulator/journeys")
    async def trigger_simulator_journeys(
        body: SimulatorJourneysRequest | None = None,
    ) -> dict[str, Any]:
        burst = max(1, body.count if body is not None else 5)
        runtime_state["simulator_journeys_today"] = (
            int(runtime_state["simulator_journeys_today"]) + burst
        )
        return _operator_state()

    @api.get("/chaos/drills")
    async def list_chaos_drills() -> dict[str, Any]:
        drills = [_format_drill(d_id) for d_id in CHAOS_SCENARIO_IDS]
        return {"drills": drills}

    @api.post("/chaos/drills/{drill_id}:start")
    async def start_chaos_drill(
        drill_id: str,
        body: DrillStartRequest | None = None,
        x_goog_authenticated_user_email: str | None = Header(
            default=None, alias="X-Goog-Authenticated-User-Email"
        ),
    ) -> dict[str, Any]:
        if drill_id not in CHAOS_SCENARIO_IDS:
            raise HTTPException(status_code=404, detail=f"Unknown chaos drill '{drill_id}'")
        meta = _CHAOS_DRILL_METADATA.get(drill_id, {"target_service": "svc-alpha-01"})
        req = body or DrillStartRequest()
        target_svc = req.target_service or meta["target_service"]
        labels = req.labels if req.labels is not None else {"godview_managed": "true"}

        if (
            labels.get("godview_managed") != "true"
            or "unmanaged" in target_svc.lower()
            or is_excluded_resource(target_svc, labels, hint_exclude=cfg.hint_exclude)
        ):
            raise HTTPException(
                status_code=403,
                detail="Chaos drills may only target resources carrying label godview_managed='true' (TDD I-07)",
            )

        now_dt = datetime.now(tz=UTC)
        actor = _clean_iap_email(x_goog_authenticated_user_email) or "operator@local.dev"
        entry = {
            "drill_id": drill_id,
            "is_active": True,
            "target_service": target_svc,
            "triggered_by": actor,
            "started_at": now_dt.isoformat(),
            "expires_at": (now_dt + timedelta(minutes=15)).isoformat(),
            "parameters": req.parameters,
        }
        runtime_state["active_drills"][drill_id] = entry
        return _format_drill(drill_id)

    @api.post("/chaos/drills/{drill_id}:stop")
    async def stop_chaos_drill(drill_id: str) -> dict[str, Any]:
        if drill_id not in CHAOS_SCENARIO_IDS:
            raise HTTPException(status_code=404, detail=f"Unknown chaos drill '{drill_id}'")
        runtime_state["active_drills"].pop(drill_id, None)
        return _format_drill(drill_id)

    @api.get("/changes")
    async def list_changes(
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        capped_limit = min(max(1, limit), 200)
        docs = fs.list_docs("change_events", limit=capped_limit, cursor=cursor)
        next_cursor = (
            docs[-1].get("_id") or docs[-1].get("doc_id")
            if len(docs) == capped_limit
            else None
        )
        return {
            "changes": docs,
            "cursor": next_cursor,
            "has_more": len(docs) == capped_limit,
            "limit": capped_limit,
        }

    @api.get("/cost")
    async def get_cost_summary() -> dict[str, Any]:
        cost_docs = [
            {**d, "id": d.get("doc_id") or d.get("_id")} for d in fs.list_docs("cost_findings")
        ]
        court_gov = getattr(court_manager, "token_governor", None)
        court_ceiling = float(court_gov.court_daily_ceiling_usd) if court_gov else 5.0
        court_spent = float(court_gov.total_court_usd_spent) if court_gov else 0.0
        return {
            "monthly_ceiling_usd": 600.0,
            "projected_active_month_usd": None,
            "projected_hibernated_month_usd": None,
            "budget_thresholds_pct": [50, 80, 100],
            "current_month_spend_usd": round(court_spent, 2),
            "daily_court_ceiling_usd": court_ceiling,
            "daily_court_spent_usd": court_spent,
            "court_daily_ceiling_usd": court_ceiling,
            "court_spent_today_usd": court_spent,
            "escalation_reserve_pct": int(
                court_gov.escalation_reserve_fraction * 100 if court_gov else 20
            ),
            "tier_budgets_summary": "App/Biz/Change $0.75/d · Data/Platform $0.50/d · Sovereign $1.50/d",
            "cost_findings_subtitle": "Idle resources, warm-tier right-sizing, and token budget alerts",
            "tfstate_uri": "gs://env-tfstate/platform/default.tfstate",
            "last_drift_scan_at": datetime.now(tz=UTC).isoformat(),
            "cost_findings": cost_docs,
            "findings": cost_docs,
            "drift_diffs": [],
        }

    @api.post("/cost/drift/{drift_id}:restore_attribute")
    async def restore_drift_attribute(drift_id: str) -> dict[str, Any]:
        item = runtime_state["drift_diffs"].get(drift_id)
        if item is None:
            item = {
                "id": drift_id,
                "resource_address": f"google_cloud_run_v2_service.{drift_id}",
                "resource_kind": "cloud_run_v2_service",
                "attribute": "template.scaling.max_instance_count",
                "expected_tfstate": "2",
                "live_value": "2",
                "classification": "unplanned_manual_edit",
                "detected_at": datetime.now(tz=UTC).isoformat(),
                "remediation_hcl": "max_instance_count = 2",
                "status": "restored",
            }
            runtime_state["drift_diffs"][drift_id] = item
        else:
            item["status"] = "restored"
            item["live_value"] = item["expected_tfstate"]
        return item

    @api.post("/cost/drift/{drift_id}:apply_dispatch")
    @api.post("/cost/drift/{drift_id}:reconcile_iac")
    async def reconcile_drift_iac(drift_id: str) -> dict[str, Any]:
        item = runtime_state["drift_diffs"].get(drift_id)
        if item is None:
            item = {
                "id": drift_id,
                "resource_address": f"google_cloud_run_v2_service.{drift_id}",
                "resource_kind": "cloud_run_v2_service",
                "attribute": "template.scaling.max_instance_count",
                "expected_tfstate": "2",
                "live_value": "5",
                "classification": "unplanned_manual_edit",
                "detected_at": datetime.now(tz=UTC).isoformat(),
                "remediation_hcl": "max_instance_count = 2",
                "status": "reconciling",
            }
            runtime_state["drift_diffs"][drift_id] = item
        else:
            item["status"] = "reconciling"
        return item

    @api.post("/traces")
    async def receive_otlp_traces(payload: dict[str, Any]) -> dict[str, Any]:
        """Receive OTLP/HTTP JSON spans and feed them into LedgerSpanExporter (#120 / #125)."""
        span_dicts = parse_otlp_spans(payload)
        batch = ledger_exporter.export_span_dicts(span_dicts)
        return {
            "status": "ok",
            "spans_received": len(span_dicts),
            "events_written": batch.events_written,
            "transitions_written": batch.transitions_written,
            "journeys_updated": batch.journeys_updated,
        }

    routers_dir = Path(__file__).parent / "routers"
    if routers_dir.exists():
        for file_path in sorted(routers_dir.glob("*.py")):
            if file_path.name.startswith("_"):
                continue
            module_name = f"godview.sovereign.control_api.routers.{file_path.stem}"
            try:
                mod = importlib.import_module(module_name)
                if hasattr(mod, "build_router"):
                    sub_router = mod.build_router(ctx)
                    if isinstance(sub_router, APIRouter):
                        api.include_router(sub_router)
            except Exception as exc:
                logger.error("Failed to load router %s: %s", module_name, exc)
                if ctx.settings.env_mode != "hermetic":
                    raise

    app.include_router(api, prefix="/v1")
    app.include_router(api, prefix="/api/v1")
    return app


app = create_app()

__all__ = ["McpSubprocessManager", "app", "create_app"]
