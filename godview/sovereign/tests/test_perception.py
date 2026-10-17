"""M1 Unit Tests for Environment Perception (`godview/sovereign/perception/`).

Covers TDD I-02 (identity-only boot with no env vars except GODVIEW_ENV=hermetic),
SDD §2.1 (6-step self-awareness protocol), Agent Spec §1 (available/partial/unavailable
senses, optional Agent Registry discovery, batched role inference and embedding),
and Coordinator Decision A11 (observer resource exclusion).
All fixtures use randomised, environment-neutral names.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from godview_shared.adapter_contract import (
    AdapterHealth,
    DiscoveryResult,
    SenseAdapter,
    SenseCategory,
)
from godview_shared.capabilities import CapabilitySpec, get_capability
from godview_shared.contracts import (
    DiscoveredEdgeDoc,
    DiscoveredEntityDoc,
    DiscoveredServiceDoc,
)
from godview_shared.fakes import InMemoryFirestore, InMemoryPubSub

from godview.sovereign.perception import PerceptionOrgan


class _HermeticAdapter(SenseAdapter):
    def __init__(
        self,
        name: str,
        family: str,
        state: str,
        services: list[DiscoveredServiceDoc] | None = None,
        edges: list[DiscoveredEdgeDoc] | None = None,
        entities: list[DiscoveredEntityDoc] | None = None,
    ) -> None:
        self.name = name
        self.family = family  # type: ignore[assignment]
        self._state = state
        self._services = services or []
        self._edges = edges or []
        self._entities = entities or []

    async def discover(self) -> DiscoveryResult:
        return DiscoveryResult(
            services=list(self._services),
            edges=list(self._edges),
            entities=list(self._entities),
            evidence={"source": self.name, "count": len(self._services)},
        )

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.CODE, SenseCategory.CONTRACTS, SenseCategory.SCOPED_OBSERVABILITY]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {"metrics.scrape": get_capability("metrics.scrape")}

    async def health(self) -> AdapterHealth:
        return AdapterHealth(
            adapter_name=self.name,
            family=self.family,
            state=self._state,  # type: ignore[arg-type]
            evidence={"probe": self._state},
            checked_at=datetime.now(tz=UTC),
        )


@pytest.mark.unit
async def test_identity_only_boot_and_six_step_self_awareness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Boot with only GODVIEW_ENV=hermetic; run 6-step protocol and persist graph to Firestore."""
    monkeypatch.setenv("GODVIEW_ENV", "hermetic")
    monkeypatch.delenv("GODVIEW_HINT_REPO_URLS", raising=False)

    now = datetime.now(tz=UTC).isoformat()
    svc_gateway = DiscoveredServiceDoc(
        env_id="local-compose",
        name="svc-ingress-alpha-11",
        kind="cloud_run",
        adapter_family="local",
        locator="http://svc-ingress-alpha-11:8080",
        labels={"godview_managed": "true", "tier": "edge"},
        routes=[{"method": "POST", "path": "/v1/orders/apply", "x-godview-probe-safe": True}],
        metrics_endpoint="http://svc-ingress-alpha-11:8080/metrics",
        repo_ref="git://local/repo-alpha",
        first_seen=now,
        last_seen=now,
    )
    svc_eval = DiscoveredServiceDoc(
        env_id="local-compose",
        name="svc-evaluator-beta-22",
        kind="gke_workload",
        adapter_family="kubernetes",
        locator="k8s://ns-main/svc-evaluator-beta-22",
        labels={"godview_managed": "true"},
        metrics_endpoint="http://10.0.0.12:8080/metrics",
        first_seen=now,
        last_seen=now,
    )
    svc_db = DiscoveredServiceDoc(
        env_id="local-compose",
        name="db-orders-gamma-33",
        kind="cloud_sql",
        adapter_family="sql",
        locator="postgres://db-orders-gamma-33:5432/appdb",
        labels={"godview_managed": "true"},
        first_seen=now,
        last_seen=now,
    )
    # Observer resource that must be excluded per Decision A11
    svc_observer = DiscoveredServiceDoc(
        env_id="local-compose",
        name="god-view-sovereign",
        kind="cloud_run",
        adapter_family="local",
        locator="http://god-view-sovereign:8080",
        labels={"godview_role": "observer"},
        first_seen=now,
        last_seen=now,
    )
    # Unmanaged third-party service -> classified other_read_only
    svc_unmanaged = DiscoveredServiceDoc(
        env_id="local-compose",
        name="svc-legacy-readonly-99",
        kind="cloud_run",
        adapter_family="local",
        locator="http://svc-legacy-readonly-99:8080",
        labels={"owner": "external"},
        first_seen=now,
        last_seen=now,
    )

    edge = DiscoveredEdgeDoc(
        src="local-compose__cloud_run.svc-ingress-alpha-11",
        dst="local-compose__gke_workload.svc-evaluator-beta-22",
        protocol="http",
        evidence="trace",
        call_rate_baseline=12.5,
        first_seen=now,
        last_seen=now,
    )
    entity = DiscoveredEntityDoc(
        name="order_application",
        correlation_keys=["journey_id", "customer_id"],
        tables=["orders"],
        owning_service_id="local-compose__cloud_run.svc-ingress-alpha-11",
        states=["PENDING", "VERIFIED", "APPROVED", "SETTLED"],
    )

    adapters: list[SenseAdapter] = [
        _HermeticAdapter(
            "local-compose-adapter",
            "local",
            "available",
            services=[svc_gateway, svc_observer, svc_unmanaged],
            edges=[edge],
            entities=[entity],
        ),
        _HermeticAdapter("k8s-adapter", "kubernetes", "partial", services=[svc_eval]),
        _HermeticAdapter("sql-adapter", "sql", "available", services=[svc_db]),
        _HermeticAdapter("datastream-cdc", "gcp", "unavailable", services=[]),
    ]

    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    organ = PerceptionOrgan(firestore=fs, pubsub=ps, adapters=adapters)

    report = await organ.perceive(reset=True)

    # Step 1 & 2: Identity and environment fingerprint
    assert report.identity["principal"]
    assert report.env_id == "local-compose"

    # Step 3: Senses reported as available / partial / unavailable with evidence
    states_by_name = {h.adapter_name: h.state for h in report.adapter_health}
    assert states_by_name["local-compose-adapter"] == "available"
    assert states_by_name["k8s-adapter"] == "partial"
    assert states_by_name["datastream-cdc"] == "unavailable"
    assert report.agent_registry_discovered is False

    # Step 4: Observer resource excluded (A11); managed vs other_read_only classified
    stored_services = fs.list_docs("discovered_services")
    names = {s["name"] for s in stored_services}
    assert "god-view-sovereign" not in names
    assert {
        "svc-ingress-alpha-11",
        "svc-evaluator-beta-22",
        "db-orders-gamma-33",
        "svc-legacy-readonly-99",
    } == names

    by_name = {s["name"]: s for s in stored_services}
    assert by_name["svc-ingress-alpha-11"]["classification"] == "journey_linked"
    assert by_name["svc-legacy-readonly-99"]["classification"] == "other_read_only"
    assert len(by_name["svc-ingress-alpha-11"]["purpose_embedding"]) == 768

    # Batched LLM call count = 1 role inference call + 1 embedding batch call
    assert organ.role_inference_calls == 1
    assert organ.embedding_batch_calls == 1

    # Step 5: Court spawn plan includes app, data, change, business_line, platform
    agent_types = {item["agent_type"] for item in report.spawn_plan}
    assert {"application", "data", "change", "business_line", "platform"} <= agent_types


