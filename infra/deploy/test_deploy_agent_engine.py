"""Unit tests for infra/deploy/deploy_agent_engine.py, pin_image.py, and extract_workload_vars.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.deploy.deploy_agent_engine import (
    COURT_MAX_AGENTS,
    HermeticAgentEngineBackend,
    build_a2a_card,
    build_agent_payload,
    default_court_roster,
    deploy_court_agent,
    main,
)
from infra.deploy.extract_workload_vars import parse_declared_variables, unwrap_outputs
from infra.deploy.pin_image import parse_existing_images, render_tfvars


def test_build_a2a_card_valid_and_invalid() -> None:
    card = build_a2a_card("platform", "gcp-general-ak", "https://endpoint/a2a")
    assert card["name"] == "platform__gcp-general-ak"
    assert card["url"] == "https://endpoint/a2a"
    assert card["capabilities"] == {"streaming": False, "pushNotifications": False}
    assert any(s["id"] == "audit_reasoning_engine_cost" for s in card["skills"])

    with pytest.raises(ValueError, match="Unsupported agent_type"):
        build_a2a_card("invalid_agent", "gcp-general-ak")


def test_extract_workload_vars_helpers(tmp_path: Path) -> None:
    tf_file = tmp_path / "variables.tf"
    tf_file.write_text(
        'variable "gke_dns_endpoint" {\n  type = string\n}\nvariable "subnet_cidr" {\n  type = string\n}\n',
        encoding="utf-8",
    )
    declared = parse_declared_variables(tmp_path)
    assert declared == {"gke_dns_endpoint", "subnet_cidr"}

    raw = {
        "gke_dns_endpoint": {"value": "10.0.0.1", "type": "string"},
        "subnet_cidr": "10.10.0.0/20",
    }
    assert unwrap_outputs(raw) == {
        "gke_dns_endpoint": "10.0.0.1",
        "subnet_cidr": "10.10.0.0/20",
    }


def test_build_agent_payload_validates_spec_and_locations() -> None:
    payload = build_agent_payload(
        agent_type="application",
        subject_key="underwriting-los-svc",
        sovereign_url="https://god-view-sovereign-xyz.a.run.app",
    )
    assert payload.agent_id == "application__underwriting-los-svc"
    assert payload.location == "us-central1"
    assert payload.staging_bucket == "gs://general-ak-god-view-agent-staging"
    assert payload.deployment_spec.min_instances == 0
    assert payload.deployment_spec.max_instances == 1
    assert payload.deployment_spec.resource_limits == {"cpu": "1", "memory": "2Gi"}
    assert payload.deployment_spec.container_concurrency == 4
    assert payload.deployment_spec.env_vars["GODVIEW_MODEL_LOCATION"] == "global"
    assert payload.deployment_spec.env_vars["GODVIEW_REASONING_MODEL"] == "gemini-4-argon"
    assert payload.deployment_spec.env_vars["GODVIEW_FALLBACK_MODEL"] == "gemini-3.8-flash"
    assert payload.deployment_spec.env_vars["GODVIEW_ESCALATION_MODEL"] == "gemini-4-argon"
    assert payload.deployment_spec.env_vars["GODVIEW_EMBEDDING_LOCATION"] == "us-central1"
    assert payload.labels["godview_managed"] == "true"
    assert payload.labels["godview_role"] == "observer"
    assert payload.a2a_card["name"] == "application__underwriting-los-svc"
    assert len(payload.a2a_card["skills"]) == 5


def test_build_agent_payload_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="Invalid agent_type"):
        build_agent_payload("unknown_type", "svc-a", "https://sovereign.run.app")

    with pytest.raises(ValueError, match="subject_key must be non-empty"):
        build_agent_payload("application", "   ", "https://sovereign.run.app")

    with pytest.raises(ValueError, match="sovereign_url must be a valid HTTP"):
        build_agent_payload("application", "svc-a", "not-a-url")

    with pytest.raises(ValueError, match="staging_bucket must start with gs://"):
        build_agent_payload(
            "application",
            "svc-a",
            "https://sovereign.run.app",
            staging_bucket="https://wrong-bucket",
        )


def test_hermetic_deploy_and_earned_warmth_toggle() -> None:
    from godview_shared.fakes import InMemoryAgentEngine

    shared_fake = InMemoryAgentEngine()
    backend = HermeticAgentEngineBackend(shared_fake=shared_fake)
    payload = build_agent_payload(
        agent_type="data",
        subject_key="god-view-pg",
        sovereign_url="https://god-view-sovereign.a.run.app",
    )
    deployed = deploy_court_agent(backend, payload)
    assert deployed["agent_id"] == "data__god-view-pg"
    assert deployed["deployment_spec"]["min_instances"] == 0
    assert deployed["a2a_card_url"].endswith("/card.json")
    assert shared_fake.get_agent("data__god-view-pg")["state"] == "ACTIVE"

    # Warm to 1 (09:00-17:00 IST earned warmth) and cool back to 0
    warmed = backend.set_min_instances(deployed["name"], 1)
    assert warmed["deployment_spec"]["min_instances"] == 1
    assert shared_fake.get_agent("data__god-view-pg")["deployment_spec"]["min_instances"] == 1
    cooled = backend.set_min_instances(deployed["name"], 0)
    assert cooled["deployment_spec"]["min_instances"] == 0
    assert shared_fake.get_agent("data__god-view-pg")["deployment_spec"]["min_instances"] == 0

    # Negative direction: invalid min_instances rejected
    with pytest.raises(ValueError, match="Earned warmth min_instances must be 0 or 1"):
        backend.set_min_instances(deployed["name"], 2)


def test_court_cap_enforced_and_unmanaged_resources_ignored() -> None:
    from godview_shared.fakes import InMemoryAgentEngine

    shared_fake = InMemoryAgentEngine()
    backend = HermeticAgentEngineBackend(shared_fake=shared_fake)
    # Pre-populate an unmanaged Reasoning Engine (like the 29 pre-existing engines in general-ak)
    shared_fake.deploy_agent(agent_id="legacy-1", agent_type="application")
    backend._labels_by_agent["legacy-1"] = {}
    # Attempting to mutate an unmanaged Reasoning Engine must fail with PermissionError
    with pytest.raises(PermissionError, match="without label godview_managed=true"):
        backend.set_min_instances(
            "projects/hermetic/locations/us-central1/reasoningEngines/legacy-1",
            0,
        )

    # Enforce court cap on godview_managed agents
    for idx in range(3):
        p = build_agent_payload("application", f"svc-{idx}", "https://sovereign.run.app")
        deploy_court_agent(backend, p, max_court_agents=3)

    # Re-deploying an already-existing agent is allowed (idempotent update)
    p_existing = build_agent_payload("application", "svc-0", "https://sovereign.run.app")
    deploy_court_agent(backend, p_existing, max_court_agents=3)

    # Deploying a 4th distinct agent when cap=3 must raise RuntimeError
    p_overflow = build_agent_payload("application", "svc-4", "https://sovereign.run.app")
    with pytest.raises(RuntimeError, match="Court Manager cap reached"):
        deploy_court_agent(backend, p_overflow, max_court_agents=3)


def test_cli_hermetic_all_court_roster(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--all-court", "--hermetic", "--sovereign-url", "https://sovereign.run.app"])
    assert rc == 0
    out = capsys.readouterr().out
    records = json.loads(out)
    assert len(records) == len(default_court_roster())
    assert COURT_MAX_AGENTS == 40
    for rec in records:
        assert rec["labels"]["godview_managed"] == "true"
        assert rec["deployment_spec"]["min_instances"] == 0
        assert rec["deployment_spec"]["resource_limits"] == {"cpu": "1", "memory": "2Gi"}


def test_pin_image_round_trip(tmp_path: Path) -> None:
    tfvars_file = tmp_path / "images.auto.tfvars"
    initial = {
        "loan-orchestrator-api": "us-central1-docker.pkg.dev/general-ak/god-view-containers/loan-orchestrator-api@sha256:aaa",
    }
    tfvars_file.write_text(render_tfvars(initial), encoding="utf-8")
    parsed = parse_existing_images(tfvars_file)
    assert parsed == initial

    parsed["god-view-sovereign"] = (
        "us-central1-docker.pkg.dev/general-ak/god-view-containers/god-view-sovereign@sha256:bbb"
    )
    tfvars_file.write_text(render_tfvars(parsed), encoding="utf-8")
    reparsed = parse_existing_images(tfvars_file)
    assert reparsed["loan-orchestrator-api"].endswith("@sha256:aaa")
    assert reparsed["god-view-sovereign"].endswith("@sha256:bbb")
