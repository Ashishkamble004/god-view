"""M1 Unit Tests for the 0-Token Sentinel Engine (`godview/sovereign/sentinel_engine/`).

Covers TDD §2.4 (SentinelDefinition, EvidencePack), §4 (10 s cadence, 8 check classes,
same-hour baselines excluding probe rows, shadow vs enforced invariants, planned
operator action suppression, KNN recall, and A2A wake dispatch).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from godview_shared.contracts import SentinelDefinition
from godview_shared.fakes import (
    InMemoryAgentEngine,
    InMemoryBigQuery,
    InMemoryFirestore,
    InMemoryPubSub,
)

from godview.sovereign.sentinel_engine import SentinelEngine


def _make_sentinel(
    sentinel_id: str,
    sentinel_class: str,
    state: str = "enforced",
    consecutive: int = 2,
    threshold: float = 1.0,
) -> SentinelDefinition:
    return SentinelDefinition.model_validate(
        {
            "sentinel_id": sentinel_id,
            "class": sentinel_class,
            "source_ref": f"ref-{sentinel_id}",
            "check": {
                "dialect": "bigquery",
                "statement": "SELECT COUNT(*) FROM events WHERE decision='APPROVED' AND amount_inr > 1000",
                "window_s": 300,
            },
            "cadence_s": 10,
            "baseline": {"model": "same_hour", "window_days": 7, "expected": 0.0, "stddev": 0.2},
            "wake_condition": {
                "operator": ">=",
                "threshold": threshold,
                "consecutive": consecutive,
                "window_s": 300,
            },
            "target_agent_id": "application__svc-alpha-01",
            "state": state,
            "compiled_by": "application__svc-alpha-01",
            "compiled_at": datetime.now(tz=UTC).isoformat(),
        }
    )


@pytest.mark.unit
async def test_eight_check_classes_and_evidence_pack_wake() -> None:
    """All 8 sentinel check classes evaluate at 0 tokens; enforced breach emits EvidencePack and wakes lead."""
    fs = InMemoryFirestore()
    bq = InMemoryBigQuery()
    ps = InMemoryPubSub()
    ae = InMemoryAgentEngine()
    ae.deploy_agent("application__svc-alpha-01", "application", min_instances=0)

    # Seed precedent incident & procedural skill for 768-dim KNN recall
    vec = [1.0] + [0.0] * 767
    fs.set(
        "episodic_incidents",
        "inc-20261001-0001",
        {"symptom_summary": "Past threshold breach", "symptom_embedding": vec},
    )
    fs.set(
        "procedural_skills",
        "rollback-threshold-skill",
        {"name": "rollback-threshold-skill", "trigger_embedding": vec},
    )
    fs.set(
        "change_events",
        "chg-repo-sha99",
        {
            "repo": "org/repo-alpha",
            "kind": "deploy",
            "sha": "sha99",
            "occurred_at": datetime.now(tz=UTC).isoformat(),
            "business_summary": "Ratio limit changed 60 -> 40 in rules.py (PR #42)",
        },
    )

    engine = SentinelEngine(
        firestore=fs,
        bigquery=bq,
        pubsub=ps,
        agent_engine=ae,
        embed_fn=lambda _: vec,
    )
    assert engine.cadence_s == 10

    # Register all 8 check classes
    classes = (
        "invariant_sql",
        "kpi_changepoint",
        "journey_conformance",
        "probe_plan",
        "golden_signal_gate",
        "drift_diff",
        "budget",
        "discovery",
    )
    for cls_name in classes:
        snt = _make_sentinel(f"snt-{cls_name}-01", cls_name, consecutive=2, threshold=1.0)
        engine.register_sentinel(snt)

    # Probe rows must NEVER trigger or pollute baselines
    bq.insert_rows(
        "events",
        [{"event_id": "probe-1", "decision": "APPROVED", "amount_inr": 2500, "source": "probe"}],
    )
    res_clean = await engine.evaluate_tick()
    assert len(res_clean.wakes_sent) == 0

    # Insert real business breach row
    bq.insert_rows(
        "events",
        [{"event_id": "real-1", "decision": "APPROVED", "amount_inr": 2500, "source": "otel"}],
    )

    # Tick 1: consecutive_breaches = 1 (< 2 required), no wake yet
    res_tick1 = await engine.evaluate_tick()
    assert len(res_tick1.wakes_sent) == 0

    # Tick 2: consecutive_breaches = 2 (>= 2 required) -> EvidencePack + A2A wake + incident created!
    res_tick2 = await engine.evaluate_tick()
    assert len(res_tick2.wakes_sent) == 1
    evp = res_tick2.wakes_sent[0]
    assert evp.sentinel.sentinel_class == "invariant_sql"
    assert evp.breach.consecutive == 2
    assert len(evp.correlated_changes) >= 1
    assert evp.correlated_changes[0].change_event_id == "chg-repo-sha99"
    assert len(evp.recall.episodic) == 1
    assert len(evp.recall.skills) == 1
    assert len(ae.get_a2a_log()) == 1
    assert ae.get_a2a_log()[0]["type"] == "wake"


@pytest.mark.unit
async def test_shadow_sentinel_and_planned_operator_action_suppression() -> None:
    """Shadow sentinels and planned operator windows (Kill Switch / Pause / Hibernate) never wake agents."""
    fs = InMemoryFirestore()
    bq = InMemoryBigQuery()
    ps = InMemoryPubSub()
    ae = InMemoryAgentEngine()
    ae.deploy_agent("application__svc-alpha-01", "application", min_instances=0)
    vec = [1.0] + [0.0] * 767

    engine = SentinelEngine(
        firestore=fs,
        bigquery=bq,
        pubsub=ps,
        agent_engine=ae,
        embed_fn=lambda _: vec,
    )

    # Shadow sentinel
    shadow_snt = _make_sentinel(
        "snt-invariant_sql-shadow", "invariant_sql", state="shadow", consecutive=1
    )
    engine.register_sentinel(shadow_snt)
    bq.insert_rows(
        "events",
        [{"event_id": "e-10", "decision": "APPROVED", "amount_inr": 2500, "source": "otel"}],
    )

    tick_shadow = await engine.evaluate_tick()
    assert len(tick_shadow.wakes_sent) == 0
    assert tick_shadow.shadow_breaches == 1

    # Now register an enforced sentinel, but activate planned operator action (Kill Switch)
    enforced_snt = _make_sentinel(
        "snt-invariant_sql-enforced", "invariant_sql", state="enforced", consecutive=1
    )
    engine.register_sentinel(enforced_snt)
    engine.set_planned_operator_action(active=True, reason="Traffic Kill Switch engaged")

    tick_suppressed = await engine.evaluate_tick()
    assert len(tick_suppressed.wakes_sent) == 0
    assert tick_suppressed.suppressed_by_operator_window >= 1

    # When operator window is cleared, the enforced sentinel wakes normally (Rule B.5 both directions)
    engine.set_planned_operator_action(active=False, reason=None)
    tick_active = await engine.evaluate_tick()
    assert len(tick_active.wakes_sent) == 1

    # Hibernate throttles cadence from 10 s to 60 s
    engine.set_hibernate(True)
    assert engine.cadence_s == 60
    engine.set_hibernate(False)
    assert engine.cadence_s == 10


@pytest.mark.unit
async def test_violations_alias_drift_diff_and_evidence_pack_governor_trust_snapshot() -> None:
    """Findings #4, #8, #9: SELECT COUNT(*) AS violations, drift_diff cost_findings, and EvidencePack budget/trust."""
    from decimal import Decimal

    from godview.sovereign.court_manager.token_governor import TokenBudgetGovernor
    from godview.sovereign.trust_ladder import TrustGate

    fs = InMemoryFirestore()
    bq = InMemoryBigQuery()
    ps = InMemoryPubSub()
    ae = InMemoryAgentEngine()
    ae.deploy_agent("application__svc-alpha-01", "application", min_instances=0)

    gov = TokenBudgetGovernor(firestore=fs, pubsub=ps)
    gate = TrustGate(firestore=fs, pubsub=ps, token_governor=gov)
    gate.promote_to_l1_after_self_check("application__svc-alpha-01", "git.create_rollback_pr")
    for _ in range(3):
        gate.record_recommendation_correct("application__svc-alpha-01", "git.create_rollback_pr")

    await gov.record_usage(
        agent_id="application__svc-alpha-01",
        model_id="gemini-3.8-flash",
        location="global",
        tokens=10000,
        usd_cost=Decimal("0.15"),
    )

    engine = SentinelEngine(
        firestore=fs,
        bigquery=bq,
        pubsub=ps,
        agent_engine=ae,
        token_governor=gov,
        trust_gate=gate,
    )

    snt_violations = SentinelDefinition.model_validate(
        {
            "sentinel_id": "snt-invariant_sql-violations-alias",
            "class": "invariant_sql",
            "source_ref": "inv-seed-01",
            "check": {
                "dialect": "bigquery",
                "statement": "SELECT COUNT(*) AS violations FROM events WHERE decision='APPROVED' AND COALESCE(source, 'otel') != 'probe'",
                "window_s": 300,
            },
            "cadence_s": 10,
            "baseline": {"model": "same_hour", "window_days": 7},
            "wake_condition": {"operator": ">=", "threshold": 1, "consecutive": 1, "window_s": 300},
            "target_agent_id": "application__svc-alpha-01",
            "state": "enforced",
            "compiled_by": "application__svc-alpha-01",
            "compiled_at": datetime.now(tz=UTC).isoformat(),
        }
    )
    snt_drift = SentinelDefinition.model_validate(
        {
            "sentinel_id": "snt-drift_diff-01",
            "class": "drift_diff",
            "source_ref": "drift-check",
            "check": {"dialect": "python", "statement": "drift_count > 0", "window_s": 300},
            "cadence_s": 10,
            "baseline": {"model": "same_hour", "window_days": 7},
            "wake_condition": {"operator": ">=", "threshold": 1, "consecutive": 1, "window_s": 300},
            "target_agent_id": "application__svc-alpha-01",
            "state": "enforced",
            "compiled_by": "application__svc-alpha-01",
            "compiled_at": datetime.now(tz=UTC).isoformat(),
        }
    )
    engine.register_sentinel(snt_violations)
    engine.register_sentinel(snt_drift)

    bq.insert_rows(
        "events",
        [
            {
                "event_id": "ev-v1",
                "decision": "APPROVED",
                "http_status": 503,
                "duration_ms": 480,
                "source": "otel",
            }
        ],
    )
    fs.set(
        "cost_findings",
        "cf-drift-1",
        {"finding_type": "terraform_drift", "resource": "svc-alpha-01"},
    )

    rep = await engine.evaluate_tick()
    assert len(rep.wakes_sent) == 2
    evp = rep.wakes_sent[0]
    assert evp.budget.agent_tokens_remaining == 40000
    assert evp.budget.escalation_allowed is True
    assert evp.trust_snapshot.get("git.create_rollback_pr") == 2
    assert evp.golden_signals.status == "red"
    assert evp.golden_signals.p95_ms == 480.0
