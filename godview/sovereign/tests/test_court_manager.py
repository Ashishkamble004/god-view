"""M1 Unit Tests for Court Manager (`godview/sovereign/court_manager/`).

Covers TDD §2.3 (court_agents lifecycle), §3.5 (swarm exit conditions: consensus >= 0.85
or <= 4 rounds or <= 90 s), UC-03 (spawn/retire), UC-19 & UC-21 (earned warmth 09:00-17:00 IST
for top 2-3 hottest agents, cool to 0 at pause/hibernate), and Coordinator Decision A1
(hard cap GODVIEW_COURT_MAX_AGENTS=40).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from godview_shared.fakes import InMemoryAgentEngine, InMemoryFirestore, InMemoryPubSub

from godview.sovereign.court_manager import CourtCapacityError, CourtManager

IST = ZoneInfo("Asia/Kolkata")


@pytest.mark.unit
async def test_spawn_brief_retire_and_a1_max_agents_cap() -> None:
    """Spawn 5 specialist agent types, enforce A1 hard cap, and archive memory on retire."""
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    ae = InMemoryAgentEngine()
    cm = CourtManager(firestore=fs, pubsub=ps, agent_engine=ae, max_agents=5)

    a1 = await cm.spawn_agent("application", "svc-alpha-01")
    a2 = await cm.spawn_agent("data", "db-primary-01")
    a3 = await cm.spawn_agent("business_line", "origination_flow")
    a4 = await cm.spawn_agent("change", "repo-alpha")
    a5 = await cm.spawn_agent("platform", "env-neutral-01")

    assert a1.doc_id == "application__svc-alpha-01"
    assert a1.warmth_min_instances == 0
    assert a2.agent_type == "data"
    assert a3.agent_type == "business_line"
    assert a4.agent_type == "change"
    assert len(cm.list_active_agents()) == 5
    assert a5.agent_type == "platform"

    # Decision A1: Exceeding max_agents raises CourtCapacityError
    with pytest.raises(CourtCapacityError, match="max_agents=5"):
        await cm.spawn_agent("application", "svc-overflow-06")

    # Write private working memory and retire a1 -> consolidated into Firestore & archived
    ae.write_memory_bank("application__svc-alpha-01", "learned_boundary", {"threshold": 60})
    retired = await cm.retire_agent("application__svc-alpha-01")
    assert retired.state == "archived"
    assert retired.warmth_min_instances == 0
    assert len(cm.list_active_agents()) == 4

    # Now spawning a new agent succeeds because active count is 4 < 5
    a6 = await cm.spawn_agent("application", "svc-new-06")
    assert a6.state in ("sleeping", "learning", "spawning")


@pytest.mark.unit
async def test_earned_warmth_window_and_scheduled_pause_cooling() -> None:
    """UC-19 & UC-21: Top 2-3 hottest agents warmed to min_instances=1 in 09:00-17:00 IST, cooled outside."""
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    ae = InMemoryAgentEngine()
    cm = CourtManager(firestore=fs, pubsub=ps, agent_engine=ae, max_warm_agents=2)

    for idx, wakes in [("svc-1", 12), ("svc-2", 8), ("svc-3", 4), ("svc-4", 1)]:
        doc = await cm.spawn_agent("application", idx)
        fs.set("court_agents", doc.doc_id or f"application__{idx}", {"wakes_7d": wakes}, merge=True)

    # Inside 09:00-17:00 IST window (e.g. 11:30 IST): top 2 agents warmed to 1, others 0
    inside_ist = datetime(2026, 10, 16, 11, 30, tzinfo=IST)
    warmed = await cm.evaluate_earned_warmth(now_ist=inside_ist)
    assert set(warmed) == {"application__svc-1", "application__svc-2"}
    assert ae.get_agent("application__svc-1")["deployment_spec"]["min_instances"] == 1
    assert ae.get_agent("application__svc-2")["deployment_spec"]["min_instances"] == 1
    assert ae.get_agent("application__svc-3")["deployment_spec"]["min_instances"] == 0

    # Outside window (17:01 IST Scheduled Pause): all agents cooled to min_instances=0
    outside_ist = datetime(2026, 10, 16, 17, 1, tzinfo=IST)
    still_warm = await cm.evaluate_earned_warmth(now_ist=outside_ist)
    assert still_warm == []
    for idx in ("svc-1", "svc-2", "svc-3", "svc-4"):
        assert ae.get_agent(f"application__{idx}")["deployment_spec"]["min_instances"] == 0


@pytest.mark.unit
def test_swarm_convergence_exit_conditions() -> None:
    """TDD §3.5: Swarm exits at consensus >= 0.85 OR rounds >= 4 OR elapsed >= 90 s."""
    fs = InMemoryFirestore()
    ps = InMemoryPubSub()
    ae = InMemoryAgentEngine()
    cm = CourtManager(firestore=fs, pubsub=ps, agent_engine=ae)

    # Not yet exited at round 2, consensus 0.70, 35 s
    should_exit, reason = cm.check_swarm_exit(consensus=0.70, rounds=2, elapsed_seconds=35.0)
    assert should_exit is False
    assert reason == "continue"

    # Exit on consensus >= 0.85
    should_exit_c, reason_c = cm.check_swarm_exit(consensus=0.91, rounds=2, elapsed_seconds=41.0)
    assert should_exit_c is True
    assert reason_c == "consensus_reached"

    # Exit on rounds == 4
    should_exit_r, reason_r = cm.check_swarm_exit(consensus=0.60, rounds=4, elapsed_seconds=50.0)
    assert should_exit_r is True
    assert reason_r == "max_rounds_reached"

    # Exit on timeout >= 90 s
    should_exit_t, reason_t = cm.check_swarm_exit(consensus=0.50, rounds=2, elapsed_seconds=90.5)
    assert should_exit_t is True
    assert reason_t == "timeout_reached"
