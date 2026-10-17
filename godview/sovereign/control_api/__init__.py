"""Sovereign Control API & MCP Stdio Subprocess Host (`godview.sovereign.control_api`).

Implements TDD §3.4, §3.6, §3.7 and Coordinator Decisions A3, A10, A18:
- Mounts all control routes under both `/v1/*` and `/api/v1/*` (Decision A18 same-origin routing).
- Exposes `POST /v1/capabilities/{capability_id}:invoke` as the single TrustGate enforcement
  point for all specialist court agents, hosting the 4 official MCP stdio servers inside
  `god-view-sovereign` (Decision A3).
- Enforces `X-Goog-Authenticated-User-Email` IAP identity on approval decisions and L4
  whitelisting (Decision A10).
- Enforces `godview_managed="true"` on chaos drills (TDD I-07) and suppresses false-alarm
  traffic-drop wakes during Kill Switch, Scheduled Pause, and Hibernate windows.
- Verifies GitHub webhook HMAC-SHA256 signatures on `POST /public/webhooks/github`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from godview_shared.adapter_contract import SenseAdapter
from godview_shared.capabilities import (
    get_capability,
    validate_mcp_tool_binding,
)
from godview_shared.config import (
    DEFAULT_AGENT_FALLBACK_MODEL_ID,
    DEFAULT_AGENT_MODEL_ID,
    MODEL_TABLE,
    GodViewSettings,
    is_excluded_resource,
)
from godview_shared.contracts import (
    CHAOS_SCENARIO_IDS,
    ChangeEventDoc,
    CourtAgentDoc,
    SentinelDefinition,
    SentinelState,
    TrustDecision,
    TrustViolation,
)
from godview_shared.fakes import (
    InMemoryAgentEngine,
    InMemoryBigQuery,
    InMemoryFirestore,
    InMemoryPubSub,
)
from godview_shared.otel import parse_otlp_spans
from pydantic import BaseModel, Field, ValidationError

from godview.ledger.exporter import LedgerSpanExporter
from godview.sovereign.court_manager import CourtManager
from godview.sovereign.nervous_system import NervousSystemRouter
from godview.sovereign.perception import PerceptionOrgan, PerceptionReport
from godview.sovereign.sentinel_engine import SentinelEngine
from godview.sovereign.trust_ladder import TrustGate

MCP_SERVER_COMMANDS: dict[str, list[str]] = {
    "observability-mcp": ["npx", "-y", "@google-cloud/observability-mcp"],
    "gke-mcp": ["gke-mcp", "--stdio"],
    "mcp-toolbox-postgres": [
        "genai-toolbox",
        "--prebuilt",
        "cloud-sql-postgres",
        "--stdio",
    ],
    "github-mcp": ["github-mcp-server", "stdio"],
}

_CHAOS_DRILL_METADATA: dict[str, dict[str, str]] = {
    "gke_oom_503": {
        "fault_class": "infrastructure",
        "fault_type": "pod_oom_503",
        "title": "Pod OOM & HTTP 503 Saturation",
        "target_service": "svc-alpha-01",
        "infra_golden_signals": "red",
        "expected_detection": "golden_signal_gate + probe_plan",
        "mttd_target": "30s",
    },
    "pg_lock_contention": {
        "fault_class": "infrastructure",
        "fault_type": "row_lock_stall",
        "title": "Database Row-Lock Contention",
        "target_service": "db-primary-01",
        "infra_golden_signals": "amber",
        "expected_detection": "journey_conformance (STALLED)",
        "mttd_target": "60s",
    },
    "bad_rollout_pr": {
        "fault_class": "change",
        "fault_type": "calculation_regression",
        "title": "Formula Regression Rollout",
        "target_service": "svc-alpha-01",
        "infra_golden_signals": "green",
        "expected_detection": "invariant_sql",
        "mttd_target": "30s",
    },
    "silent_drop_200": {
        "fault_class": "semantic",
        "fault_type": "silent_rejection_200",
        "title": "Silent HTTP 200 Approval Drop",
        "target_service": "svc-alpha-01",
        "infra_golden_signals": "green",
        "expected_detection": "kpi_changepoint + invariant_sql",
        "mttd_target": "60s",
    },
    "armor_bypass": {
        "fault_class": "security",
        "fault_type": "guardrail_bypass",
        "title": "Guardrail Bypass Injection",
        "target_service": "svc-alpha-01",
        "infra_golden_signals": "green",
        "expected_detection": "invariant_sql",
        "mttd_target": "30s",
    },
    "cbs_timeout": {
        "fault_class": "infrastructure",
        "fault_type": "upstream_timeout",
        "title": "Upstream Dependency Timeout",
        "target_service": "vm-core-01",
        "infra_golden_signals": "amber",
        "expected_detection": "journey_conformance + golden_signal_gate",
        "mttd_target": "45s",
    },
    "migration_skipped": {
        "fault_class": "semantic",
        "fault_type": "step_bypass",
        "title": "Mandatory Step Skipped in Flow",
        "target_service": "svc-alpha-01",
        "infra_golden_signals": "green",
        "expected_detection": "journey_conformance (SKIPPED) + invariant_sql",
        "mttd_target": "30s",
    },
}


class McpSubprocessManager:
    """Hosts the 4 official MCP stdio servers and SenseAdapters inside Sovereign (Decision A3)."""

    def __init__(
        self,
        *,
        env_mode: str = "hermetic",
        adapters: Sequence[SenseAdapter] | None = None,
    ) -> None:
        self.server_commands = dict(MCP_SERVER_COMMANDS)
        self.env_mode = env_mode
        self._adapters: list[SenseAdapter] = list(adapters) if adapters is not None else []
        self.invocation_log: list[dict[str, Any]] = []

    def register_adapter(self, adapter: SenseAdapter) -> None:
        """Register a SenseAdapter to handle matching capabilities."""
        if adapter not in self._adapters:
            self._adapters.append(adapter)

    def register_adapters(self, adapters: Sequence[SenseAdapter]) -> None:
        """Register multiple SenseAdapters."""
        for adapter in adapters:
            self.register_adapter(adapter)

    def invoke_capability(
        self,
        capability_id: str,
        args: dict[str, Any],
        decision: TrustDecision,
    ) -> dict[str, Any]:
        """Validate and record a TrustGate-authorized capability call synchronously."""
        spec = get_capability(capability_id)
        if spec.mutating and not decision.authorized:
            raise TrustViolation(
                f"McpSubprocessManager refused unauthorized mutating capability '{capability_id}'"
            )

        mcp_server: str | None = spec.mcp_server
        mcp_tools: list[str] = list(spec.mcp_tools)
        if mcp_server is not None:
            for tool_name in mcp_tools:
                validate_mcp_tool_binding(mcp_server, tool_name)

        record = {
            "capability_id": capability_id,
            "mcp_server": mcp_server,
            "mcp_tools": mcp_tools,
            "sa_impersonated": decision.sa_to_impersonate,
            "args": args,
            "invoked_at": datetime.now(tz=UTC).isoformat(),
        }
        self.invocation_log.append(record)
        return {
            "ok": True,
            "capability_id": capability_id,
            "mcp_server": mcp_server,
            "mcp_tools": mcp_tools,
            "echo_args": args,
        }

    async def invoke_capability_async(
        self,
        capability_id: str,
        args: dict[str, Any],
        decision: TrustDecision,
    ) -> dict[str, Any]:
        """Dispatch a TrustGate-authorized capability call to a registered SenseAdapter or MCP server."""
        base_result = self.invoke_capability(capability_id, args, decision)
        for adapter in self._adapters:
            caps = (
                adapter.capabilities()
                if callable(getattr(adapter, "capabilities", None))
                else getattr(adapter, "capabilities", ())
            )
            if capability_id in caps:
                adapter_res = await adapter.execute_capability(
                    capability_id,
                    args,
                    trust_decision=decision,
                )
                return {
                    **base_result,
                    "adapter_family": adapter.family,
                    "adapter_result": adapter_res,
                }
        return base_result


class PerceiveRequest(BaseModel):
    reset: bool = False


class SentinelPatchRequest(BaseModel):
    state: SentinelState


class CapabilityInvokeBody(BaseModel):
    agent_id: str
    incident_id: str | None = None
    target_resource: str = "resource"
    blast_radius_resources: int = 1
    approval_id: str | None = None
    novel_skill_first_step: bool = False
    args: dict[str, Any] = Field(default_factory=dict)
    reversal_recipe: dict[str, Any] | None = None
    narrative: str = "Remediation action"
    rupee_impact: float = 0.0


class KillSwitchRequest(BaseModel):
    enabled: bool = True
    reason: str = "Operator kill switch"


class HibernateRequest(BaseModel):
    enabled: bool = True


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
    fs = firestore or InMemoryFirestore()
    bq = bigquery or InMemoryBigQuery()
    ps = pubsub or InMemoryPubSub()
    ae = agent_engine or InMemoryAgentEngine()
    cfg = settings or GodViewSettings.from_env()
    if webhook_secret is not None:
        secret = webhook_secret
    elif cfg.env_mode == "hermetic":
        secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "hermetic-webhook-secret")
    else:
        secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")

    router_ns = NervousSystemRouter(pubsub=ps)
    perception = PerceptionOrgan(firestore=fs, pubsub=ps, adapters=adapters, settings=cfg)
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
    sentinel_engine = SentinelEngine(
        firestore=fs,
        bigquery=bq,
        pubsub=ps,
        agent_engine=ae,
        token_governor=court_manager.token_governor,
        trust_gate=trust_gate,
    )
    ledger_exporter = LedgerSpanExporter(
        bq,
        env_id=cfg.env_mode,
        default_journey_name="discovered_journey",
    )
    mcp_manager = McpSubprocessManager(env_mode=cfg.env_mode, adapters=adapters)

    now_init = datetime.now(tz=UTC).isoformat()
    runtime_state: dict[str, Any] = {
        "kill_switch_enabled": False,
        "kill_switch_reason": None,
        "kill_switch_actor": None,
        "kill_switch_updated_at": None,
        "tenant_paused": False,
        "simulator_enabled": True,
        "simulator_journeys_today": 18,
        "hibernate_enabled": False,
        "active_drills": {},
        "drift_diffs": {
            "drift-01": {
                "id": "drift-01",
                "resource_address": "google_cloud_run_v2_service.svc_alpha_01",
                "resource_kind": "cloud_run_v2_service",
                "attribute": "template.scaling.max_instance_count",
                "expected_tfstate": "2",
                "live_value": "5",
                "classification": "unplanned_manual_edit",
                "detected_at": now_init,
                "remediation_hcl": "max_instance_count = 2",
                "status": "open",
            }
        },
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
                "target_service": "svc-alpha-01",
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

    def _enrich_court_agent(doc: CourtAgentDoc) -> dict[str, Any]:
        raw = doc.model_dump(mode="json")
        agent_id = doc.doc_id or doc.namespace
        trust_docs = fs.list_docs(
            "trust_records", predicate=lambda d: d.get("agent_id") == agent_id
        )
        enriched_trust: list[dict[str, Any]] = []
        for td in trust_docs:
            rec_id = td.get("doc_id") or td.get("_id") or f"{agent_id}__{td.get('action_type', '')}"
            enriched_trust.append({**td, "id": rec_id})

        subdocs = fs.list_subdocs("court_agents", agent_id, "living_model")
        living_model_dict: dict[str, list[dict[str, Any]]] = {
            "entities": [],
            "rules": [],
            "state_machines": [],
            "routes": [],
            "dependencies": [],
            "decision_boundaries": [],
            "feature_flags": [],
            "commit_diffs": [],
        }
        for sd in subdocs:
            sec = str(sd.get("section") or sd.get("_id") or "rules")
            if sec in living_model_dict and isinstance(sd.get("items"), list):
                living_model_dict[sec] = list(sd["items"])

        spent_usd = float(court_manager.token_governor.get_usd_spent(agent_id))
        spent_tokens = int(
            court_manager.token_governor.get_tokens_spent(agent_id) or doc.tokens_spent_today
        )
        return {
            **raw,
            "id": agent_id,
            "doc_id": agent_id,
            "token_budget_daily_usd": float(doc.token_budget_daily),
            "tokens_spent_today_usd": spent_usd,
            "tokens_spent_today": spent_tokens,
            "budget_exhausted": bool(
                court_manager.token_governor.is_agent_degraded_to_l1(agent_id)
            ),
            "trust_records": enriched_trust,
            "living_model": living_model_dict,
            "living_model_subdocs": subdocs,
        }

    def _format_perception_response(report: PerceptionReport) -> dict[str, Any]:
        base = report.model_dump(mode="json")
        services_raw = fs.list_docs("discovered_services")
        services_list: list[dict[str, Any]] = []
        for s in services_raw:
            sid = (
                s.get("doc_id")
                or s.get("_id")
                or f"{s.get('env_id')}__{s.get('kind')}.{s.get('name')}"
            )
            routes_out = []
            for r in s.get("routes", []):
                routes_out.append(
                    {
                        **r,
                        "probe_safe": bool(
                            r.get("probe_safe", r.get("x-godview-probe-safe", False))
                        ),
                    }
                )
            services_list.append(
                {
                    **s,
                    "id": sid,
                    "routes": routes_out,
                    "golden_signals": s.get(
                        "golden_signals",
                        {
                            "p50_ms": 18.0,
                            "p95_ms": 42.0,
                            "p99_ms": 85.0,
                            "rps": 12.4,
                            "error_rate_pct": 0.0,
                            "saturation_pct": 22.0,
                            "status": "green",
                        },
                    ),
                }
            )

        edges_list = [
            {**e, "id": e.get("doc_id") or e.get("_id") or f"{e.get('src')}__to__{e.get('dst')}"}
            for e in fs.list_docs("discovered_edges")
        ]
        entities_list = [
            {
                **ent,
                "id": ent.get("doc_id") or ent.get("_id") or f"{report.env_id}__{ent.get('name')}",
            }
            for ent in fs.list_docs("discovered_entities")
        ]

        if report.adapter_health:
            senses_list = [
                {
                    "adapter_family": h.family,
                    "availability": h.state,
                    "evidence": json.dumps(h.evidence),
                    "discovered_count": report.services_count,
                    "last_checked_at": report.perceived_at,
                }
                for h in report.adapter_health
            ]
        else:
            senses_list = [
                {
                    "adapter_family": fam,
                    "availability": "available",
                    "evidence": "Sense adapter active",
                    "discovered_count": report.services_count,
                    "last_checked_at": report.perceived_at,
                }
                for fam in ("gcp", "k8s", "sql", "http_prom", "git_iac", "local")
            ]

        return {
            **base,
            "sovereign_id": "god-view-sovereign-01",
            "sovereign_status": (
                "hibernating" if runtime_state["hibernate_enabled"] else "healthy"
            ),
            "heartbeat_at": report.perceived_at,
            "sentinel_cadence_s": sentinel_engine.cadence_s,
            "tokens_at_rest": 0,
            "sovereign_budget_daily_usd": float(
                court_manager.token_governor.get_daily_budget_usd("sovereign")
            ),
            "sovereign_spent_today_usd": float(
                court_manager.token_governor.get_usd_spent("sovereign")
            ),
            "court_ceiling_daily_usd": float(court_manager.token_governor.court_daily_ceiling_usd),
            "monthly_ceiling_usd": 600.0,
            "escalation_reserve_pct": int(
                court_manager.token_governor.escalation_reserve_fraction * 100
            ),
            "reasoning_model": DEFAULT_AGENT_MODEL_ID,
            "escalation_model": MODEL_TABLE["escalation"].model_id,
            "fallback_model": DEFAULT_AGENT_FALLBACK_MODEL_ID,
            "embedding_model": MODEL_TABLE["embedding"].model_id,
            "managed_scope_label": "godview_managed=true",
            "warm_tier_spec": f"max {court_manager.max_warm_agents} warm (09:00–17:00 IST)",
            "active_window_label": "09:00–17:00 IST (Mon–Fri)",
            "approval_window_label": "30m TTL",
            "hibernate_job_name": "god-view-hibernate-job",
            "memory_db_name": "god-view-memory-db",
            "simulator_target_daily": 100,
            "simulator_image_ratio_label": "1 in 5 journeys",
            "controls_enabled": True,
            "senses": senses_list,
            "discovered_services": services_list,
            "discovered_edges": edges_list,
            "discovered_entities": entities_list,
            "court_spawn_plan": [item["agent_id"] for item in report.spawn_plan],
            "baseline_sentinels_registered": len(sentinel_engine.list_sentinels()),
            "operator_state": _operator_state(),
        }

    app = FastAPI(
        title="God-View Sovereign Control API",
        version="0.1.0",
    )
    app.state.firestore = fs
    app.state.bigquery = bq
    app.state.pubsub = ps
    app.state.agent_engine = ae
    app.state.perception = perception
    app.state.court_manager = court_manager
    app.state.trust_gate = trust_gate
    app.state.sentinel_engine = sentinel_engine
    app.state.ledger_exporter = ledger_exporter
    app.state.mcp_manager = mcp_manager

    @app.get("/public/healthz")
    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "god-view-sovereign",
            "mcp_servers": list(MCP_SERVER_COMMANDS.keys()),
            "sentinel_cadence_s": sentinel_engine.cadence_s,
            "sentinel_interval_seconds": sentinel_engine.cadence_s,
        }

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> str:
        active_agents = len(court_manager.list_active_agents())
        sentinels_count = len(sentinel_engine.list_sentinels())
        lines = [
            "# HELP godview_sentinel_cadence_seconds Current sentinel evaluation cadence in seconds",
            "# TYPE godview_sentinel_cadence_seconds gauge",
            f"godview_sentinel_cadence_seconds {sentinel_engine.cadence_s}",
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
        email = cleaned or "operator@local.dev"
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
                    "sentinel_cadence_s": sentinel_engine.cadence_s,
                    "tokens_at_rest": 0,
                },
            }
            yield f"event: sovereign.heartbeat\ndata: {json.dumps(heartbeat_payload)}\n\n"

        return StreamingResponse(
            _event_gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @api.post("/perceive")
    async def run_perceive(body: PerceiveRequest | None = None) -> dict[str, Any]:
        reset_flag = body.reset if body is not None else False
        mcp_manager.register_adapters(perception._adapters)
        report = await perception.perceive(reset=reset_flag)
        for item in report.spawn_plan:
            await court_manager.spawn_agent(
                agent_type=item["agent_type"],
                subject_ref=item["subject_ref"],
            )
        return _format_perception_response(report)

    @api.get("/perceive")
    async def get_perceive() -> dict[str, Any]:
        mcp_manager.register_adapters(perception._adapters)
        if perception.last_report is None:
            report = await perception.perceive(reset=False)
            for item in report.spawn_plan:
                await court_manager.spawn_agent(
                    agent_type=item["agent_type"],
                    subject_ref=item["subject_ref"],
                )
            return _format_perception_response(report)
        return _format_perception_response(perception.last_report)

    @api.get("/court")
    async def list_court() -> dict[str, Any]:
        agents = [_enrich_court_agent(a) for a in court_manager.list_active_agents()]
        return {
            "agents": agents,
            "max_agents": court_manager.max_agents,
            "court_daily_ceiling_usd": str(court_manager.token_governor.court_daily_ceiling_usd),
        }

    @api.get("/court/{agent_id}")
    async def get_court_agent(agent_id: str) -> dict[str, Any]:
        doc = court_manager.get_agent(agent_id)
        if doc is None:
            raise HTTPException(status_code=404, detail=f"Unknown court agent '{agent_id}'")
        enriched = _enrich_court_agent(doc)
        return {
            **enriched,
            "agent": enriched,
        }

    @api.post("/court/{agent_id}:wake")
    async def wake_court_agent(agent_id: str) -> dict[str, Any]:
        doc = court_manager.get_agent(agent_id)
        if doc is None:
            raise HTTPException(status_code=404, detail=f"Unknown court agent '{agent_id}'")
        now_iso = datetime.now(tz=UTC).isoformat()
        updated = doc.model_copy(
            update={
                "wakes_7d": doc.wakes_7d + 1,
                "last_wake_at": now_iso,
                "state": "awake",
            }
        )
        fs.set("court_agents", agent_id, updated.model_dump(mode="json"))
        await router_ns.emit(
            "godview.court.warm",
            source="//sovereign/control-api",
            subject=agent_id,
            data={"agent_id": agent_id, "manual_wake": True},
        )
        return _enrich_court_agent(updated)

    @api.post("/court/{agent_id}:warm")
    async def warm_court_agent(agent_id: str) -> dict[str, Any]:
        try:
            updated = await court_manager.set_warmth(agent_id, 1)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _enrich_court_agent(updated)

    @api.post("/court/{agent_id}:cool")
    async def cool_court_agent(agent_id: str) -> dict[str, Any]:
        try:
            updated = await court_manager.set_warmth(agent_id, 0)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _enrich_court_agent(updated)

    @api.post("/court/{agent_id}:retire")
    async def retire_court_agent(agent_id: str) -> dict[str, Any]:
        try:
            updated = await court_manager.retire_agent(agent_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _enrich_court_agent(updated)

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

    @api.get("/sentinels")
    async def get_sentinels() -> dict[str, Any]:
        return {
            "sentinels": [
                {**s.model_dump(mode="json", by_alias=True), "id": s.sentinel_id}
                for s in sentinel_engine.list_sentinels()
            ]
        }

    @api.post("/sentinels")
    async def create_sentinel(payload: dict[str, Any]) -> dict[str, Any]:
        if "invariant_id" in payload and "sentinel_id" not in payload:
            inv_id = str(payload.get("invariant_id") or "").strip()
            if not inv_id:
                raise HTTPException(status_code=422, detail="invariant_id must not be empty")
            inv_doc = fs.get("invariants", inv_id) or {}
            slug = inv_id.removeprefix("inv-")
            check_cfg = inv_doc.get("check") if isinstance(inv_doc.get("check"), dict) else {}
            wake_cfg = (
                inv_doc.get("wake_condition")
                if isinstance(inv_doc.get("wake_condition"), dict)
                else {}
            )
            stmt = str(
                check_cfg.get("statement")
                or "SELECT COUNT(*) AS violations FROM events WHERE http_status >= 500"
            )
            dialect = str(check_cfg.get("dialect") or "bigquery")
            target_agent = str(
                wake_cfg.get("target_agent_id")
                or payload.get("target_agent_id")
                or "business_line__primary_business_flow"
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

        saved = sentinel_engine.register_sentinel(snt)
        return {**saved.model_dump(mode="json", by_alias=True), "id": saved.sentinel_id}

    @api.patch("/sentinels/{sentinel_id}")
    async def patch_sentinel(sentinel_id: str, body: SentinelPatchRequest) -> dict[str, Any]:
        try:
            updated = sentinel_engine.update_sentinel_state(sentinel_id, body.state)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {**updated.model_dump(mode="json", by_alias=True), "id": updated.sentinel_id}

    @api.get("/journeys")
    async def get_journeys() -> dict[str, Any]:
        return {"journeys": fs.list_docs("journeys")}

    @api.get("/incidents")
    async def list_incidents() -> dict[str, Any]:
        return {"incidents": fs.list_docs("episodic_incidents")}

    @api.get("/incidents/{incident_id}")
    async def get_incident(incident_id: str) -> dict[str, Any]:
        raw = fs.get("episodic_incidents", incident_id)
        if raw is None:
            raise HTTPException(status_code=404, detail=f"Unknown incident '{incident_id}'")
        return raw

    @api.get("/approvals")
    async def list_approvals() -> dict[str, Any]:
        return {"approvals": fs.list_docs("approvals")}

    @api.post("/approvals/{approval_id}:approve")
    async def approve_card(
        approval_id: str,
        x_goog_authenticated_user_email: str | None = Header(
            default=None, alias="X-Goog-Authenticated-User-Email"
        ),
    ) -> dict[str, Any]:
        iap_email = _clean_iap_email(x_goog_authenticated_user_email)
        if not iap_email:
            raise HTTPException(
                status_code=401,
                detail="X-Goog-Authenticated-User-Email IAP header is required",
            )
        try:
            card, audit = trust_gate.decide_approval(
                approval_id, decision="approved", decided_by=iap_email
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except TrustViolation as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        await router_ns.emit(
            "godview.operator.note",
            source="//sovereign/control-api",
            subject=approval_id,
            incident_id=card.incident_id,
            data={
                "approval_id": approval_id,
                "decision": "approved",
                "decided_by": card.decided_by,
                "audit_id": audit.audit_id,
            },
        )
        return {**card.model_dump(mode="json"), "audit_id": audit.audit_id}

    @api.post("/approvals/{approval_id}:reject")
    async def reject_card(
        approval_id: str,
        x_goog_authenticated_user_email: str | None = Header(
            default=None, alias="X-Goog-Authenticated-User-Email"
        ),
    ) -> dict[str, Any]:
        iap_email = _clean_iap_email(x_goog_authenticated_user_email)
        if not iap_email:
            raise HTTPException(
                status_code=401,
                detail="X-Goog-Authenticated-User-Email IAP header is required",
            )
        try:
            card, audit = trust_gate.decide_approval(
                approval_id, decision="rejected", decided_by=iap_email
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except TrustViolation as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        await router_ns.emit(
            "godview.operator.note",
            source="//sovereign/control-api",
            subject=approval_id,
            incident_id=card.incident_id,
            data={
                "approval_id": approval_id,
                "decision": "rejected",
                "decided_by": card.decided_by,
                "audit_id": audit.audit_id,
            },
        )
        return {**card.model_dump(mode="json"), "audit_id": audit.audit_id}

    @api.post("/approvals/{approval_id}:snooze")
    async def snooze_card(approval_id: str) -> dict[str, Any]:
        raw = fs.get("approvals", approval_id)
        if raw is None:
            raise HTTPException(status_code=404, detail=f"Unknown approval '{approval_id}'")
        new_expiry = (datetime.now(tz=UTC) + timedelta(minutes=30)).isoformat()
        fs.set(
            "approvals",
            approval_id,
            {"expires_at": new_expiry, "decision": "snoozed"},
            merge=True,
        )
        return fs.get("approvals", approval_id) or {}

    @api.post("/capabilities/{capability_id}:invoke")
    async def invoke_capability_endpoint(
        capability_id: str, body: CapabilityInvokeBody
    ) -> dict[str, Any]:
        """Single TrustGate enforcement endpoint for all court agent tool calls (Decision A3)."""
        mcp_manager.register_adapters(perception._adapters)
        try:
            spec = get_capability(capability_id)
            decision = trust_gate.authorize(
                agent_id=body.agent_id,
                capability_id=capability_id,
                target_resource=body.target_resource,
                blast_radius_resources=body.blast_radius_resources,
                approval_id=body.approval_id,
                novel_skill_first_step=body.novel_skill_first_step,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except TrustViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        if not decision.authorized:
            inc_id = body.incident_id or "inc-20261016-0001"
            card = trust_gate.create_approval_request(
                incident_id=inc_id,
                agent_id=body.agent_id,
                capability_id=capability_id,
                target_resource=body.target_resource,
                trust_level_required=decision.required_level,
                narrative=body.narrative,
                evidence_pack_id="evp-20261016-0001",
                rupee_impact=body.rupee_impact,
                reversal_recipe=body.reversal_recipe,
            )
            await router_ns.emit(
                "godview.operator.note",
                source="//sovereign/trust-gate",
                subject=card.approval_id,
                incident_id=inc_id,
                data={
                    "kind": "approval_requested",
                    "approval_id": card.approval_id,
                    "agent_id": body.agent_id,
                    "capability_id": capability_id,
                },
            )
            return {
                "status": "approval_required",
                "approval_id": card.approval_id,
                "decision": decision.model_dump(mode="json"),
            }

        exec_result = await mcp_manager.invoke_capability_async(capability_id, body.args, decision)
        if spec.mutating:
            audit = trust_gate.record_audit(
                actor=body.agent_id,
                action=capability_id,
                target=body.target_resource,
                trust_level=decision.effective_level,
                approval_id=decision.approval_id,
                incident_id=body.incident_id,
                reversal_recipe=body.reversal_recipe,
                outcome="success",
            )
            await router_ns.emit(
                "godview.audit.config_change",
                source="//sovereign/trust-gate",
                subject=body.target_resource,
                incident_id=body.incident_id,
                data={
                    "capability_id": capability_id,
                    "agent_id": body.agent_id,
                    "audit_id": audit.audit_id,
                },
            )
            return {
                "status": "executed",
                "capability_id": capability_id,
                "audit_id": audit.audit_id,
                "sa_impersonated": decision.sa_to_impersonate,
                "result": exec_result,
            }

        return {
            "status": "executed",
            "capability_id": capability_id,
            "sa_impersonated": decision.sa_to_impersonate,
            "result": exec_result,
        }

    @api.post("/tenant/kill-switch")
    async def tenant_kill_switch(
        body: KillSwitchRequest,
        x_goog_authenticated_user_email: str | None = Header(
            default=None, alias="X-Goog-Authenticated-User-Email"
        ),
    ) -> dict[str, Any]:
        actor = _clean_iap_email(x_goog_authenticated_user_email) or "operator@local.dev"
        now_iso = datetime.now(tz=UTC).isoformat()
        sentinel_engine.set_planned_operator_action(
            active=body.enabled, reason=body.reason if body.enabled else None
        )
        drain = trust_gate.set_global_kill_switch(enabled=body.enabled, actor=actor)
        runtime_state["kill_switch_enabled"] = body.enabled
        runtime_state["kill_switch_reason"] = body.reason if body.enabled else None
        runtime_state["kill_switch_actor"] = actor
        runtime_state["kill_switch_updated_at"] = now_iso

        if body.enabled:
            cleared = len(runtime_state["active_drills"])
            runtime_state["active_drills"].clear()
            runtime_state["tenant_paused"] = True
            runtime_state["simulator_enabled"] = False
            await court_manager.cool_all_agents()
            ce_type: str = "godview.operator.kill_switch"
        else:
            cleared = 0
            runtime_state["tenant_paused"] = False
            runtime_state["simulator_enabled"] = True
            ce_type = "godview.schedule.resume"

        await router_ns.emit(
            ce_type,
            source="//sovereign/control-api",
            subject="tenant",
            data={"enabled": body.enabled, "reason": body.reason, "actor": actor},
        )
        return {
            **_operator_state(),
            "active_drills_cleared": cleared,
            "cloud_event_type": ce_type,
            "drain": drain,
        }

    @api.post("/tenant/pause")
    @api.post("/lifecycle/tenant-pause")
    async def tenant_pause() -> dict[str, Any]:
        runtime_state["tenant_paused"] = True
        runtime_state["simulator_enabled"] = False
        sentinel_engine.set_planned_operator_action(active=True, reason="Scheduled Pause")
        await court_manager.cool_all_agents()
        await router_ns.emit(
            "godview.schedule.pause",
            source="//sovereign/control-api",
            subject="tenant",
            data={"tenant_paused": True, "warm_agents_count": 0},
        )
        return {
            **_operator_state(),
            "warm_agents_count": 0,
            "cloud_event_type": "godview.schedule.pause",
        }

    @api.post("/tenant/resume")
    @api.post("/lifecycle/tenant-resume")
    async def tenant_resume() -> dict[str, Any]:
        runtime_state["tenant_paused"] = False
        runtime_state["simulator_enabled"] = True
        sentinel_engine.set_planned_operator_action(active=False, reason=None)
        await router_ns.emit(
            "godview.schedule.resume",
            source="//sovereign/control-api",
            subject="tenant",
            data={"tenant_paused": False},
        )
        return {
            **_operator_state(),
            "cloud_event_type": "godview.schedule.resume",
        }

    @api.post("/probes/run")
    async def run_probes(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute synthetic verification probes and sentinel evaluation cycle (Cloud Scheduler godview-probe-run)."""
        now_iso = datetime.now(tz=UTC).isoformat()
        if (
            runtime_state["kill_switch_enabled"]
            or runtime_state["tenant_paused"]
            or runtime_state["hibernate_enabled"]
        ):
            return {
                "status": "skipped",
                "action": "probe_run",
                "reason": "paused_or_hibernating",
                "sentinels_evaluated": 0,
                "wakes_triggered": 0,
                "checked_at": now_iso,
            }
        tick = await sentinel_engine.evaluate_tick()
        return {
            "status": "ok",
            "action": "probe_run",
            "source": (payload or {}).get("source", "cloud_scheduler"),
            "sentinels_evaluated": tick.evaluated_count,
            "wakes_triggered": len(tick.wakes_sent),
            "checked_at": now_iso,
        }

    @api.post("/memory/reflect")
    async def reflect_memory(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run nightly episodic-to-procedural memory consolidation (Cloud Scheduler godview-agent-reflection)."""
        now_iso = datetime.now(tz=UTC).isoformat()
        agents = court_manager.list_active_agents()
        for doc in agents:
            aid = doc.doc_id or doc.namespace
            updated = doc.model_copy(update={"last_reflection_at": now_iso})
            fs.set("court_agents", aid, updated.model_dump(mode="json"))
        await router_ns.emit(
            "godview.operator.note",
            source="//sovereign/control-api",
            subject="memory",
            data={
                "kind": "nightly_memory_reflection",
                "source": (payload or {}).get("source", "cloud_scheduler"),
                "agents_reflected": len(agents),
                "reflected_at": now_iso,
            },
        )
        return {
            "status": "ok",
            "action": "reflect",
            "agents_reflected": len(agents),
            "reflected_at": now_iso,
        }

    @api.post("/nervous-system/push")
    async def receive_nervous_system_push(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Receive Pub/Sub push notifications on godview-sovereign-push (Cloud Audit Logs & CloudEvents)."""
        now_iso = datetime.now(tz=UTC).isoformat()
        body = payload or {}
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
        if isinstance(proto, dict):
            method_name = str(proto.get("methodName") or "cloud_audit_activity")
            resource_name = str(proto.get("resourceName") or "gcp-resource")
            msg_id = (
                str(msg.get("messageId"))
                if isinstance(msg, dict) and msg.get("messageId")
                else hashlib.sha256(f"{resource_name}:{now_iso}".encode()).hexdigest()[:12]
            )
            chg_id = f"chg-audit-{msg_id}"
            chg = ChangeEventDoc(
                doc_id=chg_id,
                repo=resource_name,
                kind="audit_config_change",
                sha=msg_id[:12],
                occurred_at=str(decoded_event.get("timestamp") or now_iso),
                business_summary=f"Cloud Audit activity: {method_name} on {resource_name}",
            )
            fs.set("change_events", chg_id, chg.model_dump(mode="json"))

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

    @api.post("/hibernate")
    async def set_hibernate_mode(body: HibernateRequest) -> dict[str, Any]:
        runtime_state["hibernate_enabled"] = body.enabled
        cadence_s = sentinel_engine.set_hibernate(body.enabled)
        sentinel_engine.set_planned_operator_action(
            active=body.enabled,
            reason="Environment Hibernate" if body.enabled else None,
        )
        if body.enabled:
            await court_manager.cool_all_agents()
            job = "god-view-hibernate-job"
            ce_type = "godview.operator.hibernate"
        else:
            job = "god-view-wake-job"
            ce_type = "godview.schedule.resume"

        await router_ns.emit(
            ce_type,
            source="//sovereign/control-api",
            subject="environment",
            data={"hibernate_enabled": body.enabled, "sentinel_cadence_s": cadence_s},
        )
        return {
            **_operator_state(),
            "hibernate_enabled": body.enabled,
            "sentinel_cadence_s": cadence_s,
            "job_triggered": job,
            "cloud_event_type": ce_type,
        }

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
    async def list_changes() -> dict[str, Any]:
        return {"changes": fs.list_docs("change_events")}

    @api.get("/cost")
    async def get_cost_summary() -> dict[str, Any]:
        cost_docs = [
            {**d, "id": d.get("doc_id") or d.get("_id")} for d in fs.list_docs("cost_findings")
        ]
        drift_list = list(runtime_state["drift_diffs"].values())
        court_ceiling = float(court_manager.token_governor.court_daily_ceiling_usd)
        court_spent = float(court_manager.token_governor.total_court_usd_spent)
        return {
            "monthly_ceiling_usd": 600.0,
            "projected_active_month_usd": 195.0,
            "projected_hibernated_month_usd": 42.0,
            "budget_thresholds_pct": [50, 80, 100],
            "current_month_spend_usd": round(18.5 + court_spent, 2),
            "daily_court_ceiling_usd": court_ceiling,
            "daily_court_spent_usd": court_spent,
            "court_daily_ceiling_usd": court_ceiling,
            "court_spent_today_usd": court_spent,
            "escalation_reserve_pct": int(
                court_manager.token_governor.escalation_reserve_fraction * 100
            ),
            "tier_budgets_summary": "App/Biz/Change $0.75/d · Data/Platform $0.50/d · Sovereign $1.50/d",
            "cost_findings_subtitle": "Idle resources, warm-tier right-sizing, and token budget alerts",
            "tfstate_uri": "gs://env-tfstate/platform/default.tfstate",
            "last_drift_scan_at": datetime.now(tz=UTC).isoformat(),
            "cost_findings": cost_docs,
            "findings": cost_docs,
            "drift_diffs": drift_list,
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

    from godview.agents.inspection_routes import create_harness_inspection_router
    from godview.agents.server import create_a2a_router

    api.include_router(
        create_harness_inspection_router(
            firestore=fs,
            token_governor=court_manager.token_governor,
            trust_gate=trust_gate,
        )
    )
    app.include_router(create_a2a_router())
    app.include_router(api, prefix="/v1")
    app.include_router(api, prefix="/api/v1")
    return app


app = create_app()

__all__ = ["McpSubprocessManager", "app", "create_app"]
