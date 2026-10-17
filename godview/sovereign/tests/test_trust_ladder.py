"""M1 Unit Tests for Trust Ladder & TrustGate (`godview/sovereign/trust_ladder/`).

Covers TDD I-06 (TrustGate before every mutating call, blast radius = 1, godview-act-sa
impersonation, L2 approval_id, money/customer-comms caps) and Coordinator Decision A10
(bad outcome resets to L1, L4 whitelisting only by human owner from Cockpit recording
whitelisted_by, global kill switch drains 1 in-flight step and auto-expires new L2 approvals).
"""

from __future__ import annotations

import pytest
from godview_shared.contracts import TrustViolation
from godview_shared.fakes import InMemoryFirestore, InMemoryPubSub

from godview.sovereign.trust_ladder import TrustGate


@pytest.mark.unit
def test_trust_promotions_n1_n2_n3_and_a10_demotion_to_l1() -> None:
    """UC-17 & Decision A10: Promotion N1=3->L2, N2=5->L3, N3=10+whitelist->L4; bad outcome resets to L1."""
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    gate = TrustGate(firestore=fs, pubsub=ps)

    agent_id = "data__pg-primary-01"
    action = "sql.terminate_backend"

    # Starts at L0 -> passes self-consistency -> L1
    rec0 = gate.get_or_create_record(agent_id, action)
    assert rec0.level == 0
    rec1 = gate.promote_to_l1_after_self_check(agent_id, action)
    assert rec1.level == 1

    # N1 = 3 correct recommendations -> L2
    for _ in range(3):
        rec2 = gate.record_recommendation_correct(agent_id, action)
    assert rec2.level == 2

    # N2 = 5 approved executions with verified outcomes and 0 regressions -> L3
    for _ in range(5):
        rec3 = gate.record_approved_execution_verified(agent_id, action)
    assert rec3.level == 3

    # At L3, sql.terminate_backend (min_level=3) is authorized autonomously with godview-act-sa
    decision = gate.authorize(
        agent_id=agent_id,
        capability_id=action,
        target_resource="pid:4192",
        blast_radius_resources=1,
    )
    assert decision.authorized is True
    assert decision.effective_level == 3
    assert decision.sa_to_impersonate == "godview-act-sa"

    # Blast radius != 1 must raise TrustViolation (TDD I-06)
    with pytest.raises(TrustViolation, match="Blast radius"):
        gate.authorize(
            agent_id=agent_id,
            capability_id=action,
            target_resource="pid:*",
            blast_radius_resources=2,
        )

    # Decision A10: A single bad outcome resets that action type to L1 (not level-1 = L2!)
    demoted = gate.record_bad_outcome(
        agent_id, action, reason="Regression inside verification window"
    )
    assert demoted.level == 1
    assert demoted.history[-1].from_level == 3
    assert demoted.history[-1].to == 1


@pytest.mark.unit
def test_l4_cockpit_owner_whitelist_and_money_comms_caps() -> None:
    """Decision A10 & TDD I-06: L4 requires N3=10 + human IAP owner whitelist; customer comms capped at L2."""
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    gate = TrustGate(firestore=fs, pubsub=ps)

    agent_id = "business_line__origination_flow"
    action = "biz.replay_transaction"

    gate.promote_to_l1_after_self_check(agent_id, action)
    for _ in range(3):
        gate.record_recommendation_correct(agent_id, action)
    for _ in range(5):
        gate.record_approved_execution_verified(agent_id, action)
    for _ in range(10):
        rec_before_wl = gate.record_business_success(agent_id, action)

    # Without human whitelist, money-movement action stays capped and cannot reach L4
    assert rec_before_wl.level == 3
    dec_capped = gate.authorize(
        agent_id=agent_id,
        capability_id=action,
        target_resource="ord-101",
    )
    assert dec_capped.authorized is False
    assert dec_capped.effective_level == 2  # money movement capped at L2 without whitelist

    # Non-human / missing IAP identity cannot whitelist for L4 (Decision A10)
    with pytest.raises(TrustViolation, match="IAP"):
        gate.whitelist_action(agent_id, action, iap_principal="")

    # Human owner from Cockpit whitelists action -> promotes to L4 and records whitelisted_by
    rec_l4 = gate.whitelist_action(agent_id, action, iap_principal="admin@example.com")
    assert rec_l4.whitelisted is True
    assert rec_l4.whitelisted_by == "admin@example.com"
    assert rec_l4.level == 4

    dec_l4 = gate.authorize(
        agent_id=agent_id,
        capability_id=action,
        target_resource="ord-101",
    )
    assert dec_l4.authorized is True
    assert dec_l4.effective_level == 4

    # Novel skill first mutating step ALWAYS requires 1-click approval even at L4 (Agent Spec §8.1 #3)
    dec_novel = gate.authorize(
        agent_id=agent_id,
        capability_id=action,
        target_resource="ord-101",
        novel_skill_first_step=True,
    )
    assert dec_novel.authorized is False
    assert "novel skill" in dec_novel.reason.lower()

    # Customer communication is NEVER whitelisted in v1 and capped at L2
    with pytest.raises(TrustViolation, match="comms.customer_notify"):
        gate.whitelist_action(agent_id, "comms.customer_notify", iap_principal="admin@example.com")


