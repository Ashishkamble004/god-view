import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_DIR = REPO_ROOT / "infra" / "terraform" / "platform"
OPENAPI_SPEC_TPL = REPO_ROOT / "infra" / "api-gateway" / "openapi-spec.yaml.tftpl"


def _tf_env(tmp_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    local_bin = str(Path.home() / ".local" / "bin")
    if local_bin not in env.get("PATH", ""):
        env["PATH"] = f"{local_bin}:{env.get('PATH', '')}"
    cache_dir = Path.home() / "tmp" / "tf-plugin-cache"
    if cache_dir.is_dir():
        env["TF_PLUGIN_CACHE_DIR"] = str(cache_dir)
    env["TF_DATA_DIR"] = str(tmp_dir / ".terraform")
    env["TF_IN_AUTOMATION"] = "1"
    return env


def _collect_resources(module_obj: dict) -> list[dict]:
    resources = list(module_obj.get("resources", []))
    for child in module_obj.get("child_modules", []):
        resources.extend(_collect_resources(child))
    return resources


def _run_plan(tmp_dir: Path, extra_vars: dict[str, str] | None = None) -> dict:
    assert PLATFORM_DIR.is_dir(), f"Missing platform directory: {PLATFORM_DIR}"
    work_dir = tmp_dir / "platform_work"
    shutil.copytree(
        PLATFORM_DIR,
        work_dir,
        ignore=shutil.ignore_patterns(".terraform", "*.tfstate*", "tfplan*"),
    )
    backend_file = work_dir / "backend.tf"
    if backend_file.exists():
        content = backend_file.read_text()
        assert 'bucket = "general-ak-god-view-tfstate"' in content
        assert 'prefix = "platform"' in content
        stripped = re.sub(r'backend\s+"gcs"\s*\{[^}]*\}', "", content)
        backend_file.write_text(stripped)

    env = _tf_env(tmp_dir)
    subprocess.run(
        ["terraform", f"-chdir={work_dir}", "init", "-backend=false", "-input=false"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["terraform", f"-chdir={work_dir}", "validate"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    plan_file = tmp_dir / "tfplan"
    merged_vars = {"lookup_dns_zone": "false"}
    if extra_vars:
        merged_vars.update(extra_vars)
    cmd = [
        "terraform",
        f"-chdir={work_dir}",
        "plan",
        "-input=false",
        "-refresh=false",
        f"-out={plan_file}",
    ]
    for k, v in merged_vars.items():
        cmd.extend(["-var", f"{k}={v}"])
    plan_res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert plan_res.returncode == 0, f"terraform plan failed:\n{plan_res.stdout}\n{plan_res.stderr}"
    res = subprocess.run(
        ["terraform", f"-chdir={work_dir}", "show", "-json", str(plan_file)],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(res.stdout)


@pytest.fixture(scope="module")
def plan_json(tmp_path_factory: pytest.TempPathFactory) -> dict:
    tmp_dir = tmp_path_factory.mktemp("tf_platform_default")
    return _run_plan(tmp_dir)


@pytest.fixture(scope="module")
def resources(plan_json: dict) -> list[dict]:
    root = plan_json.get("planned_values", {}).get("root_module", {})
    return _collect_resources(root)


def test_fmt_and_backend_config():
    env = _tf_env(Path("/tmp"))
    res = subprocess.run(
        ["terraform", f"-chdir={PLATFORM_DIR}", "fmt", "-check", "-recursive"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"terraform fmt -check failed:\n{res.stdout}\n{res.stderr}"
    backend_text = (PLATFORM_DIR / "backend.tf").read_text()
    assert 'bucket = "general-ak-god-view-tfstate"' in backend_text
    assert 'prefix = "platform"' in backend_text


def test_stage1_apis_and_identities(resources: list[dict]):
    api_services = {
        r["values"]["service"]: r["values"]
        for r in resources
        if r["type"] == "google_project_service"
    }
    required_apis = {
        "run.googleapis.com",
        "container.googleapis.com",
        "compute.googleapis.com",
        "sqladmin.googleapis.com",
        "firestore.googleapis.com",
        "bigquery.googleapis.com",
        "pubsub.googleapis.com",
        "cloudscheduler.googleapis.com",
        "apigateway.googleapis.com",
        "servicecontrol.googleapis.com",
        "servicemanagement.googleapis.com",
        "aiplatform.googleapis.com",
        "modelarmor.googleapis.com",
        "cloudasset.googleapis.com",
        "monitoring.googleapis.com",
        "cloudtrace.googleapis.com",
        "logging.googleapis.com",
        "secretmanager.googleapis.com",
        "iap.googleapis.com",
        "iam.googleapis.com",
        "iamcredentials.googleapis.com",
        "sts.googleapis.com",
        "artifactregistry.googleapis.com",
        "servicenetworking.googleapis.com",
        "dns.googleapis.com",
        "billingbudgets.googleapis.com",
        "cloudresourcemanager.googleapis.com",
        "datastream.googleapis.com",
        "certificatemanager.googleapis.com",
        "cloudbuild.googleapis.com",
    }
    missing = required_apis - set(api_services.keys())
    assert not missing, f"Missing google_project_service APIs: {missing}"
    for svc, vals in api_services.items():
        assert vals.get("disable_on_destroy") is False, f"{svc} must have disable_on_destroy=false"

    sa_ids = {r["values"]["account_id"] for r in resources if r["type"] == "google_service_account"}
    assert sa_ids == {
        "godview-sense-sa",
        "godview-act-sa",
        "godview-runtime-sa",
        "godview-apigw-sa",
        "godview-deploy-sa",
    }
    sa_keys = [r for r in resources if r["type"] == "google_service_account_key"]
    assert len(sa_keys) == 0, "Org policy forbids service account keys"

    runtime_roles = {
        r["values"]["role"]
        for r in resources
        if r["type"] == "google_project_iam_member"
        and (
            "godview-runtime-sa" in r.get("address", "") or "runtime_roles" in r.get("address", "")
        )
    }
    assert "roles/aiplatform.user" in runtime_roles

    sa_iam = [
        r
        for r in resources
        if r["type"] == "google_service_account_iam_member"
        and r["values"]["role"] == "roles/iam.serviceAccountUser"
    ]
    assert len(sa_iam) >= 1

    pools = [r for r in resources if r["type"] == "google_iam_workload_identity_pool"]
    assert len(pools) == 1
    assert pools[0]["values"]["workload_identity_pool_id"] == "github-pool"

    providers = [r for r in resources if r["type"] == "google_iam_workload_identity_pool_provider"]
    assert len(providers) == 1
    assert providers[0]["values"]["workload_identity_pool_provider_id"] == "github-provider"
    assert (
        providers[0]["values"]["oidc"][0]["issuer_uri"]
        == "https://token.actions.githubusercontent.com"
    )

    ar_repos = [r for r in resources if r["type"] == "google_artifact_registry_repository"]
    assert len(ar_repos) == 1
    assert ar_repos[0]["values"]["repository_id"] == "god-view-containers"
    assert ar_repos[0]["values"]["format"] == "DOCKER"
    assert ar_repos[0]["values"]["location"] == "us-central1"


def test_stage1_network_vpc_psa_nat_firewalls(resources: list[dict]):
    vpcs = [r for r in resources if r["type"] == "google_compute_network"]
    assert len(vpcs) == 1
    assert vpcs[0]["values"]["name"] == "god-view-vpc"
    assert vpcs[0]["values"]["auto_create_subnetworks"] is False

    subnets = [r for r in resources if r["type"] == "google_compute_subnetwork"]
    assert len(subnets) == 1
    assert subnets[0]["values"]["name"] == "god-view-subnet"
    assert subnets[0]["values"]["ip_cidr_range"] == "10.10.0.0/20"
    assert subnets[0]["values"]["private_ip_google_access"] is True

    global_addrs = {
        r["values"]["name"]: r["values"]
        for r in resources
        if r["type"] == "google_compute_global_address"
    }
    assert "god-view-psa-range" in global_addrs
    assert global_addrs["god-view-psa-range"]["purpose"] == "VPC_PEERING"
    assert global_addrs["god-view-psa-range"]["prefix_length"] == 16

    nats = [r for r in resources if r["type"] == "google_compute_router_nat"]
    assert len(nats) == 1
    assert nats[0]["values"]["name"] == "god-view-nat"

    ilb_addrs = {
        r["values"]["name"]: r["values"] for r in resources if r["type"] == "google_compute_address"
    }
    assert {"god-view-kyc-ilb-ip", "god-view-los-ilb-ip"}.issubset(set(ilb_addrs.keys()))
    for name in ("god-view-kyc-ilb-ip", "god-view-los-ilb-ip"):
        assert ilb_addrs[name]["address_type"] == "INTERNAL"

    fws = {
        r["values"]["name"]: r["values"]
        for r in resources
        if r["type"] == "google_compute_firewall"
    }
    assert {
        "gv-allow-subnet-to-gke-ilb",
        "gv-allow-subnet-to-cbs",
        "gv-allow-egress-cloudsql",
        "gv-allow-health-checks",
        "gv-allow-iap-ssh",
    }.issubset(set(fws.keys()))


def test_stage1_data_stores_buckets_secrets_sql_firestore_bq(resources: list[dict]):
    buckets = {
        r["values"]["name"]: r["values"] for r in resources if r["type"] == "google_storage_bucket"
    }
    expected_buckets = {
        "general-ak-god-view-kyc-vault",
        "general-ak-god-view-cdn-assets",
        "general-ak-god-view-agent-skills",
        "general-ak-god-view-tfstate",
        "general-ak-god-view-agent-staging",
    }
    assert expected_buckets == set(buckets.keys())
    for _bname, bvals in buckets.items():
        assert bvals["uniform_bucket_level_access"] is True
    assert buckets["general-ak-god-view-tfstate"]["versioning"][0]["enabled"] is True

    secrets = {
        r["values"]["secret_id"] for r in resources if r["type"] == "google_secret_manager_secret"
    }
    assert secrets == {
        "cymbal-pg-app-password",
        "godview-github-app-key",
        "godview-github-webhook-secret",
    }
    rnd = [r for r in resources if r["type"] == "random_password"]
    assert len(rnd) >= 1

    fs_dbs = [r for r in resources if r["type"] == "google_firestore_database"]
    assert len(fs_dbs) == 1
    assert fs_dbs[0]["values"]["name"] == "god-view-memory-db"
    assert fs_dbs[0]["values"]["type"] == "FIRESTORE_NATIVE"
    assert fs_dbs[0]["values"]["location_id"] == "us-central1"

    fs_indexes = [r for r in resources if r["type"] == "google_firestore_index"]
    assert len(fs_indexes) == 4
    indexed_pairs = set()
    for idx in fs_indexes:
        v = idx["values"]
        assert v["database"] == "god-view-memory-db"
        assert v["query_scope"] == "COLLECTION"
        vec_fields = [f for f in v["fields"] if f.get("vector_config")]
        assert len(vec_fields) == 1
        vf = vec_fields[0]
        assert vf["vector_config"][0]["dimension"] == 768
        indexed_pairs.add((v["collection"], vf["field_path"]))
    assert indexed_pairs == {
        ("discovered_services", "purpose_embedding"),
        ("invariants", "statement_embedding"),
        ("episodic_incidents", "symptom_embedding"),
        ("procedural_skills", "trigger_embedding"),
    }

    bq_datasets = [r for r in resources if r["type"] == "google_bigquery_dataset"]
    assert len(bq_datasets) == 1
    assert bq_datasets[0]["values"]["dataset_id"] == "godview_ledger"
    assert bq_datasets[0]["values"]["location"] == "us-central1"

    bq_tables = {
        r["values"]["table_id"]: r["values"]
        for r in resources
        if r["type"] == "google_bigquery_table"
    }
    assert set(bq_tables.keys()) == {
        "events",
        "state_transitions",
        "journeys",
        "kpi_rollups",
    }
    for tid, tvals in bq_tables.items():
        assert tvals["time_partitioning"][0]["type"] == "DAY"
        assert len(tvals["clustering"]) > 0
        schema_cols = json.loads(tvals["schema"])
        assert len(schema_cols) >= 10, f"Table {tid} schema too short"

    sql_instances = [r for r in resources if r["type"] == "google_sql_database_instance"]
    assert len(sql_instances) == 1
    sql_vals = sql_instances[0]["values"]
    assert sql_vals["name"] == "god-view-pg"
    assert sql_vals["database_version"] == "POSTGRES_16"
    assert sql_vals["deletion_protection"] is False
    settings = sql_vals["settings"][0]
    assert settings["tier"] == "db-g1-small"
    assert settings["edition"] == "ENTERPRISE"
    assert settings["availability_type"] == "ZONAL"
    assert settings["ip_configuration"][0]["ipv4_enabled"] is False
    assert settings["backup_configuration"][0]["enabled"] is True
    assert settings["backup_configuration"][0]["point_in_time_recovery_enabled"] is False
    flags = {f["name"]: f["value"] for f in settings.get("database_flags", [])}
    assert flags.get("cloudsql.logical_decoding") == "on"
    assert flags.get("cloudsql.iam_authentication") == "on"

    sql_dbs = [r for r in resources if r["type"] == "google_sql_database"]
    assert len(sql_dbs) == 1
    assert sql_dbs[0]["values"]["name"] == "godview_lms"

    sql_users = {r["values"]["name"] for r in resources if r["type"] == "google_sql_user"}
    assert sql_users == {"app_user", "godview_readonly"}


def test_stage1_gke_and_pubsub(resources: list[dict]):
    clusters = [r for r in resources if r["type"] == "google_container_cluster"]
    assert len(clusters) == 1
    c = clusters[0]["values"]
    assert c["name"] == "god-view-gke-cluster"
    assert c["enable_autopilot"] is True
    assert c["location"] == "us-central1"
    assert c["private_cluster_config"][0]["enable_private_nodes"] is True
    assert c["private_cluster_config"][0]["master_ipv4_cidr_block"] == "172.16.0.0/28"
    assert c["release_channel"][0]["channel"] == "REGULAR"

    topics = {r["values"]["name"] for r in resources if r["type"] == "google_pubsub_topic"}
    assert {"godview-nervous-system", "godview-nervous-system-dlq"}.issubset(topics)


def test_stage2_cloud_run_vm_apigw_and_edge(resources: list[dict]):
    # 8 Cloud Run v2 services
    cr_services = {
        r["values"]["name"]: r["values"]
        for r in resources
        if r["type"] == "google_cloud_run_v2_service"
    }
    expected_services = {
        "commercial-fleet-credit-svc": "INGRESS_TRAFFIC_INTERNAL_ONLY",
        "motor-insurance-svc": "INGRESS_TRAFFIC_INTERNAL_ONLY",
        "ai-customer-simulator": "INGRESS_TRAFFIC_INTERNAL_ONLY",
        "loan-orchestrator-api": "INGRESS_TRAFFIC_ALL",
        "customer-loan-portal-web": "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER",
        "internal-bank-los-web": "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER",
        "god-view-sovereign": "INGRESS_TRAFFIC_ALL",
        "god-view-sre-web": "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER",
    }
    assert set(cr_services.keys()) == set(expected_services.keys())
    for name, expected_ingress in expected_services.items():
        svc = cr_services[name]
        assert svc["ingress"] == expected_ingress, f"{name} ingress mismatch"
        assert svc["deletion_protection"] is False

    assert cr_services["customer-loan-portal-web"]["invoker_iam_disabled"] is True
    assert cr_services["internal-bank-los-web"].get("invoker_iam_disabled") in (False, None)
    assert cr_services["god-view-sre-web"].get("invoker_iam_disabled") in (False, None)

    sov_tpl = cr_services["god-view-sovereign"]["template"][0]
    assert sov_tpl["scaling"][0]["min_instance_count"] == 1
    assert sov_tpl["scaling"][0]["max_instance_count"] == 1
    sov_res = sov_tpl["containers"][0]["resources"][0]
    assert sov_res["cpu_idle"] is False
    assert sov_res["limits"]["cpu"] == "1"
    assert sov_res["limits"]["memory"] == "1Gi"
    sov_envs = {e["name"]: e.get("value") for e in sov_tpl["containers"][0]["env"]}
    assert sov_envs["GODVIEW_ENV"] == "gcp"
    assert sov_envs["GODVIEW_COURT_DAILY_CEILING_USD"] == "6"
    assert sov_envs["GODVIEW_BUDGET_SOVEREIGN_USD"] == "1.5"

    sim_tpl = cr_services["ai-customer-simulator"]["template"][0]
    assert sim_tpl["scaling"][0]["min_instance_count"] == 0
    assert sim_tpl["scaling"][0]["max_instance_count"] == 1
    assert sim_tpl["vpc_access"][0]["egress"] == "ALL_TRAFFIC"

    orch_tpl = cr_services["loan-orchestrator-api"]["template"][0]
    assert orch_tpl["vpc_access"][0]["egress"] == "ALL_TRAFFIC"

    # No allUsers / allAuthenticatedUsers anywhere
    for r in resources:
        if r["type"].endswith("_iam_member"):
            member = r["values"].get("member", "")
            assert member not in ("allUsers", "allAuthenticatedUsers"), (
                f"Forbidden public IAM on {r['address']}"
            )

    # GCE VM core-banking-cibil-vm: e2-micro, no external IP
    vms = [r for r in resources if r["type"] == "google_compute_instance"]
    assert len(vms) == 1
    vm = vms[0]["values"]
    assert vm["name"] == "core-banking-cibil-vm"
    assert vm["machine_type"] == "e2-micro"
    assert vm["zone"] == "us-central1-a"
    assert not vm["network_interface"][0].get("access_config"), "VM must not have an external IP"
    assert vm["shielded_instance_config"][0]["enable_secure_boot"] is True
    tc_main = (PLATFORM_DIR / "modules" / "04-tenant-compute" / "main.tf").read_text()
    assert "vm-artifacts/cbs-payload.tar.gz" in tc_main
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" in tc_main

    # Model Armor template with LOW_AND_ABOVE floor compliance
    ma = [r for r in resources if r["type"] == "google_model_armor_template"]
    assert len(ma) == 1
    assert ma[0]["values"]["template_id"] == "cymbal-text-guard"
    rai_levels = {
        f["filter_type"]: f["confidence_level"]
        for f in ma[0]["values"]["filter_config"][0]["rai_settings"][0]["rai_filters"]
    }
    assert rai_levels == {
        "HATE_SPEECH": "LOW_AND_ABOVE",
        "HARASSMENT": "LOW_AND_ABOVE",
        "DANGEROUS": "LOW_AND_ABOVE",
        "SEXUALLY_EXPLICIT": "LOW_AND_ABOVE",
    }

    # API Gateway & OpenAPI template
    gw = [r for r in resources if r["type"] == "google_api_gateway_gateway"]
    assert len(gw) == 1
    assert gw[0]["values"]["gateway_id"] == "god-view-loan-api-gw"
    openapi_text = OPENAPI_SPEC_TPL.read_text()
    assert "/v1/loans/apply:" in openapi_text
    assert "/api/v1/loans/apply:" in openapi_text
    assert "/admin" not in openapi_text

    # Global LB, Cloud Armor, IAP, DNS
    negs = {
        r["values"]["name"]
        for r in resources
        if r["type"] == "google_compute_region_network_endpoint_group"
    }
    assert negs == {
        "god-view-neg-portal",
        "god-view-neg-los",
        "god-view-neg-apigw",
        "god-view-neg-sovereign",
        "god-view-neg-cockpit",
    }
    bs = {
        r["values"]["name"]: r["values"]
        for r in resources
        if r["type"] == "google_compute_backend_service"
    }
    assert set(bs.keys()) == {
        "god-view-bs-portal",
        "god-view-bs-los",
        "god-view-bs-apigw",
        "god-view-bs-cockpit",
        "god-view-bs-sovereign-api",
        "god-view-bs-sovereign-public",
    }
    for iap_bs in ("god-view-bs-los", "god-view-bs-cockpit", "god-view-bs-sovereign-api"):
        assert bs[iap_bs]["iap"][0]["enabled"] is True

    armor = [r for r in resources if r["type"] == "google_compute_security_policy"]
    assert len(armor) == 1
    assert armor[0]["values"]["name"] == "god-view-edge-policy"
    rules = {ru["priority"]: ru for ru in armor[0]["values"]["rule"]}
    assert rules[100]["preview"] is True
    assert rules[100]["action"] == "deny(403)"
    assert rules[2000]["action"] == "throttle"

    dns_recs = {r["values"]["name"] for r in resources if r["type"] == "google_dns_record_set"}
    assert dns_recs == {
        "cymbal.ak-demos.com.",
        "los.cymbal.ak-demos.com.",
        "api.cymbal.ak-demos.com.",
        "godview.ak-demos.com.",
    }


def test_stage3_schedulers_monitoring_budget_and_hibernate_job(
    resources: list[dict], tmp_path_factory: pytest.TempPathFactory
):
    scheds = {
        r["values"]["name"]: r["values"]
        for r in resources
        if r["type"] == "google_cloud_scheduler_job"
    }
    expected_schedules = {
        "godview-tenant-pause": "0 17 * * *",
        "godview-tenant-resume": "0 9 * * *",
        "godview-probe-run": "*/5 9-16 * * *",
        "godview-agent-reflection": "30 3 * * *",
        "godview-simulator-tick": "* 9-16 * * *",
    }
    assert set(scheds.keys()) == set(expected_schedules.keys())
    for jname, cron in expected_schedules.items():
        assert scheds[jname]["schedule"] == cron
        assert scheds[jname]["time_zone"] == "Asia/Kolkata"
        assert scheds[jname]["http_target"][0]["oidc_token"][0]["service_account_email"].startswith(
            "godview-sense-sa@"
        )

    uptime = [r for r in resources if r["type"] == "google_monitoring_uptime_check_config"]
    assert len(uptime) == 1
    assert uptime[0]["values"]["http_check"][0]["path"] == "/public/healthz"
    assert uptime[0]["values"]["monitored_resource"][0]["labels"]["host"] == "godview.ak-demos.com"

    alerts = [r for r in resources if r["type"] == "google_monitoring_alert_policy"]
    assert len(alerts) == 1

    channels = [r for r in resources if r["type"] == "google_monitoring_notification_channel"]
    assert len(channels) == 1
    assert channels[0]["values"]["labels"]["email_address"] == "admin@kambleashish.altostrat.com"

    jobs = [r for r in resources if r["type"] == "google_cloud_run_v2_job"]
    assert len(jobs) == 1
    assert jobs[0]["values"]["name"] == "god-view-hibernate-job"

    budgets = [r for r in resources if r["type"] == "google_billing_budget"]
    assert len(budgets) == 1
    assert budgets[0]["values"]["amount"][0]["specified_amount"][0]["units"] == "600"

    # Verify create_budget=false disables the budget resource cleanly
    no_budget_dir = tmp_path_factory.mktemp("tf_no_budget")
    no_budget_plan = _run_plan(no_budget_dir, extra_vars={"create_budget": "false"})
    no_budget_res = _collect_resources(no_budget_plan["planned_values"]["root_module"])
    assert len([r for r in no_budget_res if r["type"] == "google_billing_budget"]) == 0


def test_labels_and_observer_role_invariants(resources: list[dict]):
    checked = 0
    for r in resources:
        vals = r.get("values", {}) or {}
        labels = vals.get("labels") or vals.get("resource_labels") or vals.get("user_labels")
        if (
            isinstance(labels, dict)
            and labels
            and r["type"] != "google_monitoring_notification_channel"
        ):
            checked += 1
            assert labels.get("godview_managed") == "true", (
                f"{r['address']} missing godview_managed=true"
            )
            assert labels.get("tf_module"), f"{r['address']} missing tf_module"
            assert labels.get("tenant") in ("cymbal", "godview"), (
                f"{r['address']} invalid tenant label"
            )
            if labels.get("tenant") == "godview":
                assert labels.get("godview_role") == "observer", (
                    f"{r['address']} missing godview_role=observer"
                )
    assert checked >= 15


def test_hibernate_and_datastream_both_directions(
    plan_json: dict, tmp_path_factory: pytest.TempPathFactory
):
    default_res = _collect_resources(plan_json["planned_values"]["root_module"])
    default_sql = [r for r in default_res if r["type"] == "google_sql_database_instance"][0]
    assert default_sql["values"]["settings"][0]["activation_policy"] == "ALWAYS"
    default_vm = [r for r in default_res if r["type"] == "google_compute_instance"][0]
    assert default_vm["values"]["desired_status"] == "RUNNING"
    assert not [r for r in default_res if r["type"].startswith("google_datastream_")]

    tmp_toggled = tmp_path_factory.mktemp("tf_toggled_dir")
    toggled_plan = _run_plan(
        tmp_toggled,
        extra_vars={"hibernate": "true", "enable_datastream": "true"},
    )
    toggled_res = _collect_resources(toggled_plan["planned_values"]["root_module"])
    toggled_sql = [r for r in toggled_res if r["type"] == "google_sql_database_instance"][0]
    assert toggled_sql["values"]["settings"][0]["activation_policy"] == "NEVER"
    toggled_vm = [r for r in toggled_res if r["type"] == "google_compute_instance"][0]
    assert toggled_vm["values"]["desired_status"] == "TERMINATED"
    assert len([r for r in toggled_res if r["type"].startswith("google_datastream_")]) >= 1
