"""M1 Unit Tests for Token-Budget Governor (`godview/sovereign/court_manager/token_governor.py`).

Covers TDD I-08, I-09, UC-22, and Coordinator Decision A9:
- Application / Business-Line / Change agents: $0.75/day each
- Data / Platform agents: $0.50/day each
- Sovereign: $1.50/day
- Court daily ceiling: $6.00/day, gemini-4-argon escalation reserve 20% ($1.20)
- Refuses to start without valid budget numbers
- Agent exhaustion -> degrades that agent to L1 until 00:00 IST
- Court daily ceiling exhaustion -> degrades ALL agents to L1 + emits notice
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from godview_shared.fakes import InMemoryFirestore, InMemoryPubSub

from godview.sovereign.court_manager.token_governor import TokenBudgetGovernor


@pytest.mark.unit
def test_governor_refuses_missing_budgets_and_enforces_a9_numbers() -> None:
    """Decision A9: Governor refuses to start without valid positive budget numbers."""
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()

    with pytest.raises(ValueError, match="budget"):
        TokenBudgetGovernor(
            firestore=fs,
            pubsub=ps,
            agent_daily_budgets_usd={},
            court_daily_ceiling_usd=Decimal("6.00"),
        )

    gov = TokenBudgetGovernor(firestore=fs, pubsub=ps)
    assert gov.get_daily_budget_usd("application__svc-1") == Decimal("0.75")
    assert gov.get_daily_budget_usd("business_line__flow-1") == Decimal("0.75")
    assert gov.get_daily_budget_usd("change__repo-1") == Decimal("0.75")
    assert gov.get_daily_budget_usd("data__db-1") == Decimal("0.50")
    assert gov.get_daily_budget_usd("platform__env-1") == Decimal("0.50")
    assert gov.get_daily_budget_usd("sovereign") == Decimal("1.50")
    assert gov.court_daily_ceiling_usd == Decimal("6.00")
    assert gov.escalation_reserve_usd == Decimal("1.20")


@pytest.mark.unit
async def test_agent_exhaustion_degrades_to_l1_and_midnight_ist_reset() -> None:
    """UC-22 & Decision A9: Exhausting daily budget degrades that agent to L1 until 00:00 IST."""
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    gov = TokenBudgetGovernor(firestore=fs, pubsub=ps)

    agent_id = "data__db-primary-01"  # $0.50/day budget
    assert gov.is_agent_degraded_to_l1(agent_id) is False
    assert gov.effective_trust_level(agent_id, nominal_level=3) == 3

    # Spend $0.30 on gemini-3.8-flash at global -> still within $0.50 budget
    await gov.record_usage(
        agent_id=agent_id,
        model_id="gemini-3.8-flash",
        location="global",
        tokens=15000,
        usd_cost=Decimal("0.30"),
    )
    assert gov.is_agent_degraded_to_l1(agent_id) is False
    assert gov.effective_trust_level(agent_id, nominal_level=3) == 3

    # Spend another $0.25 -> total $0.55 >= $0.50 -> agent degraded to L1!
    await gov.record_usage(
        agent_id=agent_id,
        model_id="gemini-3.8-flash",
        location="global",
        tokens=12000,
        usd_cost=Decimal("0.25"),
    )
    assert gov.is_agent_degraded_to_l1(agent_id) is True
    assert gov.effective_trust_level(agent_id, nominal_level=3) == 1
    assert gov.can_escalate_to_argon(agent_id) is False

    # Other agents are still unaffected
    assert gov.is_agent_degraded_to_l1("application__svc-01") is False

    # Reset at 00:00 IST restores agent budget and trust level
    gov.reset_daily_counters_ist()
    assert gov.is_agent_degraded_to_l1(agent_id) is False
    assert gov.effective_trust_level(agent_id, nominal_level=3) == 3


@pytest.mark.unit
async def test_court_ceiling_and_argon_escalation_reserve() -> None:
    """Decision A9: Escalation blocked when remaining court budget < 20% reserve ($1.20); ceiling degrades all."""
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    gov = TokenBudgetGovernor(firestore=fs, pubsub=ps)

    # Default agent model is gemini-4-argon @ global; fallback is gemini-3.8-flash @ global
    assert gov.resolve_agent_model("application__svc-1") == ("gemini-4-argon", "global")
    assert gov.resolve_agent_model("application__svc-1", quota_or_429_fallback=True) == (
        "gemini-3.8-flash",
        "global",
    )

    # Spend $4.90 across court -> remaining is $1.10 (< $1.20 reserve) -> Argon escalation disallowed -> fallback to gemini-3.8-flash
    await gov.record_usage(
        agent_id="sovereign",
        model_id="gemini-4-argon",
        location="global",
        tokens=100000,
        usd_cost=Decimal("4.90"),
    )
    assert gov.can_escalate_to_argon("application__svc-1") is False
    assert gov.resolve_agent_model("application__svc-1") == ("gemini-3.8-flash", "global")

    # Spend another $1.20 -> total $6.10 >= $6.00 ceiling -> ALL agents degraded to L1
    await gov.record_usage(
        agent_id="application__svc-1",
        model_id="gemini-3.8-flash",
        location="global",
        tokens=50000,
        usd_cost=Decimal("1.20"),
    )
    assert gov.court_exhausted is True
    assert gov.effective_trust_level("platform__env-1", nominal_level=3) == 1