@pytest.mark.unit
async def test_continuous_reperception_marks_disappeared_services_gone() -> None:
    """Step 6: When a service disappears on re-perception, it is marked gone and emits discovery event."""
    now = datetime.now(tz=UTC).isoformat()
    svc_a = DiscoveredServiceDoc(
        env_id="local-compose",
        name="svc-ephemeral-01",
        kind="cloud_run",
        adapter_family="local",
        locator="http://svc-ephemeral-01:8080",
        labels={"godview_managed": "true"},
        first_seen=now,
        last_seen=now,
    )
    adapter = _HermeticAdapter("local-adapter", "local", "available", services=[svc_a])
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    organ = PerceptionOrgan(firestore=fs, pubsub=ps, adapters=[adapter])

    await organ.perceive(reset=False)
    doc = fs.get("discovered_services", "local-compose__cloud_run.svc-ephemeral-01")
    assert doc is not None and doc["status"] == "active"

    # Now service disappears from adapter discovery
    adapter._services = []
    report2 = await organ.perceive(reset=False)
    doc_after = fs.get("discovered_services", "local-compose__cloud_run.svc-ephemeral-01")
    assert doc_after is not None and doc_after["status"] == "gone"
    assert "local-compose__cloud_run.svc-ephemeral-01" in report2.vanished_service_ids
