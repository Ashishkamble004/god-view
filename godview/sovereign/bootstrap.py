"""Composition root for God-View Sovereign SRE Control Plane (`godview.sovereign.bootstrap`).

Implements TDD §1.2, §3.1 and Coordinator Decisions A1-A18:
- SovereignContext dataclass containing settings, stores, adapters, auth, llm, clock, hooks.
- build_context(env) constructing the appropriate context for hermetic, local, or gcp mode.
- Strict startup assertion in gcp mode: fails closed (exit 1) if any in-memory store
  is used or zero adapters are healthy.
- create_app(ctx) initializing FastAPI with lifespan background loops and routers auto-inclusion.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from godview_shared.adapter_contract import SenseAdapter
from godview_shared.auth import AuthService
from godview_shared.config import GodViewSettings
from godview_shared.contracts import ChangeEventDoc
from godview_shared.llm import LlmClient
from godview_shared.stores.protocols import (
    AgentRuntime,
    DocumentStore,
    EventBus,
    LedgerStore,
)

logger = logging.getLogger("godview.sovereign.bootstrap")


@dataclass
class SovereignHooks:
    """Extension hooks registered by slices on the sovereign context."""

    perception_tick: list[Callable[[], Awaitable[None] | None]] = field(default_factory=list)
    sentinel_tick: list[Callable[[], Awaitable[None] | None]] = field(default_factory=list)
    on_change_event: list[Callable[[ChangeEventDoc | dict[str, Any]], Awaitable[None] | None]] = field(
        default_factory=list
    )
    on_incident: list[Callable[[Any], Awaitable[None] | None]] = field(default_factory=list)
    on_incident_closed: list[Callable[[Any], Awaitable[None] | None]] = field(default_factory=list)
    on_approval_decided: list[Callable[[Any], Awaitable[None] | None]] = field(default_factory=list)
    agent_warmth_tick: list[Callable[[], Awaitable[None] | None]] = field(default_factory=list)
    dlq_watchdog_tick: list[Callable[[], Awaitable[None] | None]] = field(default_factory=list)


@dataclass
class SovereignContext:
    """The central composition context for God-View Sovereign."""

    settings: GodViewSettings
    docs: DocumentStore
    ledger: LedgerStore
    bus: EventBus
    agents: AgentRuntime
    adapters: list[SenseAdapter]
    auth: AuthService
    llm: LlmClient
    clock: Callable[[], datetime]
    hooks: SovereignHooks = field(default_factory=SovereignHooks)
    loop_heartbeats: dict[str, datetime] = field(default_factory=dict)
    last_perception_at: datetime | None = None
    sentinels_registered: int = 0
    court_orchestrator: Any | None = None
    executors: dict[str, Callable[..., Any]] = field(default_factory=dict)


def _default_clock() -> datetime:
    return datetime.now(tz=UTC)


def _fetch_metadata(path: str) -> str | None:
    import urllib.request

    url = f"http://metadata.google.internal/computeMetadata/v1/{path.lstrip('/')}"
    try:
        req = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.read().decode("utf-8").strip()
    except Exception:
        return None


def build_context(env: Mapping[str, str] | None = None) -> SovereignContext:
    """Build and return a SovereignContext based on environment mode."""
    env_source = env if env is not None else os.environ
    settings = GodViewSettings.from_env(env_source)
    auth = AuthService(settings=settings)
    llm = LlmClient(settings=settings, auth=auth)
    clock = _default_clock

    if settings.env_mode == "hermetic":
        from godview_shared.fakes import (
            InMemoryAgentEngine,
            InMemoryBigQuery,
            InMemoryFirestore,
            InMemoryPubSub,
        )

        docs: DocumentStore = InMemoryFirestore()
        ledger: LedgerStore = InMemoryBigQuery()
        bus: EventBus = InMemoryPubSub()
        agents: AgentRuntime = InMemoryAgentEngine()
        adapters: list[SenseAdapter] = []
        try:
            from godview.adapters.local.adapter import LocalEnvironmentSenseAdapter

            adapters = [LocalEnvironmentSenseAdapter()]
        except Exception:
            adapters = []

        return SovereignContext(
            settings=settings,
            docs=docs,
            ledger=ledger,
            bus=bus,
            agents=agents,
            adapters=adapters,
            auth=auth,
            llm=llm,
            clock=clock,
        )

    if settings.env_mode == "gcp":
        # Production GCP mode
        from godview_shared.fakes import (
            InMemoryAgentEngine,
            InMemoryBigQuery,
            InMemoryFirestore,
            InMemoryPubSub,
        )
        from godview_shared.stores.agent_engine_runtime import AgentEngineRuntime
        from godview_shared.stores.bigquery_ledger import BigQueryLedger
        from godview_shared.stores.firestore_store import FirestoreStore
        from godview_shared.stores.pubsub_bus import PubSubBus

        project_id = (
            env_source.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or _fetch_metadata("project/project-id")
            or ""
        )
        if not project_id:
            err_msg = "FATAL: Production mode GODVIEW_ENV=gcp requires project_id from GOOGLE_CLOUD_PROJECT or metadata server."
            logger.critical(err_msg)
            sys.stderr.write(err_msg + "\n")
            sys.exit(1)
        fs_db = env_source.get("FIRESTORE_DATABASE") or "god-view-memory-db"
        bq_dataset = env_source.get("BIGQUERY_LEDGER_DATASET") or "godview_ledger"
        ps_topic = env_source.get("NERVOUS_SYSTEM_TOPIC") or "godview-nervous-system"

        docs = FirestoreStore(project=project_id, database=fs_db)
        ledger = BigQueryLedger(project=project_id, dataset=bq_dataset)
        bus = PubSubBus(project=project_id, topic=ps_topic)
        agents = AgentEngineRuntime(project=project_id, auth=auth)

        adapters = []
        try:
            from godview.adapters.gcp import build_all_gcp_adapters

            regions = [r.strip() for r in env_source.get("GODVIEW_REGIONS", "us-central1").split(",") if r.strip()]
            adapters = list(build_all_gcp_adapters(project_id=project_id, regions=regions, auth=auth))
        except Exception as exc:
            logger.warning("Could not auto-load GCP adapters: %s", exc)

        # Health check on adapters (evaluating await adapter.health())
        import asyncio

        async def _check_health(adapter_list: list[Any]) -> list[Any]:
            healthy = []
            for a in adapter_list:
                try:
                    if hasattr(a, "health") and callable(a.health):
                        h = a.health()
                        if asyncio.iscoroutine(h):
                            h_res = await asyncio.wait_for(h, timeout=2.0)
                        else:
                            h_res = h
                        st = getattr(h_res, "state", "available")
                        if st in ("available", "partial"):
                            healthy.append(a)
                    elif getattr(a, "healthy", True):
                        healthy.append(a)
                except Exception as ex:
                    logger.warning("Adapter %s health check failed: %s", getattr(a, "name", a), ex)
            return healthy

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                healthy_adapters = pool.submit(lambda: asyncio.run(_check_health(adapters))).result(timeout=10.0)
        else:
            healthy_adapters = asyncio.run(_check_health(adapters))

        # STARTUP ASSERTION (Binding: §0, §3.1)
        # GODVIEW_ENV=gcp must exit(1) if any InMemory* store or zero adapters healthy
        if (
            isinstance(docs, InMemoryFirestore)
            or isinstance(ledger, InMemoryBigQuery)
            or isinstance(bus, InMemoryPubSub)
            or isinstance(agents, InMemoryAgentEngine)
            or len(healthy_adapters) == 0
        ):
            err_msg = (
                f"FATAL: Production mode GODVIEW_ENV=gcp requires real GCP stores and at least one healthy adapter. "
                f"docs={type(docs).__name__}, ledger={type(ledger).__name__}, "
                f"bus={type(bus).__name__}, agents={type(agents).__name__}, healthy_adapters_count={len(healthy_adapters)}"
            )
            logger.critical(err_msg)
            sys.stderr.write(err_msg + "\n")
            sys.exit(1)

        return SovereignContext(
            settings=settings,
            docs=docs,
            ledger=ledger,
            bus=bus,
            agents=agents,
            adapters=adapters,
            auth=auth,
            llm=llm,
            clock=clock,
        )

    # local mode per Plan §1.1: LocalDocumentStore / DuckDB ledger + local adapters
    from godview_shared.fakes import InMemoryAgentEngine, InMemoryPubSub
    from godview_shared.stores.duckdb_ledger import DuckDBLedger
    from godview_shared.stores.local_document_store import LocalDocumentStore

    local_adapters: list[SenseAdapter] = []
    try:
        from godview.adapters.local.adapter import LocalEnvironmentSenseAdapter

        local_adapters = [LocalEnvironmentSenseAdapter()]
    except Exception as exc:
        logger.warning("Could not auto-load local adapters: %s", exc)

    return SovereignContext(
        settings=settings,
        docs=LocalDocumentStore(),
        ledger=DuckDBLedger(),
        bus=InMemoryPubSub(),
        agents=InMemoryAgentEngine(),
        adapters=local_adapters,
        auth=auth,
        llm=llm,
        clock=clock,
    )


def create_app(ctx: SovereignContext | None = None, **kwargs: Any) -> FastAPI:
    """Create Sovereign FastAPI app using SovereignContext and routers convention."""
    from godview.sovereign.control_api.app import create_app as _create_app

    return _create_app(ctx=ctx, **kwargs)
