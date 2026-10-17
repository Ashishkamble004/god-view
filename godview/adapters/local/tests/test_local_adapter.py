"""Unit tests for the Local Environment Sense Adapter (`godview/adapters/local/`).

Covers TDD §1 (I-02 randomised fixtures, zero hardcoded service names; Decision A11 observer
exclusion; Decision A19 honest reporting when Docker/kind binaries are absent on Cloudtop),
TDD §6.2 #3, and SDD §2 & §10:
- `docker-compose.yml` project discovery (labels, ports, `/metrics` endpoints, dependencies -> edges)
- `prometheus.yml` scrape target discovery and enrichment
- `kind/cluster.yaml` cluster topology discovery (nodes, port mappings, kube context)
- Local Postgres DSNs extracted from discovered service configs (passwords redacted in locators)
- Local Git remotes and seed SQL table discovery
- Availability states: `available`, `partial` (static configs exist without live Docker daemon per A19),
  `unavailable` (no compose/kind/git artifacts and no daemon)
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest
import yaml
from godview_shared.adapter_contract import SenseCategory

from godview.adapters.local import (
    LocalEnvironmentSenseAdapter,
    build_hermetic_local_adapters,
)


def _rand_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.mark.unit
async def test_local_adapter_discovers_compose_kind_dsn_prometheus_and_git(tmp_path: Path) -> None:
    """Discover randomised compose services, kind cluster, Postgres DSNs, Prometheus targets, and local Git."""
    svc_gateway = _rand_name("svc-gateway")
    svc_worker = _rand_name("svc-worker")
    svc_vm = _rand_name("svc-legacy-vm")
    svc_db = _rand_name("pg-store")
    svc_observer = "god-view-sovereign"
    db_name = f"db_{uuid.uuid4().hex[:6]}"
    tbl_alpha = f"tbl_{uuid.uuid4().hex[:6]}"

    compose_path = tmp_path / "docker-compose.yml"
    compose_data = {
        "services": {
            svc_db: {
                "image": "postgres:16-alpine",
                "environment": {
                    "POSTGRES_DB": db_name,
                    "POSTGRES_USER": "app_user",
                    "POSTGRES_PASSWORD": "super_secret_local_pw",
                },
                "ports": ["5432:5432"],
                "labels": {
                    "godview_managed": "true",
                    "godview_kind": "cloud_sql",
                    "godview_datastore": "postgres",
                },
            },
            svc_vm: {
                "build": {"context": f"./services/{svc_vm}"},
                "environment": {
                    "PORT": "8086",
                    "DATABASE_URL": f"postgresql://app_user:super_secret_local_pw@{svc_db}:5432/{db_name}",
                },
                "ports": ["8086:8086"],
                "depends_on": {svc_db: {"condition": "service_healthy"}},
                "labels": {
                    "godview_managed": "true",
                    "godview_kind": "gce_vm",
                    "metrics_port": "8086",
                    "metrics_path": "/metrics",
                },
            },
            svc_worker: {
                "build": {"context": f"./services/{svc_worker}"},
                "environment": {
                    "PORT": "8080",
                    "DOWNSTREAM_VM_URL": f"http://{svc_vm}:8086",
                    "DATABASE_URL": f"postgresql://app_user:super_secret_local_pw@{svc_db}:5432/{db_name}",
                },
                "ports": ["8082:8080"],
                "depends_on": [svc_db, svc_vm],
                "labels": {
                    "godview_managed": "true",
                    "godview_kind": "gke_workload",
                    "metrics_port": "8080",
                    "metrics_path": "/metrics",
                },
            },
            svc_gateway: {
                "build": {"context": f"./services/{svc_gateway}"},
                "environment": {
                    "PORT": "8080",
                    "WORKER_URL": f"http://{svc_worker}:8080",
                    "DATABASE_URL": f"postgresql://app_user:super_secret_local_pw@{svc_db}:5432/{db_name}",
                },
                "ports": ["8000:8080"],
                "depends_on": [svc_db, svc_worker],
                "labels": {
                    "godview_managed": "true",
                    "godview_kind": "cloud_run",
                    "metrics_port": "8080",
                    "metrics_path": "/metrics",
                },
            },
            svc_observer: {
                "build": {"context": "."},
                "ports": ["8090:8080"],
                "labels": {
                    "godview_managed": "true",
                    "godview_role": "observer",
                    "godview_kind": "cloud_run",
                },
            },
        }
    }
    compose_path.write_text(yaml.safe_dump(compose_data), encoding="utf-8")

    # Prometheus config
    prom_dir = tmp_path / "local-env" / "prometheus"
    prom_dir.mkdir(parents=True)
    prom_path = prom_dir / "prometheus.yml"
    prom_path.write_text(
        yaml.safe_dump(
            {
                "global": {"scrape_interval": "10s"},
                "scrape_configs": [
                    {
                        "job_name": svc_gateway,
                        "metrics_path": "/metrics",
                        "static_configs": [{"targets": [f"{svc_gateway}:8080"]}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    # kind cluster config
    kind_dir = tmp_path / "local-env" / "kind"
    kind_dir.mkdir(parents=True)
    kind_path = kind_dir / "cluster.yaml"
    cluster_name = _rand_name("kind-cluster")
    kind_path.write_text(
        yaml.safe_dump(
            {
                "kind": "Cluster",
                "apiVersion": "kind.x-k8s.io/v1alpha4",
                "name": cluster_name,
                "nodes": [
                    {
                        "role": "control-plane",
                        "extraPortMappings": [
                            {"containerPort": 30081, "hostPort": 18081, "protocol": "TCP"}
                        ],
                    },
                    {"role": "worker"},
                ],
            }
        ),
        encoding="utf-8",
    )

    # Seed SQL
    seed_dir = tmp_path / "local-env" / "seed"
    seed_dir.mkdir(parents=True)
    (seed_dir / "02_schema.sql").write_text(
        f"""
        CREATE TABLE IF NOT EXISTS {tbl_alpha} (
            item_id UUID PRIMARY KEY,
            journey_id VARCHAR(64) NOT NULL,
            customer_id UUID NOT NULL,
            status VARCHAR(32) NOT NULL
        );
        """,
        encoding="utf-8",
    )

    # Local bare Git repo
    git_dir = tmp_path / "local-env" / "git" / "repos" / "local-app.git"
    git_dir.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(git_dir)],
        check=True,
        capture_output=True,
    )

    adapter = LocalEnvironmentSenseAdapter(
        compose_path=compose_path,
        kind_config_path=kind_path,
        prometheus_config_path=prom_path,
        seed_sql_dir=seed_dir,
        git_repos_dir=git_dir.parent,
        docker_daemon_probe=lambda: False,  # Decision A19: no Docker daemon on Cloudtop
        kind_binary_probe=lambda: False,
    )

    assert SenseCategory.SCOPED_OBSERVABILITY in adapter.senses()
    assert SenseCategory.DATA in adapter.senses()
    assert SenseCategory.CODE in adapter.senses()

    health = await adapter.health()
    # Static files exist, Docker daemon is absent -> honest "partial" state with explicit evidence
    assert health.state == "partial"
    assert health.evidence["compose_file_present"] is True
    assert health.evidence["kind_config_present"] is True
    assert health.evidence["docker_daemon_available"] is False
    assert health.evidence["kind_binary_available"] is False

    discovery = await adapter.discover()
    service_names = {s.name for s in discovery.services}
    assert {svc_gateway, svc_worker, svc_vm, svc_db} == service_names
    assert svc_observer not in service_names

    by_name = {s.name: s for s in discovery.services}
    assert by_name[svc_gateway].kind == "cloud_run"
    assert by_name[svc_gateway].metrics_endpoint == f"http://{svc_gateway}:8080/metrics"
    assert by_name[svc_worker].kind == "gke_workload"
    assert by_name[svc_vm].kind == "gce_vm"
    assert by_name[svc_vm].metrics_endpoint == f"http://{svc_vm}:8086/metrics"
    assert by_name[svc_db].kind == "cloud_sql"
    # Password redacted in locator
    assert "super_secret_local_pw" not in by_name[svc_db].locator

    # Discovered Postgres DSNs in evidence (with redacted passwords)
    assert len(discovery.evidence["postgres_dsns"]) >= 1
    assert all("super_secret_local_pw" not in dsn for dsn in discovery.evidence["postgres_dsns"])
    assert discovery.evidence["kind_cluster"]["name"] == cluster_name
    assert len(discovery.evidence["local_git_remotes"]) == 1

    # Discovered edges from depends_on / env URLs
    edge_pairs = {(e.src, e.dst) for e in discovery.edges}
    assert (
        f"local-compose__cloud_run.{svc_gateway}",
        f"local-compose__gke_workload.{svc_worker}",
    ) in edge_pairs

    # Discovered entities from seed SQL
    assert any(
        ent.name == tbl_alpha and "journey_id" in ent.correlation_keys for ent in discovery.entities
    )


@pytest.mark.unit
async def test_local_adapter_available_and_unavailable_states_and_hermetic_helper(
    tmp_path: Path,
) -> None:
    """Verify 'available' state when daemon/hermetic mode is active and 'unavailable' when nothing exists."""
    compose_path = tmp_path / "docker-compose.yml"
    svc_name = _rand_name("svc-hermetic")
    compose_path.write_text(
        yaml.safe_dump(
            {
                "services": {
                    svc_name: {
                        "image": "alpine:3.20",
                        "ports": ["8080:8080"],
                        "labels": {
                            "godview_managed": "true",
                            "godview_kind": "cloud_run",
                            "metrics_port": "8080",
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    avail_adapter = LocalEnvironmentSenseAdapter(
        compose_path=compose_path,
        docker_daemon_probe=lambda: True,
        kind_binary_probe=lambda: True,
    )
    h_avail = await avail_adapter.health()
    assert h_avail.state == "available"

    unavail_adapter = LocalEnvironmentSenseAdapter(
        compose_path=tmp_path / "nonexistent-compose.yml",
        kind_config_path=tmp_path / "nonexistent-kind.yaml",
        git_repos_dir=tmp_path / "nonexistent-git",
        docker_daemon_probe=lambda: False,
        kind_binary_probe=lambda: False,
    )
    h_unavail = await unavail_adapter.health()
    assert h_unavail.state == "unavailable"

    # Hermetic helper returns working SenseAdapter instances only when GODVIEW_ENV=='hermetic' (Finding 10)
    prev_env = os.environ.get("GODVIEW_ENV")
    try:
        os.environ["GODVIEW_ENV"] = "hermetic"
        hermetic_adapters = build_hermetic_local_adapters()
        assert len(hermetic_adapters) >= 1
        disc = await hermetic_adapters[0].discover()
        assert len(disc.services) >= 1

        os.environ["GODVIEW_ENV"] = "gcp"
        with pytest.raises(RuntimeError, match="GODVIEW_ENV='hermetic'"):
            build_hermetic_local_adapters()
    finally:
        if prev_env is not None:
            os.environ["GODVIEW_ENV"] = prev_env
        else:
            os.environ.pop("GODVIEW_ENV", None)

    from datetime import UTC, datetime

    import httpx
    from godview_shared.contracts import TrustDecision, TrustViolation

    # Verify probe.execute defaults closed on undiscovered routes, requires >=L1 TrustDecision for non-GET,
    # and injects W3C baggage synthetic=true + X-Synthetic: true (Finding 1)
    seen_headers: list[dict[str, str]] = []

    def probe_handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(dict(request.headers))
        return httpx.Response(200, json={"decision": "APPROVED"})

    probe_adapter = LocalEnvironmentSenseAdapter(
        compose_path=compose_path,
        discovered_routes=[
            {"method": "GET", "path": "/v1/safe", "x-godview-probe-safe": True},
            {"method": "POST", "path": "/v1/safe-write", "x-godview-probe-safe": True},
        ],
        http_transport=httpx.MockTransport(probe_handler),
    )

    # Direction 1: Undiscovered route refused by default
    with pytest.raises(TrustViolation, match="x-godview-probe-safe"):
        await probe_adapter.execute_capability(
            "probe.execute",
            {"url": f"http://{svc_name}:8080/admin/v1/replay", "method": "POST", "json": {}},
        )

    # Direction 2: Explicit probe_safe=False refused
    with pytest.raises(TrustViolation, match="probe_safe=False"):
        await probe_adapter.execute_capability(
            "probe.execute",
            {"url": f"http://{svc_name}:8080/v1/safe", "probe_safe": False},
        )

    # Direction 3: Non-GET write probe on discovered probe-safe route refused without >=L1 TrustDecision
    with pytest.raises(TrustViolation, match="effective_level >= 1"):
        await probe_adapter.execute_capability(
            "probe.execute",
            {
                "url": f"http://{svc_name}:8080/v1/safe-write",
                "method": "POST",
                "json": {"amount": 100},
            },
        )

    # Direction 4: GET probe on discovered probe-safe route succeeds and sends baggage synthetic=true
    probe_res = await probe_adapter.execute_capability(
        "probe.execute",
        {"url": f"http://{svc_name}:8080/v1/safe", "method": "GET"},
    )
    assert probe_res["status_code"] == 200
    assert probe_res["synthetic_marker_sent"] is True
    assert seen_headers[0].get("x-synthetic") == "true"
    assert "synthetic=true" in seen_headers[0].get("baggage", "")

    # Direction 5: POST probe on discovered probe-safe route with L1 TrustDecision succeeds
    td_probe = TrustDecision(
        authorized=True,
        agent_id="app-agent",
        action_type="probe.execute",
        capability_id="probe.execute",
        effective_level=1,
        required_level=0,
        sa_to_impersonate="godview-sense-sa",
        reason="synthetic write probe",
        decided_at=datetime.now(tz=UTC),
    )
    post_res = await probe_adapter.execute_capability(
        "probe.execute",
        {"url": f"http://{svc_name}:8080/v1/safe-write", "method": "POST", "json": {"amount": 100}},
        trust_decision=td_probe,
    )
    assert post_res["status_code"] == 200
    assert "synthetic=true" in seen_headers[-1].get("baggage", "")
