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

    class MockHooks:
        def on_incident(self, inc: Any) -> None:
            court_notified.append(inc)

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
        operator="operator@example.com",
    )
    assert closed.outcome == "resolved"
    assert "Remediated" in str(closed.root_cause)
