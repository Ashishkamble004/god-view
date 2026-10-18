"""Environment Perception Organ (`godview.sovereign.perception`).

Implements SDD §2.1 & Agent Spec §1 6-step identity-only self-awareness protocol,
TDD I-02 (zero hardcoded application/tenant knowledge), Plan §3.2 (error-isolated senses,
evidence-only business context inference, canonical topology graph, and zero placeholder nodes).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from godview_shared.adapter_contract import AdapterHealth, SenseAdapter
from godview_shared.config import GodViewSettings, is_excluded_resource
from godview_shared.contracts import (
    BusinessContext,
    DiscoveredEdgeDoc,
    DiscoveredEntityDoc,
    DiscoveredServiceDoc,
    JourneyDoc,
)
from pydantic import BaseModel, Field

from godview.adapters.gcp import build_all_gcp_adapters
from godview.sovereign.business_context.inferencer import BusinessContextInferencer
from godview.sovereign.nervous_system import NervousSystemRouter
from godview.sovereign.topology.builder import TopologyBuilder, TopologyReport

logger = logging.getLogger("godview.sovereign.perception")


def resolve_runtime_identity(settings: GodViewSettings) -> dict[str, Any]:
    """Resolve runtime identity honestly from metadata server or environment."""
    principal = os.environ.get("GODVIEW_RUNTIME_PRINCIPAL")
    if not principal:
        try:
            resp = httpx.get(
                "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email",
                headers={"Metadata-Flavor": "Google"},
                timeout=1.0,
            )
            if resp.status_code == 200 and resp.text.strip():
                principal = resp.text.strip()
        except Exception:
            pass
    if not principal:
        principal = os.environ.get("GOOGLE_SERVICE_ACCOUNT_EMAIL", "")

    return {
        "principal": principal or "unauthenticated",
        "env_mode": settings.env_mode,
        "hint_repo_urls": list(settings.hint_repo_urls),
    }


def resolve_env_id(settings: GodViewSettings) -> str:
    """Resolve the canonical env_id (e.g. gcp-<project_id> or hermetic)."""
    if settings.env_mode == "hermetic":
        return "hermetic"
    project = (
        settings.gcp_project_id
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT_ID")
    )
    if project:
        return f"gcp-{project}"
    return f"gcp-{settings.env_mode}"


class PerceptionReport(BaseModel):
    """Structured output of a 6-step PerceptionOrgan pass."""

    env_id: str
    perceived_at: str
    identity: dict[str, Any]
    adapter_health: list[AdapterHealth] = Field(default_factory=list)
    agent_registry_discovered: bool = False
    services_count: int = 0
    edges_count: int = 0
    entities_count: int = 0
    journeys_count: int = 0
    spawn_plan: list[dict[str, Any]] = Field(default_factory=list)
    vanished_service_ids: list[str] = Field(default_factory=list)


class PerceptionOrgan:
    """Identity-only 6-step self-awareness and continuous re-perception organ."""

    def __init__(
        self,
        firestore: Any,
        pubsub: Any,
        adapters: Sequence[SenseAdapter] | None = None,
        settings: GodViewSettings | None = None,
        llm: Any | None = None,
        inferencer: BusinessContextInferencer | None = None,
        topology_builder: TopologyBuilder | None = None,
    ) -> None:
        self._firestore = firestore
        self._pubsub = pubsub
        self._adapters: list[SenseAdapter] = list(adapters) if adapters is not None else []
        self._settings = settings or GodViewSettings.from_env()
        self._router = NervousSystemRouter(pubsub=pubsub)
        self._llm = llm
        self._inferencer = inferencer or BusinessContextInferencer(llm=llm, firestore=firestore)
        self._env_id = resolve_env_id(self._settings)
        self._topology_builder = topology_builder or TopologyBuilder(env_id=self._env_id)
        self._last_report: PerceptionReport | None = None
        self._last_topology: TopologyReport | None = None

    @property
    def last_report(self) -> PerceptionReport | None:
        return self._last_report

    @property
    def last_topology(self) -> TopologyReport | None:
        return self._last_topology

    async def perceive(self, *, reset: bool = False) -> PerceptionReport:
        """Execute the 6-step self-awareness protocol (SDD §2.1 & Plan §3.2)."""
        now_iso = datetime.now(tz=UTC).isoformat()

        # Step 1: Introspect runtime identity honestly
        identity = resolve_runtime_identity(self._settings)
        env_id = self._env_id
        self._router.env_id = env_id

        # Step 2 & 3: Probe sense adapters with error isolation & discover agent registry
        health_reports: list[AdapterHealth] = []
        healthy_families: set[str] = set()
        agent_registry_discovered = False
        discovered_services: list[DiscoveredServiceDoc] = []
        discovered_edges: list[DiscoveredEdgeDoc] = []
        discovered_entities: list[DiscoveredEntityDoc] = []

        for adapter in self._adapters:
            try:
                h = await adapter.health()
                health_reports.append(h)
                if h.state == "available":
                    healthy_families.add(getattr(adapter, "family", "gcp"))
                if h.evidence.get("agent_registry_available") is True:
                    agent_registry_discovered = True
                if h.state in ("available", "partial"):
                    disc = await adapter.discover()
                    discovered_services.extend(disc.services)
                    discovered_edges.extend(disc.edges)
                    discovered_entities.extend(disc.entities)
            except Exception as exc:
                logger.warning(
                    "Sense adapter '%s' probe failed: %s",
                    getattr(adapter, "name", "unknown"),
                    exc,
                )
                health_reports.append(
                    AdapterHealth(
                        adapter_name=getattr(adapter, "name", "unknown"),
                        family=getattr(adapter, "family", "gcp"),
                        state="unavailable",
                        evidence={"error": str(exc)},
                        checked_at=datetime.now(tz=UTC),
                    )
                )

        # Feed real discovered hosts into probing adapters
        discovered_hostnames: set[str] = set()
        for s in discovered_services:
            loc = getattr(s, "locator", None) or ""
            if "://" in loc:
                h = (urlsplit(loc).hostname or "").lower().rstrip(".")
                if h:
                    discovered_hostnames.add(h)
        if discovered_hostnames:
            for adapter in self._adapters:
                if hasattr(adapter, "_discovered_hosts") and isinstance(adapter._discovered_hosts, set):
                    adapter._discovered_hosts.update(discovered_hostnames)

        # In hermetic mode, honour environment ID from discovered services or existing store
        if self._env_id == "hermetic":
            for s in discovered_services:
                if s.env_id and s.env_id != "hermetic":
                    self._env_id = s.env_id
                    break
            else:
                if hasattr(self._firestore, "list_docs"):
                    for stored in self._firestore.list_docs("discovered_services"):
                        st_env = stored.get("env_id")
                        if st_env and st_env != "hermetic":
                            self._env_id = st_env
                            break

        env_id = self._env_id
        self._router.env_id = env_id
        if hasattr(self._topology_builder, "env_id"):
            self._topology_builder.env_id = env_id

        # Step 4: Classify resources and infer business context from evidence
        edge_nodes: set[str] = set()
        for e in discovered_edges:
            edge_nodes.add(e.src)
            edge_nodes.add(e.dst)

        filtered_services: list[DiscoveredServiceDoc] = []
        for svc in discovered_services:
            if is_excluded_resource(svc.name, svc.labels, hint_exclude=self._settings.hint_exclude):
                continue
            filtered_services.append(svc)

        seen_service_doc_ids: set[str] = set()
        for svc in filtered_services:
            doc_id = svc.doc_id or f"{svc.env_id}__{svc.kind}.{svc.name}"
            seen_service_doc_ids.add(doc_id)
            is_managed = svc.labels.get("godview_managed") == "true" or doc_id in edge_nodes
            is_observer = (
                svc.labels.get("godview_role") == "observer"
                or svc.classification == "observer"
                or is_excluded_resource(svc.name, svc.labels)
            )
            classification = "observer" if is_observer else ("journey_linked" if is_managed else "other_read_only")
            if is_observer:
                svc.labels["godview_role"] = "observer"

            # Check if business context already exists or can be inferred
            business_ctx = svc.business_context
            if business_ctx is None and self._inferencer is not None:
                # Check stored doc first for cached business context
                existing_doc = self._firestore.get("discovered_services", doc_id) if hasattr(self._firestore, "get") else None
                if existing_doc and existing_doc.get("business_context"):
                    try:
                        business_ctx = BusinessContext(**existing_doc["business_context"])
                    except Exception:
                        business_ctx = None

                if business_ctx is None:
                    try:
                        business_ctx = await self._inferencer.infer_service_context(
                            svc, discovered_edges
                        )
                    except Exception as exc:
                        logger.warning(
                            "Business context inference failed for '%s': %s",
                            svc.name,
                            exc,
                        )

            embedding = svc.purpose_embedding
            if embedding is None and self._llm is not None and hasattr(self._llm, "embed"):
                embed_text = (business_ctx.role_summary if business_ctx else "") or (svc.inferred_role or "")
                if embed_text:
                    try:
                        vecs = await self._llm.embed([embed_text])
                        if vecs and len(vecs[0]) == 768:
                            embedding = vecs[0]
                    except Exception:
                        embedding = None

            existing = self._firestore.get("discovered_services", doc_id) if hasattr(self._firestore, "get") else None
            updated_doc = svc.model_copy(
                update={
                    "doc_id": doc_id,
                    "classification": classification,
                    "inferred_role": svc.inferred_role,
                    "role_confidence": svc.role_confidence,
                    "purpose_embedding": embedding,
                    "business_context": business_ctx,
                    "status": "active",
                    "last_seen": now_iso,
                }
            )
            if hasattr(self._firestore, "set"):
                self._firestore.set(
                    "discovered_services",
                    doc_id,
                    updated_doc.model_dump(mode="json"),
                )
            if existing is None or reset:
                await self._router.emit(
                    "godview.audit.config_change",
                    source="//sovereign/perception",
                    subject=doc_id,
                    data={
                        "event": "service_found",
                        "service_id": doc_id,
                        "name": svc.name,
                        "kind": svc.kind,
                    },
                )

        # Step 6: Continuous re-perception diff — mark disappeared services as 'gone'
        # Only diff resources from adapters that were healthy this pass
        vanished_ids: list[str] = []
        if hasattr(self._firestore, "list_docs"):
            for stored in self._firestore.list_docs("discovered_services"):
                sid = stored.get("_id") or stored.get("doc_id")
                if not sid:
                    continue
                stored_family = stored.get("adapter_family") or stored.get("family", "gcp")
                if stored_family not in healthy_families:
                    # Adapter was unavailable or partial; do NOT mark gone!
                    continue
                if stored.get("env_id") == env_id and sid not in seen_service_doc_ids:
                    if stored.get("status") != "gone":
                        self._firestore.set(
                            "discovered_services",
                            sid,
                            {"status": "gone", "last_seen": now_iso},
                            merge=True,
                        )
                        vanished_ids.append(sid)
                        await self._router.emit(
                            "godview.audit.config_change",
                            source="//sovereign/perception",
                            subject=sid,
                            data={
                                "event": "service_gone",
                                "service_id": sid,
                                "name": stored.get("name", sid),
                            },
                        )

        # Persist discovered edges & entities
        if hasattr(self._firestore, "set"):
            for edge in discovered_edges:
                edge_id = edge.doc_id or f"{edge.src}__to__{edge.dst}"
                self._firestore.set(
                    "discovered_edges",
                    edge_id,
                    edge.model_copy(update={"doc_id": edge_id}).model_dump(mode="json"),
                )

            for ent in discovered_entities:
                ent_id = ent.doc_id or f"{env_id}__{ent.name}"
                self._firestore.set(
                    "discovered_entities",
                    ent_id,
                    ent.model_copy(update={"doc_id": ent_id}).model_dump(mode="json"),
                )

        # Persist journeys from entities or inferred trace DAGs (zero placeholder flow)
        journey_keys: list[str] = []
        if hasattr(self._firestore, "set"):
            for ent in discovered_entities:
                j_key = f"{ent.name}_journey"
                journey_keys.append(j_key)
                j_doc = JourneyDoc(
                    doc_id=j_key,
                    name=j_key,
                    steps=list(ent.states) if ent.states else [],
                    intended_order_source="perception",
                    owner_agent_id=f"business_line__{j_key}",
                    status="active",
                )
                self._firestore.set("journeys", j_key, j_doc.model_dump(mode="json"))

            if self._inferencer is not None:
                inferred_journeys = await self._inferencer.infer_journeys_from_topology(
                    filtered_services, discovered_edges
                )
                for ij in inferred_journeys:
                    if ij.name not in journey_keys:
                        journey_keys.append(ij.name)
                        self._firestore.set("journeys", ij.doc_id, ij.model_dump(mode="json"))

        # Step 5: Assemble court spawn plan (evidence-only, zero placeholders)
        spawn_plan: list[dict[str, Any]] = []
        repos_seen: set[str] = set()

        for svc in filtered_services:
            doc_id = svc.doc_id or f"{svc.env_id}__{svc.kind}.{svc.name}"
            stored_svc = (
                self._firestore.get("discovered_services", doc_id)
                if hasattr(self._firestore, "get")
                else {}
            ) or {}
            if svc.kind == "cloud_sql":
                spawn_plan.append(
                    {
                        "agent_type": "data",
                        "subject_ref": svc.name,
                        "agent_id": f"data__{svc.name}",
                    }
                )
            elif stored_svc.get("classification") == "journey_linked":
                spawn_plan.append(
                    {
                        "agent_type": "application",
                        "subject_ref": svc.name,
                        "agent_id": f"application__{svc.name}",
                    }
                )
            if svc.repo_ref:
                repo_slug = svc.repo_ref.rstrip("/").split("/")[-1]
                if repo_slug not in repos_seen:
                    repos_seen.add(repo_slug)
                    spawn_plan.append(
                        {
                            "agent_type": "change",
                            "subject_ref": repo_slug,
                            "agent_id": f"change__{repo_slug}",
                        }
                    )

        for j_key in journey_keys:
            spawn_plan.append(
                {
                    "agent_type": "business_line",
                    "subject_ref": j_key,
                    "agent_id": f"business_line__{j_key}",
                }
            )

        spawn_plan.append(
            {
                "agent_type": "platform",
                "subject_ref": env_id,
                "agent_id": f"platform__{env_id}",
            }
        )

        # Build canonical topology report for cockpit with real incidents and golden signals
        stored_journeys: list[JourneyDoc] = []
        incidents_by_node: dict[str, list[dict[str, Any]]] = {}
        golden_by_node: dict[str, dict[str, Any]] = {}
        if hasattr(self._firestore, "list_docs"):
            for j_raw in self._firestore.list_docs("journeys"):
                try:
                    stored_journeys.append(JourneyDoc(**j_raw))
                except Exception:
                    pass

            for inc in self._firestore.list_docs("incidents"):
                if inc.get("status") in ("open", "escalated", "investigating"):
                    target_node = inc.get("node_id") or inc.get("resource_id") or inc.get("entity_id") or ""
                    services = inc.get("services") or []
                    keys = [target_node] + list(services)
                    for k in keys:
                        if k:
                            incidents_by_node.setdefault(k, []).append(inc)

            for g in self._firestore.list_docs("golden_signals"):
                node = g.get("node_id") or g.get("service") or g.get("_id")
                if node:
                    golden_by_node[node] = g

        topology_report = self._topology_builder.build(
            services=filtered_services,
            edges=discovered_edges,
            journeys=stored_journeys,
            incidents=incidents_by_node,
            golden_signals=golden_by_node,
        )
        self._last_topology = topology_report

        report = PerceptionReport(
            env_id=env_id,
            perceived_at=now_iso,
            identity=identity,
            adapter_health=health_reports,
            agent_registry_discovered=agent_registry_discovered,
            services_count=len(filtered_services),
            edges_count=len(discovered_edges),
            entities_count=len(discovered_entities),
            journeys_count=len(journey_keys),
            spawn_plan=spawn_plan,
            vanished_service_ids=vanished_ids,
        )
        self._last_report = report
        return report


def install(ctx: Any) -> None:
    """Install the PerceptionOrgan onto SovereignContext (Plan §1.2 & §3.2)."""
    settings = (
        getattr(ctx, "settings", None)
        or getattr(ctx, "config", None)
        or GodViewSettings.from_env()
    )
    auth = getattr(ctx, "auth", None)
    http_transport = getattr(ctx, "http_transport", None)

    adapters = getattr(ctx, "adapters", None)
    if not adapters:
        regions = getattr(settings, "gcp_regions", None) or [getattr(settings, "gcp_region", "us-central1")]
        adapters = build_all_gcp_adapters(
            project_id=getattr(settings, "gcp_project_id", None),
            regions=regions,
            region=getattr(settings, "gcp_region", "us-central1"),
            auth=auth,
            http_transport=http_transport,
        )

    firestore = getattr(ctx, "docs", None) or getattr(ctx, "store", None)
    pubsub = getattr(ctx, "bus", None)
    llm = getattr(ctx, "llm", None)

    env_id = resolve_env_id(settings)
    builder = TopologyBuilder(env_id=env_id)
    inferencer = BusinessContextInferencer(llm=llm, firestore=firestore)

    organ = PerceptionOrgan(
        firestore=firestore,
        pubsub=pubsub,
        adapters=adapters,
        settings=settings,
        llm=llm,
        inferencer=inferencer,
        topology_builder=builder,
    )
    if hasattr(ctx, "__dict__"):
        ctx.perception = organ

    # Register perception hook on ctx.hooks.perception_tick idempotently
    if hasattr(ctx, "hooks") and hasattr(ctx.hooks, "perception_tick"):
        async def _organ_perceive_hook() -> None:
            await organ.perceive()
            if hasattr(ctx, "last_perception_at"):
                ctx.last_perception_at = datetime.now(tz=UTC)

        existing_names = {getattr(fn, "__name__", "") for fn in ctx.hooks.perception_tick}
        if "_organ_perceive_hook" not in existing_names:
            ctx.hooks.perception_tick.append(_organ_perceive_hook)


__all__ = ["PerceptionOrgan", "PerceptionReport", "install", "resolve_env_id", "resolve_runtime_identity"]