@pytest.mark.unit
def test_global_kill_switch_drains_one_inflight_and_auto_expires_new_approvals() -> None:
    """Decision A10: Global kill switch lets 1 in-flight step finish, aborts queued, auto-expires new L2 approvals."""
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    gate = TrustGate(firestore=fs, pubsub=ps)

    gate.register_inflight_step("step-inflight-1")
    gate.enqueue_step("step-queued-2")
    gate.enqueue_step("step-queued-3")

    drain_report = gate.set_global_kill_switch(enabled=True, actor="admin@example.com")
    assert drain_report["inflight_allowed_to_finish"] == ["step-inflight-1"]
    assert set(drain_report["queued_aborted"]) == {"step-queued-2", "step-queued-3"}

    # New approval requested while kill switch is active is auto-expired (Decision A10)
    card = gate.create_approval_request(
        incident_id="inc-20261016-0009",
        agent_id="application__svc-a",
        capability_id="git.create_rollback_pr",
        target_resource="org/repo/pull/9",
        trust_level_required=3,
        narrative="Rollback regression",
        evidence_pack_id="evp-20261016-0009",
        financial_impact=500000.0,
        reversal_recipe={"capability": "git.create_rollback_pr"},
    )
    assert card.decision == "expired"

    # Disabling kill switch restores normal pending approval creation (Rule B.5 both directions)
    gate.set_global_kill_switch(enabled=False, actor="admin@example.com")
    card_normal = gate.create_approval_request(
        incident_id="inc-20261016-0010",
        agent_id="application__svc-a",
        capability_id="git.create_rollback_pr",
        target_resource="org/repo/pull/10",
        trust_level_required=3,
        narrative="Rollback regression",
        evidence_pack_id="evp-20261016-0010",
        financial_impact=500000.0,
        reversal_recipe={"capability": "git.create_rollback_pr"},
    )
    assert card_normal.decision == "pending"


@pytest.mark.unit
def test_approval_id_cross_capability_agent_target_and_replay_rejected_plus_unwhitelist() -> None:
    """Findings #5 & #6: approval_id binds capability, agent_id, target_resource, single-use; whitelisted=False demotes L4."""
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    gate = TrustGate(firestore=fs, pubsub=ps)

    card = gate.create_approval_request(
        incident_id="inc-20261016-0100",
        agent_id="platform__env-01",
        capability_id="k8s.scale",
        target_resource="deployment/svc-alpha-01",
        trust_level_required=2,
        narrative="Scale replicas",
        evidence_pack_id="evp-20261016-0100",
    )
    gate.decide_approval(card.approval_id, decision="approved", decided_by="owner@example.com")

    # 1. Cross-capability hijack -> TrustViolation
    with pytest.raises(TrustViolation, match="capability"):
        gate.authorize(
            agent_id="platform__env-01",
            capability_id="sql.terminate_backend",
            target_resource="deployment/svc-alpha-01",
            approval_id=card.approval_id,
        )

    # 2. Cross-agent hijack -> TrustViolation
    with pytest.raises(TrustViolation, match="agent"):
        gate.authorize(
            agent_id="data__pg-01",
            capability_id="k8s.scale",
            target_resource="deployment/svc-alpha-01",
            approval_id=card.approval_id,
        )

    # 3. Cross-target hijack -> TrustViolation
    with pytest.raises(TrustViolation, match="target"):
        gate.authorize(
            agent_id="platform__env-01",
            capability_id="k8s.scale",
            target_resource="deployment/svc-beta-02",
            approval_id=card.approval_id,
        )

    # 4. Exact match succeeds once and consumes the approval
    dec_ok = gate.authorize(
        agent_id="platform__env-01",
        capability_id="k8s.scale",
        target_resource="deployment/svc-alpha-01",
        approval_id=card.approval_id,
    )
    assert dec_ok.authorized is True

    # 5. Reusing a consumed approval_id -> TrustViolation
    with pytest.raises(TrustViolation, match="consumed"):
        gate.authorize(
            agent_id="platform__env-01",
            capability_id="k8s.scale",
            target_resource="deployment/svc-alpha-01",
            approval_id=card.approval_id,
        )

    # 6. Whitelist toggle off (whitelisted=False) demotes L4 -> L3 and clears whitelisted_by
    gate.promote_to_l1_after_self_check("platform__env-01", "k8s.scale")
    for _ in range(3):
        gate.record_recommendation_correct("platform__env-01", "k8s.scale")
    for _ in range(5):
        gate.record_approved_execution_verified("platform__env-01", "k8s.scale")
    for _ in range(10):
        gate.record_business_success("platform__env-01", "k8s.scale")
    rec_wl = gate.whitelist_action(
        "platform__env-01", "k8s.scale", iap_principal="owner@example.com"
    )
    assert rec_wl.whitelisted is True
    assert rec_wl.level == 4

    rec_unwl = gate.whitelist_action(
        "platform__env-01",
        "k8s.scale",
        iap_principal="owner@example.com",
        whitelisted=False,
    )
    assert rec_unwl.whitelisted is False
    assert rec_unwl.whitelisted_by is None
    assert rec_unwl.level == 3
