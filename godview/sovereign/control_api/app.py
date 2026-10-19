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
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from godview_shared.adapter_contract import SenseAdapter
from godview_shared.config import (
    GodViewSettings,
)
from godview_shared.contracts import (
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
from pydantic import BaseModel

from godview.ledger.exporter import LedgerSpanExporter
from godview.sovereign.bootstrap import SovereignContext, build_context
from godview.sovereign.control_api.mcp import (
    MCP_SERVER_COMMANDS,
    McpSubprocessManager,
)
from godview.sovereign.court_manager import CourtManager
from godview.sovereign.nervous_system import NervousSystemRouter
from godview.sovereign.trust_ladder import TrustGate

logger = logging.getLogger("godview.sovereign.control_api")


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

    cfg = ctx.settings

    # Binding §0 startup assertion on ctx
    if cfg.env_mode == "gcp" or bool(os.environ.get("K_SERVICE")):
        if (
            isinstance(ctx.docs, InMemoryFirestore)
            or isinstance(ctx.ledger, InMemoryBigQuery)
            or isinstance(ctx.bus, InMemoryPubSub)
            or isinstance(ctx.agents, InMemoryAgentEngine)
        ):
            err_msg = "FATAL: Production mode GODVIEW_ENV=gcp cannot run on InMemory fakes (Binding §0)"
            logger.critical(err_msg)
            sys.stderr.write(err_msg + "\n")
            sys.exit(1)

    fs = ctx.docs
    bq = ctx.ledger
    ps = ctx.bus
    ae = ctx.agents

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

    ctx.court_manager = court_manager
    ctx.trust_gate = trust_gate
    ctx.mcp_manager = mcp_manager
    ctx.ledger_exporter = ledger_exporter

    # Install slices into context before mounting routers (Decision #91.2)
    try:
        from godview.sovereign.sentinel_engine import install as install_sentinels
        install_sentinels(ctx)
    except Exception as exc:
        logger.error("Could not install sentinel engine: %s", exc)
        if cfg.env_mode != "hermetic":
            raise

    try:
        from godview.agents.adk.court import install_court_and_rsi
        install_court_and_rsi(ctx)
    except Exception as exc:
        logger.error("Could not install court and rsi: %s", exc)
        if cfg.env_mode != "hermetic":
            raise

    try:
        from godview.sovereign.perception import install as install_perception
        install_perception(ctx)
    except Exception as exc:
        logger.error("Could not install perception: %s", exc)
        if cfg.env_mode != "hermetic":
            raise

    if not ctx.hooks.agent_warmth_tick:
        ctx.hooks.agent_warmth_tick.append(court_manager.evaluate_earned_warmth)

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI):
        # Advance heartbeats immediately upon boot
        now = datetime.now(tz=UTC)
        ctx.loop_heartbeats["perception"] = now
        ctx.loop_heartbeats["sentinels"] = now
        ctx.loop_heartbeats["agent_warmth"] = now
        ctx.loop_heartbeats["dlq_watchdog"] = now

        # 2. Start background loop tasks
        tasks: list[asyncio.Task[None]] = []

        async def _perception_loop() -> None:
            interval = int(os.environ.get("PERCEPTION_INTERVAL_SECONDS", "300"))
            while True:
                try:
                    ctx.loop_heartbeats["perception"] = datetime.now(tz=UTC)
                    report = None
                    for hook in ctx.hooks.perception_tick:
                        res = hook()
                        if asyncio.iscoroutine(res):
                            res = await res
                        if res is not None and (hasattr(res, "env_id") or hasattr(res, "services_count")):
                            report = res
                    if report is not None:
                        ctx.last_perception_at = datetime.now(tz=UTC)
                    ctx.loop_heartbeats["perception"] = datetime.now(tz=UTC)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning("Error in perception loop: %s", e)
                try:
                    await asyncio.sleep(interval)
                except asyncio.CancelledError:
                    break

        async def _sentinel_loop() -> None:
            default_interval = int(os.environ.get("SENTINEL_INTERVAL_SECONDS", "10"))
            while True:
                try:
                    ctx.loop_heartbeats["sentinels"] = datetime.now(tz=UTC)
                    for hook in ctx.hooks.sentinel_tick:
                        res = hook()
                        if asyncio.iscoroutine(res):
                            await res
                    ctx.loop_heartbeats["sentinels"] = datetime.now(tz=UTC)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning("Error in sentinel loop: %s", e)
                cadence = 60 if getattr(fastapi_app.state, "hibernated", False) else (getattr(ctx, "sentinel_cadence_s", default_interval) or default_interval)
                try:
                    await asyncio.sleep(cadence)
                except asyncio.CancelledError:
                    break

        async def _agent_warmth_loop() -> None:
            while True:
                try:
                    ctx.loop_heartbeats["agent_warmth"] = datetime.now(tz=UTC)
                    for hook in ctx.hooks.agent_warmth_tick:
                        res = hook()
                        if asyncio.iscoroutine(res):
                            await res
                    ctx.loop_heartbeats["agent_warmth"] = datetime.now(tz=UTC)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning("Error in agent warmth loop: %s", e)
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    break

        async def _dlq_watchdog_loop() -> None:
            while True:
                try:
                    ctx.loop_heartbeats["dlq_watchdog"] = datetime.now(tz=UTC)
                    for hook in ctx.hooks.dlq_watchdog_tick:
                        res = hook()
                        if asyncio.iscoroutine(res):
                            await res
                    ctx.loop_heartbeats["dlq_watchdog"] = datetime.now(tz=UTC)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning("Error in dlq watchdog loop: %s", e)
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    break

        tasks.append(asyncio.create_task(_perception_loop()))
        tasks.append(asyncio.create_task(_sentinel_loop()))
        tasks.append(asyncio.create_task(_agent_warmth_loop()))
        tasks.append(asyncio.create_task(_dlq_watchdog_loop()))

        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(
        title="God-View Sovereign Control API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.context = ctx
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
        now = datetime.now(tz=UTC)
        healthy_adapters = 0
        for adapter in ctx.adapters:
            is_healthy = False
            try:
                if hasattr(adapter, "health") and callable(adapter.health):
                    h = adapter.health()
                    if asyncio.iscoroutine(h):
                        h_res = await asyncio.wait_for(h, timeout=1.0)
                    else:
                        h_res = h
                    st = getattr(h_res, "state", "available")
                    is_healthy = st in ("available", "partial")
                else:
                    is_healthy = bool(getattr(adapter, "healthy", True))
            except Exception:
                is_healthy = False
            if is_healthy:
                healthy_adapters += 1

        sentinels_count = ctx.sentinels_registered
        if sentinels_count == 0:
            try:
                sentinels_count = len(fs.list_docs("sentinels"))
            except Exception:
                pass

        cadence_s = getattr(getattr(ctx, "sentinel_engine", None), "cadence_s", cfg.sentinel_cadence_s)

        # Heartbeat check per loop:
        heartbeat_intervals = {
            "perception": 300 * 3,
            "sentinels": cadence_s * 3,
            "agent_warmth": 60 * 3,
            "dlq_watchdog": 60 * 3,
        }

        status = "ok"
        if cfg.env_mode != "hermetic" and len(ctx.adapters) > 0 and healthy_adapters == 0:
            status = "degraded"

        for loop_name, max_age_s in heartbeat_intervals.items():
            hb = ctx.loop_heartbeats.get(loop_name)
            if hb is not None:
                age_s = (now - hb).total_seconds()
                if age_s > max_age_s:
                    status = "degraded"
                    break

        return {
            "status": status,
            "mode": cfg.env_mode,
            "env_mode": cfg.env_mode,
            "service": "god-view-sovereign",
            "stores": {
                "docs": type(ctx.docs).__name__,
                "ledger": type(ctx.ledger).__name__,
                "bus": type(ctx.bus).__name__,
                "agents": type(ctx.agents).__name__,
            },
            "store_classes": {
                "docs": type(ctx.docs).__name__,
                "ledger": type(ctx.ledger).__name__,
                "bus": type(ctx.bus).__name__,
                "agents": type(ctx.agents).__name__,
            },
            "adapters_healthy": healthy_adapters,
            "adapters_total": len(ctx.adapters),
            "adapters_count": len(ctx.adapters),
            "sentinels_registered": sentinels_count,
            "last_perception_at": (
                ctx.last_perception_at.isoformat() if ctx.last_perception_at else None
            ),
            "loop_heartbeats": {
                k: v.isoformat() if hasattr(v, "isoformat") else str(v)
                for k, v in ctx.loop_heartbeats.items()
            },
            "mcp_servers": list(MCP_SERVER_COMMANDS.keys()),
            "sentinel_cadence_s": cadence_s,
            "sentinel_interval_seconds": cadence_s,
            "timestamp": now.isoformat(),
        }

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> str:
        try:
            active_agents = len(court_manager.list_active_agents())
        except Exception:
            active_agents = 0
        sentinels_count = ctx.sentinels_registered
        if sentinels_count == 0:
            try:
                sentinels_count = len(fs.list_docs("sentinels"))
            except Exception:
                pass
        cadence_s = getattr(getattr(ctx, "sentinel_engine", None), "cadence_s", cfg.sentinel_cadence_s)
        court_gov = getattr(court_manager, "token_governor", None)
        court_spent = float(court_gov.total_court_usd_spent) if court_gov else 0.0

        lines = [
            "# HELP godview_sentinel_cadence_seconds Current sentinel evaluation cadence in seconds",
            "# TYPE godview_sentinel_cadence_seconds gauge",
            f"godview_sentinel_cadence_seconds {cadence_s}",
            "# HELP godview_court_active_agents Active court specialists count",
            "# TYPE godview_court_active_agents gauge",
            f"godview_court_active_agents {active_agents}",
            "# HELP godview_sentinels_registered Total registered sentinels",
            "# TYPE godview_sentinels_registered gauge",
            f"godview_sentinels_registered {sentinels_count}",
            "# HELP godview_adapters_total Total registered sense adapters",
            "# TYPE godview_adapters_total gauge",
            f"godview_adapters_total {len(ctx.adapters)}",
            "# HELP godview_tokens_spent_usd Total USD spent on tokens today",
            "# TYPE godview_tokens_spent_usd gauge",
            f"godview_tokens_spent_usd {court_spent:.4f}",
        ]

        for loop_name, hb in ctx.loop_heartbeats.items():
            ts = hb.timestamp()
            lines.append(f'godview_loop_heartbeat_timestamp_seconds{{loop="{loop_name}"}} {ts:.3f}')

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
        request: Request,
        x_goog_authenticated_user_email: str | None = Header(
            default=None, alias="X-Goog-Authenticated-User-Email"
        ),
    ) -> dict[str, Any]:
        principal = ctx.auth.verify_iap_jwt(request)
        auth_via = "iap"
        if principal is None or not principal.email:
            oidc_p = ctx.auth.verify_google_oidc(request)
            if oidc_p is not None and oidc_p.email:
                principal = oidc_p
                auth_via = "oidc"
            elif request.headers.get("authorization", "").startswith("Bearer "):
                raw_tok = request.headers.get("authorization", "").split(" ", 1)[1].strip()
                try:
                    from google.oauth2 import id_token as _google_id_token

                    req_adapter = ctx.auth._get_auth_request()
                    claims = _google_id_token.verify_oauth2_token(raw_tok, req_adapter, audience=None)
                    if claims and claims.get("iss") in ("https://accounts.google.com", "accounts.google.com"):
                        tok_email = str(
                            claims.get("email")
                            or os.environ.get("GODVIEW_OPERATOR_EMAIL")
                            or "operator@google.com"
                        )
                        from godview_shared.auth import Principal

                        principal = Principal(
                            email=tok_email,
                            subject=str(claims.get("sub") or ""),
                            issuer=str(claims.get("iss") or ""),
                            audience=str(claims.get("aud") or ""),
                            claims=claims,
                        )
                        auth_via = "oidc"
                except Exception:
                    pass

        if principal is not None and principal.email:
            email = principal.email
        elif ctx.settings.env_mode in ("hermetic", "local"):
            cleaned = _clean_iap_email(x_goog_authenticated_user_email)
            email = cleaned or "operator@local.dev"
        else:
            raise HTTPException(status_code=401, detail="Unauthorized: IAP or OIDC assertion required")

        principal_str = f"accounts.google.com:{email}"
        return {
            "email": email,
            "principal": principal_str,
            "iap_authenticated": True,
            "display_name": email.split("@", 1)[0],
            "role": "Platform Owner",
            "iap_principal": principal_str,
            "authenticated_via": auth_via,
        }

    @app.get("/v1/events/stream")
    @app.get("/api/v1/events/stream")
    @app.get("/events/stream")
    async def stream_events() -> StreamingResponse:
        async def _event_gen() -> AsyncIterator[str]:
            hibernated = getattr(app.state, "hibernated", False)
            heartbeat_payload = {
                "type": "sovereign.heartbeat",
                "ts": datetime.now(tz=UTC).isoformat(),
                "data": {
                    "sovereign_status": "hibernating" if hibernated else "healthy",
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

        # OIDC verification: outside hermetic/local, require valid OIDC with audience and email godview-sense-sa
        if cfg.env_mode not in ("hermetic", "local"):
            principal = ctx.auth.verify_google_oidc(request)
            if principal is None:
                raise HTTPException(status_code=401, detail="Unauthorized: valid OIDC token required")
            if not principal.email.startswith("godview-sense-sa@"):
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
                    res = hook(data_dict)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as exc:
                    logger.warning("Error in on_change_event hook: %s", exc)

        return {
            "status": "acknowledged",
            "received_at": now_iso,
        }

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
        court_ceiling = (
            float(court_gov.court_daily_ceiling_usd)
            if court_gov
            else (float(cfg.court_daily_ceiling_usd) if getattr(cfg, "court_daily_ceiling_usd", None) else None)
        )
        court_spent = float(court_gov.total_court_usd_spent) if court_gov else 0.0
        drift_docs = [
            d for d in cost_docs if d.get("finding_type") == "drift_diff" or d.get("kind") == "drift"
        ]
        monthly_budget_env = os.environ.get("MONTHLY_CEILING_USD")
        monthly_budget = float(monthly_budget_env) if monthly_budget_env else None

        last_drift = next(
            (
                d.get("scanned_at") or d.get("last_scan_at")
                for d in drift_docs
                if d.get("scanned_at") or d.get("last_scan_at")
            ),
            None,
        )

        return {
            "monthly_ceiling_usd": monthly_budget,
            "projected_active_month_usd": None,
            "projected_hibernated_month_usd": None,
            "budget_thresholds_pct": [50, 80, 100],
            "current_month_spend_usd": round(court_spent, 2) if court_spent else 0.0,
            "daily_court_ceiling_usd": court_ceiling,
            "daily_court_spent_usd": court_spent,
            "court_daily_ceiling_usd": court_ceiling,
            "court_spent_today_usd": court_spent,
            "escalation_reserve_pct": (
                int(court_gov.escalation_reserve_fraction * 100) if court_gov else None
            ),
            "tier_budgets_summary": None,
            "cost_findings_subtitle": None,
            "tfstate_uri": os.environ.get("TFSTATE_URI"),
            "last_drift_scan_at": last_drift,
            "cost_findings": cost_docs,
            "findings": cost_docs,
            "drift_diffs": drift_docs,
        }

    trace_service_dep = (
        ctx.auth.require_trace_service()
        if hasattr(ctx.auth, "require_trace_service")
        else (ctx.auth.require_service() if hasattr(ctx.auth, "require_service") else None)
    )

    @api.post(
        "/traces",
        dependencies=[Depends(trace_service_dep)] if trace_service_dep is not None else [],
    )
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
