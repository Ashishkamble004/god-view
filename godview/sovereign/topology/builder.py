"""Topology graph builder for God-View v5.

Per plan §1.4, produces the canonical Topology API schema for the cockpit.
Merges multi-adapter perception results into nodes, edges, journeys, and tier layout hints.
Classifies observer vs observed resources using labels and structural evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from godview_shared.config import is_excluded_resource
from godview_shared.contracts import (
    BusinessContext,
    DiscoveredEdgeDoc,
    DiscoveredServiceDoc,
    JourneyDoc,
    Posture,
)
from pydantic import BaseModel, ConfigDict, Field

TopologyTier = Literal[
    "edge",
    "compute",
    "data",
    "messaging",
    "platform",
    "observer",
    "external",
]
TopologyStatus = Literal["green", "amber", "red", "unknown"]
EdgeKind = Literal["http", "grpc", "pubsub", "sql", "lb_route", "iam"]


class TopologyBusinessSummary(BaseModel):
    domain: str = ""
    role_summary: str = ""
    confidence: float = 0.0


class TopologyGoldenSignals(BaseModel):
    rps: float | None = None
    p95_ms: float | None = None
    error_ratio: float | None = None


class TopologyPosture(BaseModel):
    ingress: str | None = None
    iap: bool | None = None
    invoker_iam: bool | None = None
    waf: str | None = None


class TopologyNode(BaseModel):
    id: str
    name: str
    kind: str
    tier: TopologyTier
    status: TopologyStatus
    business: TopologyBusinessSummary
    incidents_open: int = 0
    agent_id: str | None = None
    golden: TopologyGoldenSignals
    posture: TopologyPosture


class TopologyEdge(BaseModel):
    id: str
    src: str
    dst: str
    kind: EdgeKind
    evidence_kind: str
    rps: float | None = None
    error_ratio: float | None = None
    confidence: float = 0.0


class TopologyJourneyStep(BaseModel):
    node: str
    operation: str


class TopologyJourney(BaseModel):
    name: str
    steps: list[TopologyJourneyStep] = Field(default_factory=list)
    health: Literal["green", "amber", "red", "unknown"] = "green"
    confidence: float = 0.0


class LayoutGroup(BaseModel):
    tier: str
    node_ids: list[str] = Field(default_factory=list)


class LayoutHint(BaseModel):
    groups: list[LayoutGroup] = Field(default_factory=list)


class TopologyReport(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    env_id: str
    generated_at: str
    nodes: list[TopologyNode] = Field(default_factory=list)
    edges: list[TopologyEdge] = Field(default_factory=list)
    journeys: list[TopologyJourney] = Field(default_factory=list)
    layout_hint: LayoutHint = Field(default_factory=LayoutHint)


def classify_tier(kind: str, name: str, labels: dict[str, str], classification: str = "") -> TopologyTier:
    """Classify node tier according to kind and observer labels."""
    if labels.get("godview_role") == "observer" or classification == "observer" or is_excluded_resource(name, labels):
        return "observer"
    if kind in ("lb_frontend", "backend_service", "security_policy", "api_gateway"):
        return "edge"
    if kind in ("cloud_run", "gke_workload", "gce_instance", "gce_vm"):
        return "compute"
    if kind in ("cloud_sql", "gcs_bucket", "bucket", "bigquery_dataset", "firestore_db"):
        return "data"
    if kind in ("pubsub_topic", "pubsub_subscription"):
        return "messaging"
    if kind in ("secret", "agent", "reasoning_engine"):
        return "platform"
    if kind == "external":
        return "external"
    return "compute"


def map_edge_kind(protocol: str) -> EdgeKind:
    norm = protocol.lower().strip()
    if norm in ("http", "https"):
        return "http"
    if norm == "grpc":
        return "grpc"
    if norm in ("pubsub", "pubsub_push"):
        return "pubsub"
    if norm in ("sql", "postgres", "mysql"):
        return "sql"
    if norm == "lb_route":
        return "lb_route"
    if norm == "iam":
        return "iam"
    return "http"


def extract_incidents_by_node(
    incidents_raw: list[dict[str, Any]],
    services: list[DiscoveredServiceDoc] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Map open/active incident docs to topology node IDs and short service names."""
    from urllib.parse import urlsplit

    incidents_by_node: dict[str, list[dict[str, Any]]] = {}
    svc_list = services or []
    for inc in incidents_raw:
        state = str(inc.get("outcome") or inc.get("status") or "").lower()
        if state not in ("open", "escalated", "investigating"):
            continue
        evp = inc.get("evidence_pack") if isinstance(inc.get("evidence_pack"), dict) else {}
        affected = evp.get("affected") if isinstance(evp.get("affected"), dict) else {}
        aff_services = affected.get("services") if isinstance(affected.get("services"), list) else []
        inc_services = inc.get("services") if isinstance(inc.get("services"), list) else []
        dedupe = str(inc.get("dedupe_key") or "")
        dedupe_res = dedupe.split(":", 1)[1] if ":" in dedupe else ""

        raw_keys = [
            inc.get("target_resource"),
            inc.get("resource_id"),
            inc.get("node_id"),
            inc.get("entity_id"),
            dedupe_res,
            *inc_services,
            *aff_services,
        ]
        expanded: set[str] = set()
        for rk in raw_keys:
            if not rk or not isinstance(rk, str):
                continue
            k = rk.strip()
            if not k:
                continue
            expanded.add(k)
            if "://" in k:
                parsed = urlsplit(k)
                host = parsed.hostname or ""
                if host:
                    expanded.add(host)
                    expanded.add(f"{parsed.scheme}://{host}")
                    for s in svc_list:
                        loc = str(s.locator or "")
                        s_name = str(s.name or "")
                        if host in loc or host in s_name or any(host in str(r) for r in (s.routes or [])):
                            expanded.add(s.name)
                            if s.doc_id:
                                expanded.add(s.doc_id)
            else:
                if ":" in k:
                    expanded.add(k.split(":")[0])
                if "/" in k:
                    expanded.add(k.rstrip("/").split("/")[-1])
        for key in expanded:
            incidents_by_node.setdefault(key, []).append(inc)
    return incidents_by_node


