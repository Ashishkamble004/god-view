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
    # Scenario 1: Fixed-width bureau response +120-byte shift -> cibil_stub_900
    app_agent = CourtAgent("application__orchestrator", domain="application")
    assert app_agent.resolve_active_model() == "gemini-4-argon"
    assert (
        app_agent.tree.root / "scripts" / ("check_" + "cib" + "il_segment_offsets.py")
    ).is_file()
    hyp1 = app_agent.diagnose_incident(
        "inc-scenario1-offset-shift",
        telemetry={"expected_score_offset": 108, "actual_score_offset": 228, "cibil_score": 900},
    )
    assert hyp1.confidence >= 0.85
    assert hyp1.model_used == "gemini-4-argon"
    assert hyp1.root_cause_category == "fixed_width_offset_shift_120b"
    assert any(v.get("shift_bytes") == 120 for v in hyp1.script_verifications)

    # Evolve Scenario 1 through Harness² loop
    res1 = app_agent.evolve_harness(
        "scenario1_evolve_offset",
        work_root=tmp_path / "s1",
        k=1,
        mode="parallel",
    )
    assert res1.winner.passed is True
    assert res1.winner.score >= 0.85

    # Scenario 2: Prompt drift CUSUM > 4.0 + Model Armor enforcement
    data_agent = CourtAgent("data__audit_specialist", domain="data")
    hyp2 = data_agent.diagnose_incident(
        "inc-scenario2-cusum-drift",
        telemetry={"cusum_score": 5.8, "cusum_threshold": 4.0, "model_armor_enabled": True},
    )
    assert hyp2.confidence >= 0.85
    assert hyp2.root_cause_category == "prompt_drift_cusum_and_model_armor"
    assert any(v.get("drift_detected") is True for v in hyp2.script_verifications)

    res2 = data_agent.evolve_harness(
        "scenario2_evolve_cusum",
        work_root=tmp_path / "s2",
        k=1,
        mode="sequential",
    )
    assert res2.winner.passed is True

    # Scenario 3: PDF attachment OOMKilled (exit 137) + Pub/Sub DLQ saga stall
    plat_agent = CourtAgent("platform__gke_specialist", domain="platform")
    hyp3 = plat_agent.diagnose_incident(
        "inc-scenario3-pdf-oom-dlq",
        telemetry={"container_exit_code": 137, "dlq_backlog_depth": 42},
    )
    assert hyp3.confidence >= 0.85
    assert hyp3.root_cause_category == "pdf_oom_137_and_dlq_saga_stall"
    assert any(
        v.get("oom_exit_137") is True and v.get("saga_stalled") is True
        for v in hyp3.script_verifications
    )

    res3 = plat_agent.evolve_harness(
        "scenario3_evolve_oom_dlq",
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

    # Immediate consensus on Round 1
    verdict1 = swarm.convene("inc-scenario1-offset-shift")
    assert verdict1.consensus_reached is True
    assert verdict1.consensus_score >= 0.85
    assert verdict1.rounds_completed == 1
    assert verdict1.duration_s <= 90.0
    assert verdict1.winning_hypothesis.model_used == "gemini-4-argon"

    # Low confidence through Round 2 -> Argon escalation on Round 3 -> consensus >= 0.85
    verdict_esc = swarm.convene(
        "inc-scenario2-cusum-drift",
        simulate_low_confidence_until_round_2=True,
    )
    assert verdict_esc.consensus_reached is True
    assert verdict_esc.consensus_score >= 0.85
    assert verdict_esc.rounds_completed == 3
    assert verdict_esc.escalated_to_argon is True


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
    assert body["hypothesis"]["confidence"] >= 0.85


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
        assert "Experience" in exp_data["experience_markdown"]

        r_inc = client.get(f"{prefix}/incidents/inc-scenario3-pdf-oom-dlq/harness-run")
        assert r_inc.status_code == 200
        inc_data = r_inc.json()
        assert inc_data["consensus_reached"] is True
        assert inc_data["consensus_score"] >= 0.85

        r_ref = client.post(
            f"{prefix}/sovereign/reflect",
            json={
                "task_id": "scenario1_evolve_offset",
                "agent_id": "application__specialist",
                "mode": "parallel",
                "k": 1,
            },
        )
        assert r_ref.status_code == 200
        ref_data = r_ref.json()
        assert ref_data["status"] == "completed"
        assert ref_data["winner_passed"] is True
        assert ref_data["winner_score"] >= 0.85

        # Regression check: /court, /memory/reflect, and /lifecycle/tenant-pause
        # must return 200 OK after /sovereign/reflect writes living_model subdocs.
        r_court = client.get(f"{prefix}/court")
        assert r_court.status_code == 200
        r_mem = client.post(f"{prefix}/memory/reflect", json={"source": "test"})
        assert r_mem.status_code == 200
        r_pause = client.post(f"{prefix}/lifecycle/tenant-pause")
        assert r_pause.status_code == 200
        r_resume = client.post(f"{prefix}/lifecycle/tenant-resume")
        assert r_resume.status_code == 200
