"""Tests for infra/terraform/workloads, .github/workflows, Makefile, docker-compose.yml, .gitignore, and local-env."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKLOADS_DIR = REPO_ROOT / "infra" / "terraform" / "workloads"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
LOCAL_ENV_DIR = REPO_ROOT / "local-env"
MAKEFILE_PATH = REPO_ROOT / "Makefile"
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
GITIGNORE_PATH = REPO_ROOT / ".gitignore"


def _find_terraform() -> str:
    env_path = f"{Path.home() / '.local' / 'bin'}:{os.environ.get('PATH', '')}"
    tf = shutil.which("terraform", path=env_path)
    if not tf:
        raise RuntimeError("terraform binary not found in ~/.local/bin or PATH")
    return tf


# ---------------------------------------------------------------------------
# 1. .gitignore tests (both directions: excluded paths vs kept outputs.json)
# ---------------------------------------------------------------------------


def test_gitignore_excludes_required_patterns_and_keeps_outputs_json() -> None:
    assert GITIGNORE_PATH.is_file(), ".gitignore must exist at repository root"
    content = GITIGNORE_PATH.read_text(encoding="utf-8")

    required_patterns = [
        ".venv/",
        "node_modules/",
        "*/dist/",
        ".terraform/",
        "*.tfstate*",
    ]
    for pattern in required_patterns:
        assert pattern in content, f".gitignore missing required exclusion pattern: {pattern}"

    # Negative direction: outputs.json must be explicitly kept and never ignored
    assert "!**/outputs.json" in content or "!outputs.json" in content, (
        ".gitignore must explicitly un-ignore outputs.json (!**/outputs.json)"
    )
    assert "\noutputs.json\n" not in content, ".gitignore must not ignore outputs.json"


# ---------------------------------------------------------------------------
# 2. Terraform workloads root (infra/terraform/workloads/)
# ---------------------------------------------------------------------------


def test_workloads_files_exist() -> None:
    expected = [
        "backend.tf",
        "providers.tf",
        "variables.tf",
        "main.tf",
        "outputs.tf",
        "images.auto.tfvars",
    ]
    for filename in expected:
        path = WORKLOADS_DIR / filename
        assert path.is_file(), f"Missing workloads Terraform file: {path}"


def test_workloads_provider_uses_access_token_and_supports_kind_mode() -> None:
    providers_tf = (WORKLOADS_DIR / "providers.tf").read_text(encoding="utf-8")
    backend_tf = (WORKLOADS_DIR / "backend.tf").read_text(encoding="utf-8")
    variables_tf = (WORKLOADS_DIR / "variables.tf").read_text(encoding="utf-8")

    # GCS backend with bucket general-ak-god-view-tfstate and prefix workloads
    assert 'bucket = "general-ak-god-view-tfstate"' in backend_tf
    assert 'prefix = "workloads"' in backend_tf

    # Must use google_container_cluster data + google_client_config access_token
    assert 'data "google_container_cluster"' in providers_tf
    assert 'data "google_client_config"' in providers_tf
    assert "access_token" in providers_tf

    # Negative check: no gke-gcloud-auth-plugin exec block
    assert "gke-gcloud-auth-plugin" not in providers_tf

    # Must also target kind via provider_mode variable
    assert 'variable "provider_mode"' in variables_tf
    assert "var.provider_mode" in providers_tf
    assert "var.kube_context" in providers_tf


def test_workloads_resources_match_spec() -> None:
    main_tf = (WORKLOADS_DIR / "main.tf").read_text(encoding="utf-8")
    variables_tf = (WORKLOADS_DIR / "variables.tf").read_text(encoding="utf-8")
    tfvars = (WORKLOADS_DIR / "images.auto.tfvars").read_text(encoding="utf-8")

    # Namespace cymbal with required labels
    assert 'resource "kubernetes_namespace_v1" "cymbal"' in main_tf
    assert '"cymbal"' in main_tf
    assert 'godview_managed = "true"' in main_tf
    assert 'tenant          = "cymbal"' in main_tf
    assert 'tf_module       = "workloads"' in main_tf

    # KSAs for kyc-model-armor-svc and underwriting-los-svc bound to godview-runtime-sa
    assert 'resource "kubernetes_service_account_v1"' in main_tf
    assert "iam.gke.io/gcp-service-account" in main_tf
    assert "godview-runtime-sa@" in main_tf
    assert "kyc-model-armor-svc" in main_tf
    assert "underwriting-los-svc" in main_tf

    # Deployments at 250m / 512Mi x 1 replica (0 when hibernate=true)
    assert 'resource "kubernetes_deployment_v1" "utility"' in main_tf
    assert '"250m"' in variables_tf or '"250m"' in main_tf
    assert '"512Mi"' in variables_tf or '"512Mi"' in main_tf
    assert "var.hibernate ? 0 : var.gke_replicas" in main_tf
    assert "8080" in main_tf
    assert '"/healthz"' in main_tf

    # Negative check: HPA-off by design
    assert "kubernetes_horizontal_pod_autoscaler" not in main_tf

    # Internal LB Services with reserved ILB annotation + load_balancer_ip fallback (Decision A4)
    assert 'resource "kubernetes_service_v1" "utility"' in main_tf
    assert "networking.gke.io/load-balancer-type" in main_tf
    assert "networking.gke.io/load-balancer-ip-addresses" in main_tf
    assert "load_balancer_ip" in main_tf

    # Platform remote state consumption (gcs backend, prefix platform) + precondition checks
    assert 'data "terraform_remote_state" "platform"' in main_tf
    assert "precondition" in main_tf

    # ConfigMap must include DB_PASSWORD_SECRET reference, GODVIEW_ENV, and OTEL_EXPORTER_OTLP_ENDPOINT (/v1/traces)
    assert "DB_PASSWORD_SECRET" in main_tf
    assert "cymbal-pg-app-password" in main_tf
    assert 'variable "sovereign_service_url"' in variables_tf
    assert "GODVIEW_ENV" in main_tf
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" in main_tf
    assert "/v1/traces" in main_tf

    # Service sets node_port 30081 and 30082 in kind/NodePort mode
    assert "30081" in main_tf
    assert "30082" in main_tf
    assert "node_port" in main_tf

    # NetworkPolicies for utility pods (Ingress 8080 from subnet + health checks; Egress 8085, 5432, 443, and GKE metadata 988/80)
    assert 'resource "kubernetes_network_policy_v1" "utility"' in main_tf
    assert "130.211.0.0/22" in main_tf
    assert "35.191.0.0/16" in main_tf
    assert "8085" in main_tf
    assert "5432" in main_tf
    assert "443" in main_tf
    assert "169.254.169.252/32" in main_tf
    assert "988" in main_tf
    assert "169.254.169.254/32" in main_tf

    # images.auto.tfvars defines both GKE utilities
    assert "kyc-model-armor-svc" in tfvars
    assert "underwriting-los-svc" in tfvars


def test_workloads_kind_local_backend_override(tmp_path: Path) -> None:
    tf = _find_terraform()
    tpl_path = LOCAL_ENV_DIR / "kind" / "backend_override.tf.tpl"
    assert tpl_path.is_file(), "Missing local-env/kind/backend_override.tf.tpl for kind local backend override"

    work_copy = tmp_path / "workloads"
    shutil.copytree(WORKLOADS_DIR, work_copy, ignore=shutil.ignore_patterns(".terraform*", "*.tfstate*"))
    # Link .terraform plugins from WORKLOADS_DIR if already initialized so we don't need network access
    if (WORKLOADS_DIR / ".terraform").is_dir():
        shutil.copytree(WORKLOADS_DIR / ".terraform", work_copy / ".terraform")
    if (WORKLOADS_DIR / ".terraform.lock.hcl").is_file():
        shutil.copy2(WORKLOADS_DIR / ".terraform.lock.hcl", work_copy / ".terraform.lock.hcl")

    shutil.copy2(tpl_path, work_copy / "backend_override.tf")

    init_res = subprocess.run(
        [tf, f"-chdir={work_copy}", "init", "-reconfigure", "-input=false", "-get=false"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert init_res.returncode == 0, f"kind local backend init failed:\n{init_res.stdout}\n{init_res.stderr}"
    assert "Successfully configured the backend \"local\"" in init_res.stdout


def test_workloads_terraform_fmt_and_validate() -> None:
    tf = _find_terraform()
    fmt_res = subprocess.run(
        [tf, f"-chdir={WORKLOADS_DIR}", "fmt", "-check", "-recursive"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert fmt_res.returncode == 0, f"terraform fmt -check failed:\n{fmt_res.stdout}\n{fmt_res.stderr}"

    init_res = subprocess.run(
        [tf, f"-chdir={WORKLOADS_DIR}", "init", "-backend=false", "-input=false"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert init_res.returncode == 0, f"terraform init -backend=false failed:\n{init_res.stdout}\n{init_res.stderr}"

    val_res = subprocess.run(
        [tf, f"-chdir={WORKLOADS_DIR}", "validate"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert val_res.returncode == 0, f"terraform validate failed:\n{val_res.stdout}\n{val_res.stderr}"


# ---------------------------------------------------------------------------
# 3. GitHub Actions Workflows (.github/workflows/)
# ---------------------------------------------------------------------------


def test_github_workflows_wif_only_and_structure() -> None:
    expected_workflows = [
        "terraform-platform.yml",
        "terraform-workloads.yml",
        "services-ci-cd.yml",
    ]
    for wf_name in expected_workflows:
        wf_path = WORKFLOWS_DIR / wf_name
        assert wf_path.is_file(), f"Missing workflow file: {wf_path}"
        raw = wf_path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(raw)
        assert isinstance(parsed, dict), f"Workflow {wf_name} is not a valid YAML mapping"

        # WIF only: google-github-actions/auth@v2, id-token: write, github-pool / github-provider
        assert "google-github-actions/auth@v2" in raw, f"{wf_name} must use google-github-actions/auth@v2"
        assert "workload_identity_provider" in raw, f"{wf_name} must use workload_identity_provider"
        assert "github-pool" in raw, f"{wf_name} must reference github-pool"
        assert "github-provider" in raw, f"{wf_name} must reference github-provider"
        assert "godview-deploy-sa" in raw, f"{wf_name} must reference godview-deploy-sa"

        # Negative direction: no JSON service account keys anywhere
        for forbidden in ("credentials_json", "service_account_key", "GCP_SA_KEY", "PRIVATE_KEY"):
            assert forbidden not in raw, f"{wf_name} contains forbidden static key token: {forbidden}"

        permissions = parsed.get("permissions", {})
        assert permissions.get("id-token") == "write", f"{wf_name} must grant id-token: write"

        # Finding 6: fallback must use github.repository, not hardcoded placeholder-org/god-view
        assert "placeholder-org/god-view" not in raw, f"{wf_name} must use github.repository fallback, not placeholder-org/god-view"
        assert "github.repository" in raw, f"{wf_name} must reference github.repository"

        # Finding 9: no direct ${{ inputs.* }} interpolation inside run: shell scripts
        for job_name, job_def in (parsed.get("jobs") or {}).items():
            for step in job_def.get("steps") or []:
                run_script = step.get("run") or ""
                assert "${{ inputs." not in run_script, (
                    f"{wf_name} job {job_name} interpolates inputs directly into run: (use env: instead)"
                )

    # Check targeted reconcile workflow_dispatch in platform & workloads workflows
    for tf_wf in ("terraform-platform.yml", "terraform-workloads.yml"):
        raw = (WORKFLOWS_DIR / tf_wf).read_text(encoding="utf-8")
        parsed = yaml.safe_load(raw)
        # In YAML, 'on' can be parsed as boolean True by PyYAML 1.1 loader if unquoted, handle both 'on' and True
        triggers = parsed.get("on") or parsed.get(True) or {}
        assert "workflow_dispatch" in triggers, f"{tf_wf} must support workflow_dispatch"
        inputs = triggers["workflow_dispatch"].get("inputs", {})
        assert "target" in inputs, f"{tf_wf} workflow_dispatch must accept 'target'"
        assert "hibernate" in inputs, f"{tf_wf} workflow_dispatch must accept 'hibernate'"
        assert "reason" in inputs, f"{tf_wf} workflow_dispatch must accept 'reason'"
        assert "-target=" in raw, f"{tf_wf} must pass -target= on targeted reconcile"

    # Check kind parity check in terraform-workloads.yml uses backend_override.tf (Finding 2)
    workloads_wf_raw = (WORKFLOWS_DIR / "terraform-workloads.yml").read_text(encoding="utf-8")
    assert "kind" in workloads_wf_raw, "terraform-workloads.yml must include kind parity check on PRs"
    assert "backend_override.tf" in workloads_wf_raw, "terraform-workloads.yml kind job must configure local backend_override.tf"
    assert "workflow_run" in workloads_wf_raw, "terraform-workloads.yml must trigger on workflow_run after platform"

    # Check services-ci-cd.yml installs deps, resolves sha256 digests, pins @sha256:, and dispatches terraform workflows (Findings 4, 5, 7)
    ci_raw = (WORKFLOWS_DIR / "services-ci-cd.yml").read_text(encoding="utf-8")
    ci_parsed = yaml.safe_load(ci_raw)
    assert ci_parsed.get("permissions", {}).get("actions") == "write", "services-ci-cd.yml must grant actions: write"
    assert "us-central1-docker.pkg.dev/general-ak/god-view-containers" in ci_raw
    assert "infra/terraform/platform/images.auto.tfvars" in ci_raw
    assert "infra/terraform/workloads/images.auto.tfvars" in ci_raw
    assert "image_summary.digest" in ci_raw, "services-ci-cd.yml must resolve sha256 digest from Artifact Registry"
    assert '= "$(resolve_digest_ref' not in ci_raw, (
        "services-ci-cd.yml must not call $(resolve_digest_ref ...) inside heredoc (assign to variables first)"
    )
    assert 'KYC_REF="$(resolve_digest_ref' in ci_raw
    assert "@sha256:" in ci_raw, "services-ci-cd.yml must validate @sha256: digests in images.auto.tfvars"
    assert "gh workflow run terraform-platform.yml" in ci_raw
    assert "gh workflow run terraform-workloads.yml" in ci_raw
    assert "uv pip install" in ci_raw, "services-ci-cd.yml must install Python dependencies before running tests"
    assert "npm ci" in ci_raw, "services-ci-cd.yml must install frontend dependencies before running tests"


# ---------------------------------------------------------------------------
# 4. Makefile targets (TDD §7)
# ---------------------------------------------------------------------------


def test_makefile_targets_match_tdd_section_7() -> None:
    assert MAKEFILE_PATH.is_file(), "Makefile must exist at repo root"
    content = MAKEFILE_PATH.read_text(encoding="utf-8")

    required_targets = [
        "test-unit:",
        "test-contract:",
        "local-up:",
        "test-local-e2e:",
        "drills:",
        "lint-brands:",
        "lint-neutrality:",
    ]
    for target in required_targets:
        assert target in content, f"Makefile missing required target {target}"

    # Verify marker expressions, local backend override for kind, and loud failure on missing node_modules
    assert "not contract and not local_e2e and not gcp and not drill" in content
    assert "-m contract" in content
    assert "-m local_e2e" in content
    assert "-m drill" in content
    assert "local-env/kind/cluster.yaml" in content
    assert "backend_override.tf" in content
    assert "kind load docker-image" in content
    assert "node_modules is missing" in content
    assert "shared/tools" in content


# ---------------------------------------------------------------------------
# 5. docker-compose.yml & local-env/ static schema validation
# ---------------------------------------------------------------------------


def test_docker_compose_schema_and_services() -> None:
    assert COMPOSE_PATH.is_file(), "docker-compose.yml must exist at repo root"
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    assert isinstance(compose, dict), "docker-compose.yml must be a YAML mapping"
    services: dict[str, Any] = compose.get("services", {})
    assert isinstance(services, dict) and services, "docker-compose.yml must define services"

    expected_services = {
        "postgres",
        "loan-orchestrator-api",
        "kyc-model-armor-svc",
        "underwriting-los-svc",
        "commercial-fleet-credit-svc",
        "motor-insurance-svc",
        "core-banking-cibil-vm",
        "ai-customer-simulator",
        "customer-loan-portal-web",
        "internal-bank-los-web",
        "god-view-sovereign",
        "god-view-sre-web",
        "prometheus",
        "chat-mock",
    }
    missing = expected_services - set(services.keys())
    assert not missing, f"docker-compose.yml missing required services: {sorted(missing)}"

    # Validate Postgres 16 + wal_level=logical + pg_stat_statements
    pg = services["postgres"]
    assert "postgres:16" in pg.get("image", ""), "postgres service must use postgres:16 image"
    pg_cmd = " ".join(pg.get("command", [])) if isinstance(pg.get("command"), list) else str(pg.get("command", ""))
    assert "wal_level=logical" in pg_cmd, "postgres must enable wal_level=logical"
    assert "pg_stat_statements" in pg_cmd, "postgres must preload pg_stat_statements"

    # Validate core-banking-cibil-vm exposes 8085
    cbs_ports = [str(p) for p in services["core-banking-cibil-vm"].get("ports", [])]
    assert any("8085" in p for p in cbs_ports), "core-banking-cibil-vm must expose port 8085"

    # Validate GODVIEW_ENV=${GODVIEW_ENV:-local} on all 6 tenant services + simulator + sovereign,
    # and OTEL_EXPORTER_OTLP_ENDPOINT=http://god-view-sovereign:8080/v1/traces on all 6 tenant services + simulator
    otel_tracked_services = (
        "core-banking-cibil-vm",
        "kyc-model-armor-svc",
        "underwriting-los-svc",
        "commercial-fleet-credit-svc",
        "motor-insurance-svc",
        "loan-orchestrator-api",
        "ai-customer-simulator",
    )
    for svc_name in (*otel_tracked_services, "god-view-sovereign"):
        svc_env = services[svc_name].get("environment", {})
        assert isinstance(svc_env, dict), f"{svc_name} environment must be a mapping"
        assert svc_env.get("GODVIEW_ENV") == "${GODVIEW_ENV:-local}", (
            f"{svc_name} must set GODVIEW_ENV: ${{GODVIEW_ENV:-local}}"
        )
    for svc_name in otel_tracked_services:
        svc_env = services[svc_name].get("environment", {})
        assert svc_env.get("OTEL_EXPORTER_OTLP_ENDPOINT") == "http://god-view-sovereign:8080/v1/traces", (
            f"{svc_name} must set OTEL_EXPORTER_OTLP_ENDPOINT: http://god-view-sovereign:8080/v1/traces"
        )

    # Validate Decision A9 budget env vars on god-view-sovereign for GODVIEW_ENV=local
    sovereign_env = services["god-view-sovereign"].get("environment", {})
    expected_budgets = {
        "GODVIEW_COURT_DAILY_CEILING_USD": "6.00",
        "GODVIEW_BUDGET_APPLICATION_USD": "0.75",
        "GODVIEW_BUDGET_BUSINESS_LINE_USD": "0.75",
        "GODVIEW_BUDGET_CHANGE_USD": "0.75",
        "GODVIEW_BUDGET_DATA_USD": "0.50",
        "GODVIEW_BUDGET_PLATFORM_USD": "0.50",
        "GODVIEW_BUDGET_SOVEREIGN_USD": "1.50",
    }
    for key, val in expected_budgets.items():
        assert str(sovereign_env.get(key, "")) == val or f":-{val}" in str(sovereign_env.get(key, "")), (
            f"god-view-sovereign missing required budget env var {key}={val} in docker-compose.yml"
        )

    # Static schema validation on every service entry
    allowed_service_keys = {
        "image",
        "build",
        "container_name",
        "command",
        "entrypoint",
        "environment",
        "env_file",
        "ports",
        "expose",
        "volumes",
        "depends_on",
        "networks",
        "healthcheck",
        "labels",
        "restart",
        "working_dir",
    }
    for svc_name, svc_def in services.items():
        assert isinstance(svc_def, dict), f"Service {svc_name} definition must be a dict"
        unknown_keys = set(svc_def.keys()) - allowed_service_keys
        assert not unknown_keys, f"Service {svc_name} has invalid compose keys: {unknown_keys}"
        assert "image" in svc_def or "build" in svc_def, f"Service {svc_name} must specify image or build"
        labels = svc_def.get("labels", {})
        if isinstance(labels, dict):
            assert labels.get("godview_managed") == "true", f"Service {svc_name} must carry godview_managed=true label"


def test_kind_cluster_config_schema() -> None:
    kind_path = LOCAL_ENV_DIR / "kind" / "cluster.yaml"
    assert kind_path.is_file(), "local-env/kind/cluster.yaml must exist"
    cfg = yaml.safe_load(kind_path.read_text(encoding="utf-8"))
    assert cfg.get("kind") == "Cluster"
    assert cfg.get("apiVersion") == "kind.x-k8s.io/v1alpha4"
    assert cfg.get("name") == "godview"
    nodes = cfg.get("nodes", [])
    assert isinstance(nodes, list) and len(nodes) >= 1
    roles = [n.get("role") for n in nodes]
    assert "control-plane" in roles


def test_local_env_seed_sql_and_readonly_role() -> None:
    roles_sql = LOCAL_ENV_DIR / "seed" / "01_extensions_and_roles.sql"
    schema_sql = LOCAL_ENV_DIR / "seed" / "02_schema_and_seed.sql"
    seed_sh = LOCAL_ENV_DIR / "seed" / "seed.sh"
    env_example = LOCAL_ENV_DIR / ".env.example"

    assert roles_sql.is_file(), "Missing local-env/seed/01_extensions_and_roles.sql"
    assert schema_sql.is_file(), "Missing local-env/seed/02_schema_and_seed.sql"
    assert seed_sh.is_file(), "Missing local-env/seed/seed.sh"
    assert env_example.is_file(), "Missing local-env/.env.example"

    roles_text = roles_sql.read_text(encoding="utf-8")
    assert "CREATE EXTENSION IF NOT EXISTS pg_stat_statements" in roles_text
    # Decision A12: godview_readonly with default_transaction_read_only = on and statement_timeout = '5s'
    assert "godview_readonly" in roles_text
    assert "default_transaction_read_only = on" in roles_text
    assert "statement_timeout = '5s'" in roles_text
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA public TO godview_readonly" in roles_text

    schema_text = schema_sql.read_text(encoding="utf-8")
    expected_tables = [
        "vehicles",
        "customers",
        "kyc_documents",
        "loan_applications",
        "underwriting_audits",
        "commercial_credit_lines",
        "insurance_policies",
        "disbursements",
        "chaos_fault_states",
    ]
    for tbl in expected_tables:
        assert f"CREATE TABLE IF NOT EXISTS {tbl}" in schema_text, f"Seed SQL missing table {tbl}"

    # Invariant I-04 negative check: removed v3 telemetry tables must NEVER appear
    assert "service_metric_rollups" not in schema_text
    assert "otel_trace_spans" not in schema_text


def test_local_env_prometheus_and_git_init(tmp_path: Path) -> None:
    prom_path = LOCAL_ENV_DIR / "prometheus" / "prometheus.yml"
    git_sh = LOCAL_ENV_DIR / "git" / "init-local-git.sh"

    assert prom_path.is_file(), "Missing local-env/prometheus/prometheus.yml"
    assert git_sh.is_file(), "Missing local-env/git/init-local-git.sh"

    prom = yaml.safe_load(prom_path.read_text(encoding="utf-8"))
    assert prom.get("global", {}).get("scrape_interval") == "10s"
    scrape_configs = prom.get("scrape_configs", [])
    job_names = {job.get("job_name") for job in scrape_configs}
    for required_job in (
        "loan-orchestrator-api",
        "kyc-model-armor-svc",
        "underwriting-los-svc",
        "commercial-fleet-credit-svc",
        "motor-insurance-svc",
        "core-banking-cibil-vm",
        "god-view-sovereign",
    ):
        assert required_job in job_names, f"prometheus.yml missing scrape job for {required_job}"

    # Execute init-local-git.sh against tmp_path and verify bare repo + baseline commit
    env = {**os.environ, "LOCAL_GIT_BASE_DIR": str(tmp_path)}
    res = subprocess.run(["bash", str(git_sh)], env=env, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"init-local-git.sh failed:\n{res.stdout}\n{res.stderr}"
    bare_repo = tmp_path / "god-view.git"
    assert (bare_repo / "HEAD").is_file(), "Bare Git repository was not initialized"
    log_res = subprocess.run(
        ["git", "--git-dir", str(bare_repo), "log", "--oneline", "main"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert log_res.returncode == 0 and "rules_engine" in log_res.stdout


def test_chat_mock_receiver_handles_cards_and_Rejects_invalid_payloads() -> None:
    chat_app_path = LOCAL_ENV_DIR / "chat-mock" / "app.py"
    assert chat_app_path.is_file(), "Missing local-env/chat-mock/app.py"

    spec = importlib.util.spec_from_file_location("chat_mock_app", chat_app_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    store = module.ChatCardStore()
    # Positive direction: valid card recorded
    recorded = store.receive_card(
        space="god-view",
        payload={
            "cardsV2": [{"cardId": "apr-inc-1", "card": {"header": {"title": "Approval Required"}}}],
            "approval_id": "apr-inc-1",
        },
    )
    assert recorded["approval_id"] == "apr-inc-1"
    assert len(store.list_cards()) == 1

    # Negative direction: empty/invalid payload raises ValueError loudly
    with pytest.raises(ValueError, match="cardsV2|text"):
        store.receive_card(space="god-view", payload={})


# ---------------------------------------------------------------------------
# 6. L4 GCP E2E & L5 Drill Harness Verification (both directions)
# ---------------------------------------------------------------------------


def test_gcp_e2e_and_drills_modules_and_evaluators(monkeypatch: pytest.MonkeyPatch) -> None:
    from infra.tests import test_drills, test_gcp_e2e

    # Verify both modules expose the expected markers and evaluator helpers
    assert hasattr(test_gcp_e2e, "assert_uc01_perception_response")
    assert hasattr(test_gcp_e2e, "assert_uc24_journey_slo")
    assert hasattr(test_drills, "DRILL_SPECIFICATIONS")
    assert len(test_drills.DRILL_SPECIFICATIONS) == 8

    # Verify resolve_gcp_endpoints reads sovereign_service_url and api_gateway_hostname from outputs
    monkeypatch.delenv("GODVIEW_API_URL", raising=False)
    monkeypatch.delenv("GODVIEW_SOVEREIGN_URL", raising=False)
    endpoints_from_outputs = test_gcp_e2e.resolve_gcp_endpoints(
        config=None,
        outputs={
            "api_gateway_hostname": "god-view-loan-api-gw-9t9witd8.uc.gateway.dev",
            "sovereign_service_url": "https://god-view-sovereign-mcj3w7ujpq-uc.a.run.app",
        },
    )
    assert endpoints_from_outputs.api_url == "https://god-view-loan-api-gw-9t9witd8.uc.gateway.dev"
    assert endpoints_from_outputs.sovereign_url == "https://god-view-sovereign-mcj3w7ujpq-uc.a.run.app"

    # Negative/fallback direction when outputs are empty
    endpoints_fallback = test_gcp_e2e.resolve_gcp_endpoints(config=None, outputs={})
    assert endpoints_fallback.api_url == "https://api.cymbal.ak-demos.com"
    assert endpoints_fallback.sovereign_url == "https://godview.ak-demos.com/api"

    # Positive direction for UC-01 perception evaluator (both dict and list[dict] senses)
    valid_perception = {
        "senses": {"gcp": {"state": "available", "evidence": "ADC active"}},
        "discovered_services": [
            {"name": "loan-orchestrator-api", "classification": "journey_linked"},
            {"name": "kyc-model-armor-svc", "classification": "journey_linked"},
            {"name": "underwriting-los-svc", "classification": "journey_linked"},
            {"name": "commercial-fleet-credit-svc", "classification": "journey_linked"},
            {"name": "motor-insurance-svc", "classification": "journey_linked"},
            {"name": "core-banking-cibil-vm", "classification": "journey_linked"},
            {"name": "customer-loan-portal-web", "classification": "journey_linked"},
            {"name": "internal-bank-los-web", "classification": "journey_linked"},
            {"name": "god-view-sre-web", "classification": "other_read_only"},
        ],
        "elapsed_seconds": 12.4,
    }
    test_gcp_e2e.assert_uc01_perception_response(valid_perception)

    live_perception_list_senses = {
        "senses": [
            {"adapter_family": "gcp", "availability": "available", "evidence": "Sense adapter active"},
            {"adapter_family": "k8s", "availability": "available", "evidence": "Sense adapter active"},
        ],
        "discovered_services": [
            {"name": "svc-alpha-01", "classification": "journey_linked"},
        ],
        "elapsed_seconds": 0.8,
    }
    test_gcp_e2e.assert_uc01_perception_response(live_perception_list_senses)

    # Negative direction: missing senses (empty dict or empty list), missing service, or slow perception (>30s) fails loudly
    with pytest.raises(AssertionError, match="sense availability map"):
        test_gcp_e2e.assert_uc01_perception_response({"senses": {}, "discovered_services": [], "elapsed_seconds": 5.0})

    with pytest.raises(AssertionError, match="sense availability map"):
        test_gcp_e2e.assert_uc01_perception_response({"senses": [], "discovered_services": [], "elapsed_seconds": 5.0})

    with pytest.raises(AssertionError, match="Missing expected discovered services"):
        test_gcp_e2e.assert_uc01_perception_response(
            {
                "senses": {"gcp": {"state": "available", "evidence": "ADC active"}},
                "discovered_services": [],
                "elapsed_seconds": 5.0,
            }
        )

    with pytest.raises(AssertionError, match="exceeded 30s SLO"):
        test_gcp_e2e.assert_uc01_perception_response({**valid_perception, "elapsed_seconds": 45.0})

    # Positive & negative directions for UC-24 latency SLO (< 3.5s excluding image generation)
    import hashlib
    import inspect

    uc24_src = inspect.getsource(test_gcp_e2e.test_uc24_human_loan_journey_and_sse_stream)
    assert '"ABCDE0006F"' in uc24_src
    pan_digest = hashlib.sha256(b"ABCDE0006F:").digest()
    assert 300 + (int.from_bytes(pan_digest[:8], "big") % 600) == 798

    test_gcp_e2e.assert_uc24_journey_slo(
        {"loan_id": "ln-1", "journey_id": "jrn-1", "status": "DISBURSED", "is_synthetic": False},
        elapsed_ms=2100.0,
    )
    with pytest.raises(AssertionError, match="P95 target 3500ms"):
        test_gcp_e2e.assert_uc24_journey_slo(
            {"loan_id": "ln-1", "journey_id": "jrn-1", "status": "DISBURSED", "is_synthetic": False},
            elapsed_ms=4200.0,
        )

    # Positive & negative directions for L5 Drill Outcome Evaluator
    drill_spec = test_drills.DRILL_SPECIFICATIONS["foir_threshold_regression"]
    test_drills.assert_drill_outcome(
        drill_spec,
        incident={
            "incident_id": "inc-20261016-0001",
            "root_cause": "Regressed FOIR threshold 60 -> 40 in commit abc1234",
            "evidence_pack": {"golden_signals": {"status": "green"}},
            "mttd_seconds": 10.0,
            "mtti_seconds": 42.0,
        },
    )
    with pytest.raises(AssertionError, match="MTTD"):
        test_drills.assert_drill_outcome(
            drill_spec,
            incident={
                "incident_id": "inc-20261016-0001",
                "root_cause": "Regressed FOIR threshold",
                "evidence_pack": {"golden_signals": {"status": "green"}},
                "mttd_seconds": 25.0,
                "mtti_seconds": 42.0,
            },
        )

