# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Unit and scenario tests for `godview/agents` and Sovereign Harness² inspection routes."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from godview.agents import (
    CONSENSUS_THRESHOLD,
    COURT_DOMAINS,
    MAX_SWARM_DURATION_S,
    MAX_SWARM_ROUNDS,
    A2ATaskRequest,
    AgentExecutor,
    CourtAgent,
    CourtSwarmSession,
    create_agent_app,
    list_archetypes,
    load_seed_harness_tree,
)
from godview.sovereign.control_api import create_app


def test_five_court_archetypes_and_seed_trees() -> None:
    specs = list_archetypes()
    assert [s.domain for s in specs] == list(COURT_DOMAINS)
    for spec in specs:
        assert spec.default_model_id == "gemini-4-argon"
        assert spec.fallback_model_id == "gemini-3.8-flash"
        assert spec.location == "global"
        tree = load_seed_harness_tree(spec.domain)
        assert tree.always_on_tokens() <= 12_000
        assert len(tree.skills()) >= 1
        assert len(tree.scripts()) >= 1
        assert len(tree.sub_agents()) >= 1
        assert tree.hooks_path().is_file()


def test_all_three_failure_scenarios_diagnosed_via_harness2(tmp_path: Path) -> None:
    # Scenario 1: Payload schema offset shift
    app_agent = CourtAgent("application__orchestrator", domain="application")
    assert app_agent.resolve_active_model() == "gemini-4-argon"
    assert (
        app_agent.tree.root / "scripts" / "check_payload_schema.py"
    ).is_file()
    hyp1 = app_agent.diagnose_incident(
        "inc-schema-offset-shift",
        telemetry={"expected_score_offset": 108, "actual_score_offset": 228, "offset": 120},
    )
    # Without live model, specialist cleanly returns model unavailable without fabricating a story
    assert hyp1.confidence == 0.0
    assert hyp1.hypothesis == "not diagnosed: model unavailable"
    assert hyp1.root_cause_category == "model_unavailable"

    # Evolve Scenario 1 through Harness² loop
    res1 = app_agent.evolve_harness(
        "incident_evolve_schema",
        work_root=tmp_path / "s1",
        k=1,
        mode="parallel",
    )
    assert res1.winner.passed is True
    assert res1.winner.score >= 0.85

    # Scenario 2: Statistical distribution / CUSUM drift
    data_agent = CourtAgent("data__audit_specialist", domain="data")
    assert (
        data_agent.tree.root / "scripts" / "check_distribution_shift.py"
    ).is_file()
    hyp2 = data_agent.diagnose_incident(
        "inc-metric-cusum-drift",
        telemetry={"cusum_score": 5.8, "cusum_threshold": 4.0},
    )
    assert hyp2.confidence == 0.0
    assert hyp2.hypothesis == "not diagnosed: model unavailable"
    assert hyp2.root_cause_category == "model_unavailable"

    res2 = data_agent.evolve_harness(
        "incident_distribution_shift",
        work_root=tmp_path / "s2",
        k=1,
        mode="sequential",
    )
    assert res2.winner.passed is True

    # Scenario 3: Container OOM exit 137 + backlog stall
    plat_agent = CourtAgent("platform__infra_specialist", domain="platform")
    assert (
        plat_agent.tree.root / "scripts" / "check_container_restart.py"
    ).is_file()
    hyp3 = plat_agent.diagnose_incident(
        "inc-container-oom-backlog",
        telemetry={"container_exit_code": 137, "dlq_backlog_depth": 42},
    )
    assert hyp3.confidence == 0.0
    assert hyp3.hypothesis == "not diagnosed: model unavailable"
    assert hyp3.root_cause_category == "model_unavailable"

    res3 = plat_agent.evolve_harness(
        "incident_container_oom",
        work_root=tmp_path / "s3",
        k=1,
        mode="parallel",
    )
    assert res3.winner.passed is True


def test_court_swarm_session_consensus_and_argon_escalation() -> None:
    swarm = CourtSwarmSession()
    assert swarm.consensus_threshold == CONSENSUS_THRESHOLD == 0.85
    assert swarm.max_rounds == MAX_SWARM_ROUNDS == 4
    assert swarm.max_duration_s == MAX_SWARM_DURATION_S == 90.0

    # Without model, consensus is not fabricated
    verdict1 = swarm.convene("inc-scenario1-offset-shift")
    assert verdict1.consensus_reached is False
    assert verdict1.consensus_score == 0.0
    assert verdict1.duration_s <= 90.0
    assert verdict1.winning_hypothesis.hypothesis == "not diagnosed: model unavailable"


def test_a2a_agent_cards_and_task_execution() -> None:
    executor = AgentExecutor()
    app = create_agent_app(executor)
    client = TestClient(app)

    r_well_known = client.get("/.well-known/agent.json")
    assert r_well_known.status_code == 200
    card = r_well_known.json()
    assert card["default_model_id"] == "gemini-4-argon"
    assert card["fallback_model_id"] == "gemini-3.8-flash"
    assert card["location"] == "global"
    assert card["capabilities"]["harness2RecursiveEvolution"] is True

    for dom in COURT_DOMAINS:
        r_dom = client.get(f"/a2a/agents/{dom}/card")
        assert r_dom.status_code == 200
        assert r_dom.json()["domain"] == dom

    req = A2ATaskRequest(
        task_id="task-001",
        agent_id="application__specialist",
        incident_id="inc-scenario1-offset-shift",
    )
    r_send = client.post("/a2a/tasks/send", json=req.model_dump())
    assert r_send.status_code == 200
    body = r_send.json()
    assert body["status"] == "completed"
    assert body["model_used"] == "gemini-4-argon"
    assert body["hypothesis"]["confidence"] == 0.0
    assert body["hypothesis"]["hypothesis"] == "not diagnosed: model unavailable"


def test_sovereign_control_api_harness2_inspection_and_reflect_routes() -> None:
    app = create_app()
    client = TestClient(app)

    for prefix in ("/api/v1", "/v1"):
        r_h = client.get(f"{prefix}/agents/application__specialist/harness")
        assert r_h.status_code == 200
        h_data = r_h.json()
        assert h_data["surface"] == "godview"
        assert h_data["model_id"] == "gemini-4-argon"
        assert h_data["validation"]["ok"] is True
        assert h_data["always_on_tokens"] <= 12_000
        assert len(h_data["categories"]["c6_skills"]) >= 1
        assert len(h_data["categories"]["c7_scripts"]) >= 1

        r_exp = client.get(f"{prefix}/agents/application__specialist/experience")
        assert r_exp.status_code == 200
        exp_data = r_exp.json()
        assert exp_data["domain"] == "application"
        assert "Memory" in exp_data["experience_markdown"] or "Experience" in exp_data["experience_markdown"]

        # Verify court and harness endpoints
        r_court = client.get(f"{prefix}/court")
        assert r_court.status_code == 200
        r_tree = client.get(f"{prefix}/harness/application__specialist/tree")
        assert r_tree.status_code == 200
        assert r_tree.json()["surface"] == "godview"
        r_mem = client.post(f"{prefix}/memory/reflect", json={"source": "test"})
        assert r_mem.status_code == 200
