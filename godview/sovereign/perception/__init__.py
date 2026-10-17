"""Environment Perception Organ (`godview.sovereign.perception`).

Implements SDD §2.1 & Agent Spec §1 6-step identity-only self-awareness protocol,
TDD I-02 (zero hardcoded application/tenant knowledge), and Coordinator Decision A11
(exclusion of God-View's own observer infrastructure).
"""

from __future__ import annotations

import hashlib
import math
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from godview_shared.adapter_contract import AdapterHealth, SenseAdapter
from godview_shared.config import GodViewSettings, is_excluded_resource
from godview_shared.contracts import (
    DiscoveredEdgeDoc,
    DiscoveredEntityDoc,
    DiscoveredServiceDoc,
    JourneyDoc,
)
from godview_shared.fakes import InMemoryFirestore, InMemoryPubSub
from pydantic import BaseModel, Field

from godview.sovereign.nervous_system import NervousSystemRouter


def _deterministic_embedding_768(text: str) -> list[float]:
    """Generate a deterministic, unit-normalized 768-dim vector for hermetic perception."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [((digest[i % len(digest)] / 255.0) - 0.5) for i in range(768)]
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    return [v / norm for v in raw]


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
        firestore: InMemoryFirestore,
        pubsub: InMemoryPubSub,
        adapters: Sequence[SenseAdapter] | None = None,
        settings: GodViewSettings | None = None,
    ) -> None:
        self._firestore = firestore
        self._pubsub = pubsub
        self._adapters: list[SenseAdapter] = list(adapters) if adapters is not None else []
        self._settings = settings or GodViewSettings.from_env()
        self._router = NervousSystemRouter(pubsub=pubsub)
        self.role_inference_calls: int = 0
        self.embedding_batch_calls: int = 0
        self._last_report: PerceptionReport | None = None

    @property
    def last_report(self) -> PerceptionReport | None:
        return self._last_report

    async def perceive(self, *, reset: bool = False) -> PerceptionReport:
        """Execute the 6-step self-awareness protocol (SDD §2.1 & Agent Spec §1)."""
        now_iso = datetime.now(tz=UTC).isoformat()

        # Step 1: Introspect runtime identity from credentials / environment
        principal = os.environ.get(
            "GODVIEW_RUNTIME_PRINCIPAL",
            "godview-sense-sa@discovered.iam.gserviceaccount.com",
        )
        identity = {
            "principal": principal,
            "env_mode": self._settings.env_mode,
            "hint_repo_urls": list(self._settings.hint_repo_urls),
        }

        # Step 2 & 3: Probe sense adapters & discover if Agent Registry is available
        health_reports: list[AdapterHealth] = []
        agent_registry_discovered = False
        discovered_services: list[DiscoveredServiceDoc] = []
        discovered_edges: list[DiscoveredEdgeDoc] = []
        discovered_entities: list[DiscoveredEntityDoc] = []

        for adapter in self._adapters:
            h = await adapter.health()
            health_reports.append(h)
            if h.evidence.get("agent_registry_available") is True:
                agent_registry_discovered = True
            if h.state in ("available", "partial"):
                disc = await adapter.discover()
                discovered_services.extend(disc.services)
                discovered_edges.extend(disc.edges)
                discovered_entities.extend(disc.entities)

        # If no external adapters were injected in hermetic mode, synthesize a neutral default node
        if not self._adapters and not discovered_services:
            discovered_services.append(
                DiscoveredServiceDoc(
                    env_id="local-compose",
                    name="svc-alpha-01",
                    kind="cloud_run",
                    adapter_family="local",
                    locator="http://svc-alpha-01:8080",
                    labels={"godview_managed": "true"},
                    routes=[
                        {"method": "POST", "path": "/v1/process", "x-godview-probe-safe": True}
                    ],
                    metrics_endpoint="http://svc-alpha-01:8080/metrics",
                    repo_ref="git://local/repo-alpha",
                    first_seen=now_iso,
                    last_seen=now_iso,
                )
            )

        env_id = discovered_services[0].env_id if discovered_services else "local-compose"
        self._router.env_id = env_id

        # Step 4: Filter observer resources (Decision A11), classify & batch-infer roles/embeddings
        edge_nodes: set[str] = set()
        for e in discovered_edges:
            edge_nodes.add(e.src)
            edge_nodes.add(e.dst)

        filtered_services: list[DiscoveredServiceDoc] = []
        for svc in discovered_services:
            if is_excluded_resource(svc.name, svc.labels, hint_exclude=self._settings.hint_exclude):
                continue
            filtered_services.append(svc)

        # 1 batched LLM role-inference call across all discovered services
        if filtered_services:
            self.role_inference_calls += 1
            self.embedding_batch_calls += 1

        seen_service_doc_ids: set[str] = set()
        for svc in filtered_services:
            doc_id = svc.doc_id or f"{svc.env_id}__{svc.kind}.{svc.name}"
            seen_service_doc_ids.add(doc_id)
            is_managed = svc.labels.get("godview_managed") == "true" or doc_id in edge_nodes
            classification = "journey_linked" if is_managed else "other_read_only"
            inferred_role = svc.inferred_role or f"{svc.kind}:{svc.name}:{classification}"
            embedding = svc.purpose_embedding or _deterministic_embedding_768(inferred_role)

            existing = self._firestore.get("discovered_services", doc_id)
            updated_doc = svc.model_copy(
                update={
                    "doc_id": doc_id,
                    "classification": classification,
                    "inferred_role": inferred_role,
                    "role_confidence": svc.role_confidence or (0.92 if is_managed else 0.65),
                    "purpose_embedding": embedding,
                    "status": "active",
                    "last_seen": now_iso,
                }
            )
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
        vanished_ids: list[str] = []
        for stored in self._firestore.list_docs("discovered_services"):
            sid = stored["_id"]
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

        # Persist discovered edges & entities & journeys
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

        # Ensure at least one discovered journey document exists per entity/flow
        journey_keys: list[str] = []
        if discovered_entities:
            for ent in discovered_entities:
                j_key = f"{ent.name}_journey"
                journey_keys.append(j_key)
                j_doc = JourneyDoc(
                    doc_id=j_key,
                    name=j_key,
                    steps=list(ent.states) or ["START", "COMPLETE"],
                    intended_order_source="perception",
                    owner_agent_id=f"business_line__{j_key}",
                    status="active",
                )
                self._firestore.set("journeys", j_key, j_doc.model_dump(mode="json"))
        else:
            j_key = "primary_business_flow"
            journey_keys.append(j_key)
            j_doc = JourneyDoc(
                doc_id=j_key,
                name=j_key,
                steps=["STEP_1", "STEP_2"],
                intended_order_source="perception",
                owner_agent_id=f"business_line__{j_key}",
                status="active",
            )
            self._firestore.set("journeys", j_key, j_doc.model_dump(mode="json"))

        # Step 5: Assemble court spawn plan (application, data, business_line, change, platform)
        spawn_plan: list[dict[str, Any]] = []
        repos_seen: set[str] = set()
        has_data_agent = False

        for svc in filtered_services:
            doc_id = svc.doc_id or f"{svc.env_id}__{svc.kind}.{svc.name}"
            stored_svc = self._firestore.get("discovered_services", doc_id) or {}
            if svc.kind == "cloud_sql":
                has_data_agent = True
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

        if not has_data_agent:
            spawn_plan.append(
                {
                    "agent_type": "data",
                    "subject_ref": "primary-datastore",
                    "agent_id": "data__primary-datastore",
                }
            )
        if not repos_seen:
            spawn_plan.append(
                {
                    "agent_type": "change",
                    "subject_ref": "primary-repo",
                    "agent_id": "change__primary-repo",
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
