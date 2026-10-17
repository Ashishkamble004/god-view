"""Unit tests for TopologyBuilder (Plan §1.4)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from godview_shared.contracts import (
    DiscoveredEdgeDoc,
    DiscoveredServiceDoc,
    JourneyDoc,
    Posture,
)

from godview.sovereign.topology.builder import TopologyBuilder


@pytest.mark.unit
def test_topology_builder_schema_and_tiers() -> None:
    """Verify TopologyBuilder produces valid §1.4 JSON format with distinct tiers."""
    builder = TopologyBuilder(env_id="test-env")
    now = datetime.now(tz=UTC).isoformat()

    services = [
        DiscoveredServiceDoc(
            env_id="test-env",
            name="fe-ingress",
            kind="lb_frontend",
            adapter_family="gcp",
            locator="https://203.0.113.1",
            labels={"tier": "edge"},
            first_seen=now,
            last_seen=now,
        ),
        DiscoveredServiceDoc(
            env_id="test-env",
            name="svc-processor",
            kind="cloud_run",
            adapter_family="gcp",
            locator="https://svc-processor.run.app",
            labels={"godview_managed": "true"},
            posture=Posture(ingress="all", iap=True, invoker_iam=True, waf="sensitivity-1"),
            first_seen=now,
            last_seen=now,
        ),
        DiscoveredServiceDoc(
            env_id="test-env",
            name="db-relational",
            kind="cloud_sql",
            adapter_family="sql",
            locator="postgres://db-relational:5432/db",
            labels={},
            first_seen=now,
            last_seen=now,
        ),
        DiscoveredServiceDoc(
            env_id="test-env",
            name="queue-events",
            kind="pubsub_topic",
            adapter_family="gcp",
            locator="projects/p/topics/queue-events",
            labels={},
            first_seen=now,
            last_seen=now,
        ),
        DiscoveredServiceDoc(
            env_id="test-env",
            name="god-view-sovereign",
            kind="cloud_run",
            adapter_family="gcp",
            locator="https://god-view.run.app",
            labels={"godview_role": "observer"},
            first_seen=now,
            last_seen=now,
        ),
    ]

    edges = [
        DiscoveredEdgeDoc(
            src="test-env__lb_frontend.fe-ingress",
            dst="test-env__cloud_run.svc-processor",
            protocol="http",
            evidence="url_map",
            evidence_kind="lb_route",
            confidence=1.0,
            first_seen=now,
            last_seen=now,
        )
    ]

    journeys = [
        JourneyDoc(
            doc_id="j-process",
            name="j-process",
            steps=["test-env__lb_frontend.fe-ingress", "test-env__cloud_run.svc-processor"],
            status="active",
        )
    ]

    incidents = {"test-env__cloud_run.svc-processor": 1}
    golden = {"test-env__cloud_run.svc-processor": {"rps": 50.0, "p95_ms": 12.0, "error_ratio": 0.001}}

    report = builder.build(
        services=services,
        edges=edges,
        journeys=journeys,
        incidents_by_node=incidents,
        golden_by_node=golden,
    )

    assert report.env_id == "test-env"
    assert len(report.nodes) == 5
    assert len(report.edges) == 1
    assert len(report.journeys) == 1

    tier_by_name = {n.name: n.tier for n in report.nodes}
    assert tier_by_name["fe-ingress"] == "edge"
    assert tier_by_name["svc-processor"] == "compute"
    assert tier_by_name["db-relational"] == "data"
    assert tier_by_name["queue-events"] == "messaging"
    assert tier_by_name["god-view-sovereign"] == "observer"

    # Status calculation: svc-processor has 1 open incident -> red
    node_proc = next(n for n in report.nodes if n.name == "svc-processor")
    assert node_proc.status == "red"
    assert node_proc.incidents_open == 1
    assert node_proc.golden.rps == 50.0
    assert node_proc.posture.iap is True

    # Layout hints grouping
    groups = {g.tier: g.node_ids for g in report.layout_hint.groups}
    assert "edge" in groups
    assert "compute" in groups
    assert "data" in groups
