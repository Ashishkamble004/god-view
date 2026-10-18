"""Unit tests for IncidentManager deduplication, lifecycle, evidence packs, and hooks."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from godview_shared.contracts import SentinelDefinition
from godview_shared.fakes import InMemoryAgentEngine, InMemoryFirestore, InMemoryPubSub

from godview.sovereign.incidents.manager import IncidentManager
from godview.sovereign.sentinels.models import SentinelEvaluation


def _make_dummy_sentinel(
    sentinel_id: str = "snt-lb-edge",
    sentinel_class: str = "lb_requests",
    source_ref: str = "edge-policy-01",
    consecutive: int = 1,
) -> SentinelDefinition:
    return SentinelDefinition.model_validate(
        {
            "sentinel_id": sentinel_id,
            "class": sentinel_class,
            "source_ref": source_ref,
            "check": {"dialect": "python", "statement": "test check", "window_s": 300},
            "cadence_s": 10,
            "baseline": {"model": "adaptive", "expected": 0.0, "stddev": 0.1},
            "wake_condition": {
                "operator": ">=",
                "threshold": 1.0,
                "consecutive": consecutive,
                "window_s": 300,
            },
            "target_agent_id": "platform__env",
            "state": "enforced",
            "compiled_by": "test",
            "compiled_at": datetime.now(tz=UTC).isoformat(),
        }
    )


@pytest.mark.unit
async def test_incident_dedupe_and_lifecycle_auto_close() -> None:
    """Verifies dedupe key, update on repeated breach, and auto-close when clear for N ticks."""
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    ae = InMemoryAgentEngine()
    now = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)

    court_notified: list[Any] = []
    closed_notified: list[Any] = []

    class MockHooks:
        def __init__(self) -> None:
            self.on_incident = [lambda inc: court_notified.append(inc)]
            self.on_incident_closed = [lambda inc: closed_notified.append(inc)]

    mgr = IncidentManager(
        firestore=fs,
        bus=ps,
        agents=ae,
        hooks=MockHooks(),
        clock=lambda: now,
        auto_close_ticks=3,
    )

    snt = _make_dummy_sentinel()
    eval_breach = SentinelEvaluation(
        observed=1.0,
        breached=True,
        signature="denied_by_security_policy_sqli",
        resource_id="edge-policy-01",
        severity_override="sev1",
        details={"blocked_count": 5},
    )

    # Tick 1: Open incident
    inc1, opened1 = await mgr.record_breach(snt, eval_breach, now)
    assert opened1 is True
    assert inc1.outcome == "open"
    assert inc1.severity == "sev1"
    assert inc1.dedupe_key == "lb_requests:edge-policy-01:denied_by_security_policy_sqli"
    assert len(court_notified) == 1

    # Tick 2: Repeated breach on same signature -> Deduplicated & Updated
    inc2, opened2 = await mgr.record_breach(snt, eval_breach, now)
    assert opened2 is False
    assert inc2.doc_id == inc1.doc_id
    assert inc2.consecutive_breaches == 2

    # Clear ticks 1 & 2 -> Still open (needs 3 ticks)
    closed = await mgr.record_clear(snt, now)
    assert len(closed) == 0
    assert inc2.clear_ticks == 1

    closed = await mgr.record_clear(snt, now)
    assert len(closed) == 0
    assert inc2.clear_ticks == 2

    # Clear tick 3 -> Auto-closes!
    closed = await mgr.record_clear(snt, now)
    assert len(closed) == 1
    assert closed[0].doc_id == inc1.doc_id
    assert closed[0].outcome == "resolved"
    assert closed[0].closed_at is not None
    assert len(closed_notified) == 1


@pytest.mark.unit
async def test_manual_close_and_change_event_correlation() -> None:
    """Verifies manual close via operator and nervous-system change_event correlation."""
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    mgr = IncidentManager(firestore=fs, bus=ps)
    now = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)

    snt = _make_dummy_sentinel()
    eval_breach = SentinelEvaluation(
        observed=2.0,
        breached=True,
        signature="invoker_iam_disabled",
        resource_id="service-auth",
        severity_override="sev1",
    )

    inc, _ = await mgr.record_breach(snt, eval_breach, now)

    # Change event arrives from nervous system push
    mgr.on_change_event(
        {
            "doc_id": "chg-audit-01",
            "kind": "audit",
            "affected_services": ["service-auth"],
            "business_summary": "invoker IAM disabled by antigravity session",
            "occurred_at": "2026-10-17T12:06:00Z",
        }
    )

    assert "correlated_changes" in inc.raw_evidence
    assert len(inc.raw_evidence["correlated_changes"]) == 1
    assert inc.raw_evidence["correlated_changes"][0]["change_event_id"] == "chg-audit-01"

    # Operator manually closes incident
    closed = await mgr.close_incident(
        inc.doc_id,
        reason="Remediated via Cloud Run IAM restoration",
        outcome="resolved",
        operator="operator@example.com",
    )
    assert closed.outcome == "resolved"
    assert "Remediated" in str(closed.root_cause)
    assert closed.raw_evidence.get("closed_by") == "operator@example.com"


@pytest.mark.unit
async def test_record_clear_isolation_across_resources() -> None:
    """Verifies that record_clear on resource A never auto-closes incidents on resource B."""
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    mgr = IncidentManager(firestore=fs, bus=ps, auto_close_ticks=2)
    now = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)

    # Sentinel A on resource-alpha
    snt_a = _make_dummy_sentinel(sentinel_id="snt-posture-alpha", sentinel_class="posture", source_ref="resource-alpha")
    eval_a = SentinelEvaluation(
        observed=1.0,
        breached=True,
        signature="posture_drift_ingress",
        resource_id="resource-alpha",
        severity_override="sev2",
    )
    inc_a, opened_a = await mgr.record_breach(snt_a, eval_a, now)
    assert opened_a is True
    assert inc_a.outcome == "open"

    # Sentinel B on resource-beta of the SAME sentinel class ("posture")
    snt_b = _make_dummy_sentinel(sentinel_id="snt-posture-beta", sentinel_class="posture", source_ref="resource-beta")

    # Clear sentinel B for 5 ticks
    for _ in range(5):
        closed = await mgr.record_clear(snt_b, now, resource_id="resource-beta")
        assert len(closed) == 0

    # Incident A on resource-alpha must still be open with 0 clear ticks!
    assert inc_a.outcome == "open"
    assert inc_a.clear_ticks == 0

    # Now clear sentinel A for 2 ticks -> incident A closes
    await mgr.record_clear(snt_a, now, resource_id="resource-alpha")
    assert inc_a.clear_ticks == 1
    closed_a = await mgr.record_clear(snt_a, now, resource_id="resource-alpha")
    assert len(closed_a) == 1
    assert closed_a[0].doc_id == inc_a.doc_id
    assert closed_a[0].outcome == "resolved"


@pytest.mark.unit
async def test_incident_manager_restart_persistence() -> None:
    """Verifies that open incidents survive process restart and deduplicate properly."""
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    now = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)

    # 1. Process 1 opens an incident
    mgr1 = IncidentManager(firestore=fs, bus=ps, auto_close_ticks=3)
    snt = _make_dummy_sentinel(source_ref="svc-gateway")
    eval_b = SentinelEvaluation(
        observed=1.0,
        breached=True,
        signature="http_5xx_spike",
        resource_id="svc-gateway",
    )
    inc1, opened1 = await mgr1.record_breach(snt, eval_b, now)
    assert opened1 is True

    # 2. Process 2 restarts (new IncidentManager instance connected to same store)
    mgr2 = IncidentManager(firestore=fs, bus=ps, auto_close_ticks=3)
    # Open incident must be loaded into memory
    assert inc1.dedupe_key in mgr2._active_incidents
    loaded_inc = mgr2.get_incident(inc1.doc_id)
    assert loaded_inc is not None
    assert loaded_inc.outcome == "open"

    # 3. Another breach on same signature updates existing incident across restart
    inc2, opened2 = await mgr2.record_breach(snt, eval_b, now)
    assert opened2 is False
    assert inc2.doc_id == inc1.doc_id

    # 4. Auto-close across 3 ticks resolves it in store
    for _ in range(2):
        assert len(await mgr2.record_clear(snt, now, resource_id="svc-gateway")) == 0
    closed_list = await mgr2.record_clear(snt, now, resource_id="svc-gateway")
    assert len(closed_list) == 1
    assert closed_list[0].outcome == "resolved"

    # In store it must be resolved
    store_doc = fs.get("incidents", inc1.doc_id)
    assert store_doc["outcome"] == "resolved"


@pytest.mark.unit
async def test_honest_evidence_pack_and_zero_fake_defaults() -> None:
    """Verifies EvidencePack honesty: strict change correlation window, no fake 50k tokens, no SHA-256 fake embeddings."""
    fs = InMemoryFirestore()
    now = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)

    # Seed change events:
    # 1. Matching resource within 10m -> MUST correlate
    fs.set(
        "change_events",
        "chg-valid",
        {
            "doc_id": "chg-valid",
            "target_resource": "svc-orders",
            "kind": "deploy",
            "occurred_at": "2026-10-17T11:55:00Z",  # 5 min ago
            "business_summary": "Orders deploy v2.1",
        },
    )
    # 2. Matching resource but 2 hours ago -> MUST NOT correlate (>30m)
    fs.set(
        "change_events",
        "chg-stale",
        {
            "doc_id": "chg-stale",
            "target_resource": "svc-orders",
            "kind": "config",
            "occurred_at": "2026-10-17T10:00:00Z",  # 2 hours ago
            "business_summary": "Stale config change",
        },
    )
    # 3. Unrelated resource within 5m -> MUST NOT correlate
    fs.set(
        "change_events",
        "chg-unrelated",
        {
            "doc_id": "chg-unrelated",
            "target_resource": "svc-billing",
            "kind": "deploy",
            "occurred_at": "2026-10-17T11:58:00Z",
            "business_summary": "Billing deploy",
        },
    )

    mgr = IncidentManager(firestore=fs)
    snt = _make_dummy_sentinel(source_ref="svc-orders")
    eval_b = SentinelEvaluation(
        observed=1.0,
        breached=True,
        signature="latency_high",
        resource_id="svc-orders",
    )

    inc, _ = await mgr.record_breach(snt, eval_b, now)
    evp = inc.evidence_pack
    if hasattr(evp, "model_dump"):
        evp_dict = evp.model_dump(mode="json")
    else:
        evp_dict = evp

    # Correlated changes must ONLY contain chg-valid
    chgs = evp_dict.get("correlated_changes", [])
    assert len(chgs) == 1
    assert chgs[0]["change_event_id"] == "chg-valid"

    # Budget tokens must be 0 (honest, not fake 50k default)
    assert evp_dict["budget"]["agent_tokens_remaining"] == 0
    assert evp_dict["budget"]["escalation_allowed"] is False

    # Vector recall must be empty when no embedding model provided (honest, no SHA-256 fake)
    assert evp_dict["recall"]["episodic"] == []
    assert evp_dict["recall"]["skills"] == []
    assert inc.symptom_embedding is None


@pytest.mark.unit
async def test_llm_client_embed_signature_unwrapping() -> None:
    """Verifies that LlmClient.embed(texts: Sequence[str]) -> list[list[float]] unwraps to 1D list[float] on IncidentDoc."""
    fs = InMemoryFirestore()
    now = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)

    dummy_vector = [0.1] * 768

    class FakeLlmClient:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        async def embed(self, texts: list[str]) -> list[list[float]]:
            self.calls.append(texts)
            # True LlmClient.embed signature: accepts list of strings, returns list of vectors
            assert isinstance(texts, list)
            assert all(isinstance(t, str) for t in texts)
            return [dummy_vector for _ in texts]

    fake_llm = FakeLlmClient()

    class MockContext:
        def __init__(self) -> None:
            self.llm = fake_llm

    mgr = IncidentManager(firestore=fs, ctx=MockContext())
    snt = _make_dummy_sentinel(source_ref="svc-test")
    eval_b = SentinelEvaluation(
        observed=1.0,
        breached=True,
        signature="waf_anomaly",
        resource_id="svc-test",
    )

    inc, opened = await mgr.record_breach(snt, eval_b, now)
    assert opened is True
    assert inc.symptom_embedding == dummy_vector
    assert isinstance(inc.symptom_embedding, list)
    assert isinstance(inc.symptom_embedding[0], float)
    # Ensure embed was called with list[str], not raw str
    assert len(fake_llm.calls) >= 1
    assert isinstance(fake_llm.calls[0], list)