class TopologyBuilder:
    """Builds and merges the canonical God-View topology graph."""

    def __init__(self, env_id: str = "default") -> None:
        self.env_id = env_id

    def build(
        self,
        services: list[DiscoveredServiceDoc],
        edges: list[DiscoveredEdgeDoc],
        journeys: list[JourneyDoc] | None = None,
        incidents_by_node: dict[str, Any] | None = None,
        golden_by_node: dict[str, dict[str, Any]] | None = None,
        posture_by_node: dict[str, Posture] | None = None,
        *,
        incidents: dict[str, Any] | None = None,
        golden_signals: dict[str, Any] | None = None,
    ) -> TopologyReport:
        now_iso = datetime.now(tz=UTC).isoformat()
        raw_incidents = incidents_by_node if incidents_by_node is not None else (incidents or {})
        incidents_map = {
            k: (len(v) if isinstance(v, (list, tuple, set)) else int(v or 0))
            for k, v in raw_incidents.items()
        }
        golden_map = golden_by_node or golden_signals or {}
        posture_map = posture_by_node or {}

        # 1. Deduplicate services by node_id / name+kind
        node_map: dict[str, DiscoveredServiceDoc] = {}
        for s in services:
            doc_id = s.doc_id or f"{self.env_id}__{s.kind}.{s.name}"
            if doc_id in node_map:
                # Merge existing: prefer doc with business_context, routes, or posture
                existing = node_map[doc_id]
                merged_labels = dict(existing.labels)
                merged_labels.update(s.labels)
                routes = s.routes or existing.routes
                bc = s.business_context or existing.business_context
                pos = s.posture or existing.posture
                locator = s.locator or existing.locator
                node_map[doc_id] = existing.model_copy(
                    update={
                        "labels": merged_labels,
                        "routes": routes,
                        "business_context": bc,
                        "posture": pos,
                        "locator": locator,
                    }
                )
            else:
                node_map[doc_id] = s

        # 2. Build TopologyNode list
        nodes: list[TopologyNode] = []
        status_by_node: dict[str, TopologyStatus] = {}
        tier_groups: dict[str, list[str]] = {
            "edge": [],
            "compute": [],
            "data": [],
            "messaging": [],
            "platform": [],
            "observer": [],
            "external": [],
        }

        for node_id, s in node_map.items():
            name = s.name
            kind = s.kind
            labels = s.labels
            tier = classify_tier(kind, name, labels, s.classification or "")

            # Business context summary
            bc = s.business_context
            tier_default_domain = {
                "edge": "Edge & Ingress",
                "compute": "Application Compute",
                "data": "Data & Persistence",
                "messaging": "Event & Messaging Mesh",
                "platform": "AI & Platform Runtime",
                "observer": "Observer Control Plane",
                "external": "External Integration",
            }.get(tier, "Platform Services")
            kind_label = kind.replace("_", " ").title()
            if bc is not None and isinstance(bc, BusinessContext):
                business = TopologyBusinessSummary(
                    domain=bc.domain or tier_default_domain,
                    role_summary=bc.role_summary or s.inferred_role or f"{kind_label} ({name})",
                    confidence=bc.confidence or 0.85,
                )
            else:
                inferred_dom = (
                    s.labels.get("domain")
                    or s.labels.get("app")
                    or s.labels.get("tier")
                    or tier_default_domain
                )
                business = TopologyBusinessSummary(
                    domain=str(inferred_dom),
                    role_summary=s.inferred_role or f"{kind_label} resource in {tier} tier",
                    confidence=s.role_confidence or 0.8,
                )

            # Incidents open count
            inc_open = incidents_map.get(node_id, 0)
            if not inc_open:
                # Also check by short name or locator
                inc_open = incidents_map.get(name, 0)
            if not inc_open and s.locator:
                inc_open = incidents_map.get(s.locator, 0)
                if not inc_open and "://" in s.locator:
                    from urllib.parse import urlsplit
                    loc_host = urlsplit(s.locator).hostname or ""
                    if loc_host:
                        inc_open = incidents_map.get(loc_host, 0)

            # Golden signals
            node_golden = golden_map.get(node_id) or golden_map.get(name) or {}
            rps = node_golden.get("rps")
            p95 = node_golden.get("p95_ms")
            err = node_golden.get("error_ratio")
            golden = TopologyGoldenSignals(rps=rps, p95_ms=p95, error_ratio=err)

            # Status evaluation
            if inc_open > 0:
                status: TopologyStatus = "red"
            elif getattr(s, "status", "active") == "gone":
                status = "red"
            elif err is not None and err > 0.02:
                status = "red"
            elif p95 is not None and p95 > 3500.0:
                status = "red"
            elif err is not None and err > 0.005:
                status = "amber"
            elif (rps is not None and rps > 0) or (p95 is not None and p95 > 0):
                status = "green"
            elif getattr(s, "status", "active") == "active":
                status = "green"
            else:
                status = "unknown"

            status_by_node[node_id] = status
            status_by_node[name] = status

            # Posture
            pos = s.posture or posture_map.get(node_id) or posture_map.get(name)
            if pos is not None and isinstance(pos, Posture):
                posture = TopologyPosture(
                    ingress=pos.ingress,
                    iap=pos.iap,
                    invoker_iam=pos.invoker_iam,
                    waf=pos.waf,
                )
            else:
                posture = TopologyPosture(
                    ingress=None,
                    iap=None,
                    invoker_iam=None,
                    waf=None,
                )

            # Agent ID assignment
            if s.owner_agent_id:
                agent_id = s.owner_agent_id
            elif tier == "compute":
                agent_id = f"application__{name}"
            elif tier == "data":
                agent_id = f"data__{name}"
            elif tier == "messaging":
                agent_id = f"messaging__{name}"
            elif tier == "platform":
                agent_id = f"platform__{name}"
            else:
                agent_id = None

            nodes.append(
                TopologyNode(
                    id=node_id,
                    name=name,
                    kind=kind,
                    tier=tier,
                    status=status,
                    business=business,
                    incidents_open=inc_open,
                    agent_id=agent_id,
                    golden=golden,
                    posture=posture,
                )
            )
            tier_groups[tier].append(node_id)

        # 3. Deduplicate and format edges
        topology_edges: list[TopologyEdge] = []
        seen_edge_ids: set[str] = set()

        for e in edges:
            edge_id = e.doc_id or f"{e.src}__to__{e.dst}"
            if edge_id in seen_edge_ids:
                continue
            seen_edge_ids.add(edge_id)

            e_kind = map_edge_kind(e.protocol)
            evidence_kind = e.evidence_kind or (
                str(e.evidence) if hasattr(e, "evidence") else "config"
            )

            topology_edges.append(
                TopologyEdge(
                    id=edge_id,
                    src=e.src,
                    dst=e.dst,
                    kind=e_kind,
                    evidence_kind=evidence_kind,
                    rps=e.rps,
                    error_ratio=e.error_ratio,
                    confidence=float(e.confidence or 0.0),
                )
            )

        # 4. Inferred journeys
        topology_journeys: list[TopologyJourney] = []
        if journeys:
            for j in journeys:
                steps: list[TopologyJourneyStep] = []
                for s in j.steps:
                    node = ""
                    op = ""
                    if isinstance(s, dict):
                        node = str(s.get("node") or s.get("service") or "")
                        op = str(s.get("operation") or "")
                    elif isinstance(s, str):
                        node = s
                        op = ""

                    # Disambiguate if node contains operation (e.g. node_id.op or node_id:op)
                    if node and node not in status_by_node:
                        for n_id in status_by_node:
                            if node.startswith(f"{n_id}.") or node.startswith(f"{n_id}:"):
                                if not op or op == "unknown":
                                    op = node[len(n_id) + 1:]
                                node = n_id
                                break

                    if not op or op == "unknown":
                        svc_match = node_map.get(node) or next(
                            (sv for sv in node_map.values() if sv.name == node), None
                        )
                        if svc_match is not None:
                            if svc_match.routes:
                                r0 = svc_match.routes[0]
                                op = str(
                                    r0.get("operation_id")
                                    or r0.get("summary")
                                    or f"{r0.get('method', 'POST')} {r0.get('path', '/v1/op')}"
                                )
                            else:
                                op = f"{svc_match.kind}:{svc_match.name}"
                        elif "." in node:
                            op = f"invoke:{node.split('.', 1)[-1]}"
                        else:
                            op = f"invoke:{node}"

                    steps.append(TopologyJourneyStep(node=node, operation=op))

                # Calculate health from member nodes
                member_statuses = [
                    status_by_node.get(step.node, "unknown")
                    for step in steps
                    if step.node in status_by_node
                ]
                if any(st == "red" for st in member_statuses):
                    j_health = "red"
                elif any(st == "amber" for st in member_statuses):
                    j_health = "amber"
                elif member_statuses and all(st == "green" for st in member_statuses):
                    j_health = "green"
                else:
                    j_health = "unknown"

                conf = float(getattr(j, "confidence", 0.0) or 0.0)
                topology_journeys.append(
                    TopologyJourney(
                        name=j.name,
                        steps=steps,
                        health=j_health,
                        confidence=conf,
                    )
                )

        # 5. Layout hints
        layout_groups = [
            LayoutGroup(tier=t, node_ids=ids)
            for t, ids in tier_groups.items()
            if ids
        ]

        return TopologyReport(
            env_id=self.env_id,
            generated_at=now_iso,
            nodes=nodes,
            edges=topology_edges,
            journeys=topology_journeys,
            layout_hint=LayoutHint(groups=layout_groups),
        )
