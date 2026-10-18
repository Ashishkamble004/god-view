# Terraform Platform Apply Log (`infra/terraform/platform/`)

## Stage 1 — Foundation, IAM, Network, Data Stores, GKE Autopilot & Nervous System

- **Plan Approved**: Board `#85` (`@infra-verifier PLAN OK stage 1 (rev 2)`) at `2026-10-16T17:34:25Z`
- **Apply Executed**: `2026-10-16T17:34:42Z` – `2026-10-16T17:54:45Z` (`terraform apply -input=false tfplan-stage1`) and completed at `2026-10-17T07:46:14Z` (`terraform apply -input=false tfplan-stage1-fix`)
- **Remote State**: `gs://general-ak-god-view-tfstate` (prefix `platform`)
- **Result**: **140 / 140 resources created** (+ 1 imported state bucket `general-ak-god-view-tfstate` updated in-place with labels + `prevent_destroy = true`); **0 destroyed**.
  - **APIs (30)**: All 30 `google_project_service` resources created (`disable_on_destroy = false`).
  - **Identities & WIF**:
    - 5 Service Accounts (`godview-sense-sa`, `godview-act-sa`, `godview-runtime-sa`, `godview-apigw-sa`, `godview-deploy-sa`) — zero SA keys.
    - Project IAM bindings (`roles/aiplatform.user` included on `godview-runtime-sa`) + SA-scoped impersonation (`sense -> act` `serviceAccountTokenCreator`, `act -> runtime` `serviceAccountUser`).
    - Workload Identity Federation pool `github-pool` and OIDC provider `github-provider`.
    - Artifact Registry repository `god-view-containers` (`us-central1`, `DOCKER`).
  - **VPC, PSA, NAT, ILB IPs & Firewalls**:
    - `god-view-vpc`, `god-view-subnet` (`10.10.0.0/20`), `god-view-psa-range` (`/16`), `google_service_networking_connection.psa`, `god-view-router`, `god-view-nat`.
    - Static internal IPs `god-view-kyc-ilb-ip` and `god-view-los-ilb-ip`.
    - 5 firewall rules (`gv-allow-subnet-to-gke-ilb`, `gv-allow-subnet-to-cbs`, `gv-allow-egress-cloudsql`, `gv-allow-health-checks`, `gv-allow-iap-ssh`).
  - **Data Stores**:
    - 5 GCS buckets (`general-ak-god-view-{kyc-vault,cdn-assets,agent-skills,tfstate,agent-staging}`) + IAM bindings.
    - 3 Secret Manager secrets (`cymbal-pg-app-password`, `godview-github-app-key`, `godview-github-webhook-secret`) + secret versions + IAM bindings.
    - Firestore Native database `god-view-memory-db` (`us-central1`) + 4 768-dim flat vector indexes (`discovered_services.purpose_embedding`, `invariants.statement_embedding`, `episodic_incidents.symptom_embedding`, `procedural_skills.trigger_embedding`) — completed in `6m01s`.
    - BigQuery dataset `godview_ledger` (`us-central1`) + 4 DAY-partitioned/clustered tables (`events`, `state_transitions`, `journeys`, `kpi_rollups`).
    - Cloud SQL PostgreSQL 16 instance `god-view-pg` (`ENTERPRISE`, `db-g1-small`, `ZONAL`, private IP, `cloudsql.logical_decoding=on`, `pg_stat_statements.track=all`, `cloudsql.iam_authentication=on`) — completed in `4m48s` + database `godview_lms` + users `app_user` and `godview_readonly`.
  - **GKE Autopilot & Workload Identity**:
    - `god-view-gke-cluster` (`us-central1`, Autopilot, private nodes, master CIDR `172.16.0.0/28`, DNS endpoint enabled, `REGULAR` channel) — completed in `9m10s`.
    - Workload Identity bindings for `cymbal/kyc-model-armor-svc` and `cymbal/underwriting-los-svc` on `godview-runtime-sa`.
  - **Nervous System**:
    - Pub/Sub topics `godview-nervous-system` and `godview-nervous-system-dlq`.
    - Project-wide Cloud Audit Log sink `godview-audit-to-nervous-system` + writer IAM binding (Note: carries all `general-ak` admin activity; Sovereign filters by `godview_managed="true"` label/resource).
  - **Model Armor**:
    - `google_model_armor_template.text_guard` (`projects/general-ak/locations/us-central1/templates/cymbal-text-guard`) created at `2026-10-17T07:46:14Z` after aligning all 4 RAI filters (`HATE_SPEECH`, `HARASSMENT`, `DANGEROUS`, `SEXUALLY_EXPLICIT`) to `LOW_AND_ABOVE` per project `769002985772` effective floor settings.
- **Stage 1 Key Outputs (`infra/terraform/platform/outputs.json`)**:
  - `artifact_registry_url`: `us-central1-docker.pkg.dev/general-ak/god-view-containers`
  - `gke_cluster_name`: `god-view-gke-cluster` (`35.232.94.232`, DNS endpoint `gke-00dca33da82641e7a02cb81ed031de9e7418-769002985772.us-central1.gke.goog`)
  - `sql_connection_name`: `general-ak:us-central1:god-view-pg` (`sql_private_ip`: `10.186.0.3`)
  - `kyc_ilb_address`: `10.10.0.3` (`god-view-kyc-ilb-ip`), `los_ilb_address`: `10.10.0.2` (`god-view-los-ilb-ip`)
  - `firestore_db_name`: `god-view-memory-db`, `ledger_dataset_id`: `godview_ledger`
  - `nervous_system_topic`: `godview-nervous-system`, `nervous_system_dlq_topic`: `godview-nervous-system-dlq`
  - `wif_provider_name`: `projects/769002985772/locations/global/workloadIdentityPools/github-pool/providers/github-provider`

## Stage 2 — Cloud Run Services, GCE CBS VM, API Gateway, Global Edge LB, Cloud Armor, IAP & DNS

- **Plan Approved**: Board `#163` (`@infra-verifier PLAN OK stage 2`) at `2026-10-17T07:55:01Z` (including the 3 verifier additions: `UNDERWRITING_SVC_URL` + `INSURANCE_SVC_URL` on `loan_orchestrator`, `GITHUB_WEBHOOK_SECRET` on `god-view-sovereign`, and `/api/*` + `/public/*` path rules on `pm-godview`).
- **Apply Executed**: `2026-10-17T07:55:49Z` – `2026-10-17T08:09:44Z` (`terraform apply -input=false tfplan-stage2`)
- **Result**: **69 resources created, 1 updated in-place, 0 destroyed**.
  - **8 Cloud Run v2 Services** + IAM Invokers (zero `allUsers`/`allAuthenticatedUsers`):
    - `commercial-fleet-credit-svc`: `https://commercial-fleet-credit-svc-mcj3w7ujpq-uc.a.run.app`
    - `motor-insurance-svc`: `https://motor-insurance-svc-mcj3w7ujpq-uc.a.run.app`
    - `ai-customer-simulator`: `https://ai-customer-simulator-mcj3w7ujpq-uc.a.run.app`
    - `loan-orchestrator-api`: `https://loan-orchestrator-api-mcj3w7ujpq-uc.a.run.app`
    - `customer-loan-portal-web`: `https://customer-loan-portal-web-mcj3w7ujpq-uc.a.run.app`
    - `internal-bank-los-web`: `https://internal-bank-los-web-mcj3w7ujpq-uc.a.run.app`
    - `god-view-sovereign`: `https://god-view-sovereign-mcj3w7ujpq-uc.a.run.app`
    - `god-view-sre-web`: `https://god-view-sre-web-mcj3w7ujpq-uc.a.run.app`
  - **GCE VM `core-banking-cibil-vm`**: `us-central1-a`, `e2-micro`, private IP `10.10.0.5` (`cbs_vm_internal_ip`), Shielded VM, no external IP.
  - **API Gateway**: `god-view-loan-api`, `god-view-loan-cfg-*`, and gateway `god-view-loan-api-gw` (`god-view-loan-api-gw-9t9witd8.uc.gateway.dev`).
  - **Global External Application Load Balancer, Cloud Armor, CDN, IAP & DNS**:
    - Global static IP `god-view-lb-ip`: `35.244.128.165` (`lb_ip_address`).
    - Managed SSL certificate `god-view-edge-cert` for `cymbal.ak-demos.com`, `los.cymbal.ak-demos.com`, `api.cymbal.ak-demos.com`, `godview.ak-demos.com`.
    - 5 Serverless NEGs + 6 Backend Services (`iap.enabled = true` on `los`, `cockpit`, `sovereign-api` + `roles/iap.httpsResourceAccessor` for `user:admin@kambleashish.altostrat.com`) + CDN Backend Bucket `god-view-cdn-assets-backend`.
    - URL maps `god-view-edge` & `god-view-http-redirect` + HTTPS/HTTP proxies & forwarding rules.
    - Cloud Armor security policy `god-view-edge-policy` (WAF SQLi/XSS, 120 req/min/IP rate limit, priority 100 emergency kill-switch rule in preview).
    - 4 DNS A records (`cymbal`, `los.cymbal`, `api.cymbal`, `godview.ak-demos.com.`) pointing to `35.244.128.165`.
  - **Pub/Sub Push Subscription**: `godview-sovereign-push` (`https://god-view-sovereign-mcj3w7ujpq-uc.a.run.app/v1/nervous-system/push`, OIDC via `godview-sense-sa`, DLQ `godview-nervous-system-dlq`).
  - **Firestore Vector Indexes**: All 4 768-dim flat vector indexes created with `lifecycle { ignore_changes = [fields] }`.

## Stage 3 — Cloud Scheduler Jobs, Cloud Monitoring, Billing Budget, Hibernate Job & Vertex AI Staging IAM

- **Plan Approved**: Board `#174` (`@infra-verifier PLAN OK stage 3`) at `2026-10-17T08:21:47Z` (including the `/v1/tenant/pause` and `/v1/tenant/resume` scheduler URI alignment).
- **Apply Executed**: `2026-10-17T08:22:12Z` – `2026-10-17T08:25:40Z` (`terraform apply -input=false tfplan-stage3` + `google_billing_budget.godview[0]` with `billing_project = "general-ak"`, `user_project_override = true`)
- **Result**: **11 / 11 Stage 3 resources created, 0 destroyed** (**220 / 220 total platform resources applied across Stages 1, 2, and 3**).
  - **5 Cloud Scheduler Jobs** (`Asia/Kolkata`, OIDC via `godview-sense-sa@general-ak.iam.gserviceaccount.com`):
    - `godview-tenant-pause` (`0 17 * * *` -> `POST https://god-view-sovereign-mcj3w7ujpq-uc.a.run.app/v1/tenant/pause`)
    - `godview-tenant-resume` (`0 9 * * *` -> `POST https://god-view-sovereign-mcj3w7ujpq-uc.a.run.app/v1/tenant/resume`)
    - `godview-probe-run` (`*/5 9-16 * * *` -> `POST https://god-view-sovereign-mcj3w7ujpq-uc.a.run.app/v1/probes/run`)
    - `godview-agent-reflection` (`30 3 * * *` -> `POST https://god-view-sovereign-mcj3w7ujpq-uc.a.run.app/v1/memory/reflect`)
    - `godview-simulator-tick` (`* 9-16 * * *` -> `POST https://ai-customer-simulator-mcj3w7ujpq-uc.a.run.app/v1/simulator/tick`)
  - **3 Cloud Monitoring Resources**:
    - `google_monitoring_notification_channel.operator_email` (`projects/general-ak/notificationChannels/17176394192918736918`, `admin@kambleashish.altostrat.com`)
    - `google_monitoring_uptime_check_config.sovereign_health` (`god-view-sovereign-uptime`, `https://godview.ak-demos.com/public/healthz`, `60s` period, `10s` timeout)
    - `google_monitoring_alert_policy.sovereign_heartbeat` (`projects/general-ak/alertPolicies/5589601309521396425`)
  - **Billing Budget**:
    - `google_billing_budget.godview[0]` (`billingAccounts/019575-4F86FB-B4652B/budgets/4c86e797-02c3-421d-b428-7a8086b56f42`, `god-view-monthly-budget-600`, `$600 USD`, thresholds `50%`, `80%`, `100%`, scoped to `projects/769002985772` and `godview_managed = "true"`)
  - **Cloud Run v2 Job**:
    - `google_cloud_run_v2_job.hibernate` (`projects/general-ak/locations/us-central1/jobs/god-view-hibernate-job`, `godview-act-sa@general-ak.iam.gserviceaccount.com`)
  - **Vertex AI Reasoning Engine Staging Bucket IAM**:
    - `google_storage_bucket_iam_member.staging_re_service_agent` (`roles/storage.objectViewer` for `serviceAccount:service-769002985772@gcp-sa-aiplatform-re.iam.gserviceaccount.com` on `general-ak-god-view-agent-staging`)

## Live Log Audit & E2E Fixes (Revisions 3 & 4)

- **Rev 3 (`2026-10-17T09:11:02Z`)**:
  - Expanded `source_ranges` in `google_compute_firewall.allow_subnet_to_cbs` (`gv-allow-subnet-to-cbs`) and `google_compute_firewall.allow_subnet_to_gke_ilb` (`gv-allow-subnet-to-gke-ilb`) to `["10.10.0.0/20", "10.163.128.0/17", "10.0.0.0/8"]` so GKE Autopilot pods (`10.163.128.0/17`) can reach `core-banking-cibil-vm` (`10.10.0.5:8085`).
  - Routed `paths = ["/public/healthz", "/healthz"]` on `pm-godview` (`god-view-edge` URL map) to `god-view-bs-portal` (`customer-loan-portal-web`, `invoker_iam_disabled = true`) so `god-view-sovereign-uptime` gets `200 OK` at the GCLB edge without causing unauthenticated `403` warnings on `god-view-sovereign`.
- **Rev 4 (`2026-10-17T09:38:00Z`, Board `#220` / `#221`)**:
  - Set `vpc_access.egress = "ALL_TRAFFIC"` on `loan-orchestrator-api` and `ai-customer-simulator` in `modules/04-tenant-compute/main.tf` so outbound `*.run.app` calls traverse `god-view-subnet` (which has `private_ip_google_access = true`) and are accepted as internal VPC traffic by `motor-insurance-svc` and `commercial-fleet-credit-svc` (`ingress = "INGRESS_TRAFFIC_INTERNAL_ONLY"`).

- **Rev 5 (`2026-10-17T12:31Z`–`12:52Z`, coordinator; see `issues-identified-17-10-26.md` at the repo root)**:
  - **Root cause of the God-View / LOS IAP login 403 and of every tenant JSON-`POST` 403:** `google_compute_security_policy.edge_policy` rule 1000 calls `evaluatePreconfiguredWaf(...)` with no `sensitivity`, so Cloud Armor uses **level 4**. CRS signature `owasp-crs-v030301-id942432-sqli` (≥ 2 special characters in args/body) rejects IAP's OAuth callback and any JSON body before IAP or the backend sees it.
  - Added `google_compute_security_policy.control_policy` (`god-view-control-policy`): WAF at `{'sensitivity': 1}`, `json_parsing = "STANDARD"`, `log_level = "VERBOSE"`, 600 req/min/IP, and no kill-switch rule. Applied with `terraform apply -target=module.network_edge.google_compute_security_policy.control_policy` (1 added).
  - Moved `cockpit`, `sovereign_api` and `sovereign_public` backends to `control_policy`. Added `log_config { enable = true, sample_rate = 1.0 }` to all 6 backend services. Re-routed `pm-godview` `/healthz` and `/public/healthz` from `god-view-bs-portal` to `god-view-bs-sovereign-public`, ending the false-positive heartbeat that ran 08:56–12:36Z.
  - These were applied with `gcloud` instead of a targeted plan: the targeted plan pulls in the drifted tenant Cloud Run services as dependencies and would have rewritten them. Terraform state was then re-synced with `terraform state rm` + `terraform import` for exactly those 7 resources (6 backend services + `google_compute_url_map.edge`). Backup: `~/tmp/platform-state-backup-20261017T123655Z.tfstate`. A follow-up targeted plan shows **0 changes** for all edge resources.
  - Cloud Run, via `gcloud`, restoring Terraform intent after an *antigravity* session at 12:06Z had set `ingress=all` / `invoker-iam-disabled=True`:
    - `god-view-sre-web`, `internal-bank-los-web`, `customer-loan-portal-web` → `internal-and-cloud-load-balancing`;
    - invoker IAM check re-enabled on `god-view-sre-web`, `god-view-sovereign`, `internal-bank-los-web` and `loan-orchestrator-api`.
  - Tenant WAF (`god-view-edge-policy`) deliberately **unchanged**: it is God-View test case T1.
  - Known side effect: with Sovereign IAM enforced, the anonymous uptime check on `godview.ak-demos.com/public/healthz` returns 403. It needs an OIDC uptime check plus `roles/run.invoker` for `service-769002985772@gcp-sa-monitoring-notification.iam.gserviceaccount.com`, which is pending owner approval.

- **Rev 6 (`2026-10-17T17:30:00Z`, @infra; God-View v5 Rebuild Infrastructure Spec §3.8)**:
  - **Module `05-godview`**:
    - **Uptime check**: Updated `google_monitoring_uptime_check_config.sovereign_health` to probe the direct Sovereign Cloud Run `*.run.app` host with OIDC service agent authentication (`http_check.service_agent_authentication { type = "OIDC_TOKEN" }`), path `/public/healthz`.
    - **Sovereign IAM invokers**: Added `serviceAccount:service-769002985772@gcp-sa-monitoring-notification.iam.gserviceaccount.com` `roles/run.invoker` to `local.sovereign_invokers` (granted by hand at 17:09Z; imported by deployer).
    - **Sovereign resources**: Allocated 2 vCPU / 2 GiB (`sovereign_cpu = "2"`, `sovereign_memory = "2Gi"`, `min=max=1`).
    - **Sovereign environment variables**: Added `GOOGLE_CLOUD_PROJECT = var.project_id`, `GODVIEW_REGIONS = "us-central1"`, `IAP_BACKEND_SERVICE_IDS` (numeric IDs of `god-view-bs-sovereign-api` and `god-view-bs-cockpit` resolved via data sources without cyclic module dependencies), `PERCEPTION_INTERVAL_SECONDS = "300"`, and confirmed `GODVIEW_ENV = "gcp"`.
    - **Secrets hygiene**: Moved `PG_READONLY_PASSWORD` and `DB_PASSWORD` from plaintext environment variables to Secret Manager `value_source.secret_key_ref` referencing `godview-pg-readonly-password:latest`.
    - **Log sink hygiene**: Added exclusions to `google_logging_project_sink.audit_to_nervous_system` for high-volume GKE Autopilot noise (`io.k8s.coordination.v1.leases`, `io.k8s.core.v1.configmaps`, `pods.binding`, `endpointslices`).
    - **Pub/Sub DLQ**: Created `google_pubsub_subscription.nervous_system_dlq` (`godview-nervous-system-dlq-sub`) with 7-day retention (`604800s`), ack retention, and subscriber IAM binding for `godview-sense-sa`. Set topic retention to 7 days.
  - **Module `01-project-iam`**:
    - Scoped `godview-sense-sa` project roles strictly to plan §3.8: removed broad `roles/viewer`; added `compute.viewer`, `run.viewer`, `pubsub.viewer`, `iam.securityReviewer`, `apigateway.viewer`, `container.viewer`, `cloudsql.viewer`, `bigquery.metadataViewer`, `secretmanager.viewer` alongside `logging.viewer`, `monitoring.viewer`, `datastore.user`, `bigquery.jobUser`, `cloudasset.viewer`, `aiplatform.user`, `cloudtrace.user`.
    - Scoped `godview-act-sa` project roles strictly to plan §3.8: `roles/compute.securityAdmin`, `roles/run.admin`, `roles/pubsub.editor`, `roles/container.developer` (+ `roles/iam.serviceAccountUser` on `godview-runtime-sa`). Removed unapproved broad roles (`instanceAdmin.v1`, `cloudsql.editor`, `cloudscheduler.admin`, `run.developer`).
    - Added custom role `godview_hibernate_operator` for `godview-act-sa` (per coordinator decision #47) with minimal permissions for `god-view-hibernate-job` and agent scaling (`compute.instances.{get,start,stop}`, `compute.zoneOperations.get`, `cloudsql.instances.{get,update}`, `cloudscheduler.jobs.{get,pause,resume,run}`, `aiplatform.reasoningEngines.{get,list,update}`).
    - Granted `roles/iam.serviceAccountUser` on `godview-sense-sa` to Reasoning Engine service agent `serviceAccount:service-${var.project_number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com` (engines run as `godview-sense-sa`).
  - **Module `03-data`**:
    - Created Secret Manager secret `godview-pg-readonly-password` and secret version.
    - Granted `roles/secretmanager.secretAccessor` on `godview-pg-readonly-password` to `godview-sense-sa`.
    - Rotated Cloud SQL user password via `random_password.pg_readonly` rotation keeper `v5-secret-manager` and updated `google_sql_user.godview_readonly` with explicit `depends_on` secret version and accessor to eliminate rotation lockout race.
    - Exported `godview_pg_readonly_password` in `secret_ids` and exported `pg_readonly_secret_version_id` with `depends_on` on the secretAccessor IAM member. Removed dead `sql_readonly_password` output.
    - BigQuery schema sync: Synced `schemas/events.json` and `schemas/journeys.json` with `godview/ledger/schemas/` per `@neutrality` request (`entity_type`, `state_from`, `state_to`, `numeric_attributes`, `currency`, `total_value`). Recreating empty tables in targeted apply.
  - **Module `05-godview`**:
    - **Uptime check**: Updated `google_monitoring_uptime_check_config.sovereign_health` to probe the direct Sovereign Cloud Run `*.run.app` host with OIDC service agent authentication (`http_check.service_agent_authentication { type = "OIDC_TOKEN" }`), path `/public/healthz`, and `lifecycle { create_before_destroy = true }`.
    - **Alert policy target**: Targeted `google_monitoring_alert_policy.sovereign_heartbeat` in the same apply to prevent orphan check deletion errors.
    - **Sovereign IAM invokers**: Added `serviceAccount:service-769002985772@gcp-sa-monitoring-notification.iam.gserviceaccount.com` `roles/run.invoker` to `local.sovereign_invokers` (granted by hand at 17:09Z; imported by deployer).
    - **Sovereign resources**: Allocated 2 vCPU / 2 GiB (`sovereign_cpu = "2"`, `sovereign_memory = "2Gi"`, `min=max=1`).
    - **Sovereign environment variables**: Added `GOOGLE_CLOUD_PROJECT = var.project_id`, `GOOGLE_CLOUD_PROJECT_NUMBER = var.project_number`, `GODVIEW_REGIONS = "us-central1"`, `IAP_BACKEND_SERVICE_IDS` (numeric IDs of `god-view-bs-sovereign-api` and `god-view-bs-cockpit` resolved via data sources without cyclic module dependencies), `PERCEPTION_INTERVAL_SECONDS = "300"`, `GODVIEW_ENV = "gcp"`, and explicit `PG_READONLY_SECRET_VERSION` dependency.
    - **Secrets hygiene**: Moved `PG_READONLY_PASSWORD` and `DB_PASSWORD` from plaintext environment variables to Secret Manager `value_source.secret_key_ref` referencing `var.pg_readonly_secret_id`. Removed dead `sql_readonly_password` variable.
    - **Log sink hygiene**: Added exclusions to `google_logging_project_sink.audit_to_nervous_system` for high-volume GKE Autopilot churn using `protoPayload.methodName:"..."` (`leases`, `configmaps`, `pods.binding`, `endpointslices`).
    - **Pub/Sub DLQ**: Created `google_pubsub_subscription.nervous_system_dlq` (`godview-nervous-system-dlq-sub`) with 7-day retention (`604800s`), ack retention, and subscriber IAM binding for `godview-sense-sa`. Set topic retention to 7 days.
  - **Module `04-tenant-compute`**:
    - Unchanged (tenant frozen; Cloud Armor WAF sensitivity 4 fault T1 preserved as God-View test case).

### Rev 7 — 2026-10-17 — Hibernate Role Permission & Uptime Check Cloud Run Resource Target
- **Module `01-project-iam`**:
  - In `google_project_iam_custom_role.act_hibernate`, replaced invalid permission `cloudscheduler.jobs.resume` with valid GCP permission `cloudscheduler.jobs.enable`.
- **Module `03-data`**:
  - In `variables.tf`, flipped default of `ledger_table_deletion_protection` from `false` to `true` post-Step 1c.
- **Module `05-godview`**:
  - In `google_monitoring_uptime_check_config.sovereign_health`, updated `monitored_resource` from `uptime_url` to `cloud_run_revision` (`labels = { project_id, location = "us-central1", service_name = "god-view-sovereign" }`), enabling valid OIDC service agent authentication. Removed `validate_ssl` (only supported on `uptime_url` checks).
  - In `google_monitoring_alert_policy.sovereign_heartbeat`, aligned condition filter to `resource.type="cloud_run_revision"` and aggregation `group_by_fields = ["resource.label.service_name"]`.

### Rev 8 — 2026-10-17 — Firestore TTL Policy & Sovereign OIDC / Service Principals Environment
- **Module `03-data`**:
  - Added `google_firestore_field.change_events_ttl` with `ttl_config {}` on collection `change_events` and field `expires_at` in database `god-view-memory-db` (G9 TTL enforcement).
- **Module `05-godview`**:
  - Added environment variables to `google_cloud_run_v2_service.sovereign`:
    - `GODVIEW_SOVEREIGN_URL`: `var.sovereign_service_url` (`https://god-view-sovereign-mcj3w7ujpq-uc.a.run.app`) for OIDC audience verification.
    - `SERVICE_URL`: alias pointing to the Sovereign service URL.
    - `GODVIEW_SERVICE_PRINCIPALS`: exact project-qualified comma-separated service account emails: `sense_sa_email`, `act_sa_email`, and Monitoring notification SA (`service-769002985772@gcp-sa-monitoring-notification.iam.gserviceaccount.com`). Tenant runtime SA, deploy SA, and internal GCP agent identities excluded per verifier-edge #99.

### Rev 9 — 2026-10-18 — Trace Exporter Principals Environment
- **Module `05-godview`**:
  - Added environment variable `GODVIEW_TRACE_PRINCIPALS` = `${var.runtime_sa_email},${var.sense_sa_email}` to `google_cloud_run_v2_service.sovereign` for in-app authorization of OTLP spans on `POST /v1/traces` (addressing verifier-core #128 item 6).

## v5 deploy log

- **2026-10-17T17:52:45Z**
  - **Command**: `terraform -chdir=infra/terraform/platform import 'module.godview.google_cloud_run_v2_service_iam_member.sovereign_invokers["serviceAccount:service-769002985772@gcp-sa-monitoring-notification.iam.gserviceaccount.com"]' 'projects/general-ak/locations/us-central1/services/god-view-sovereign roles/run.invoker serviceAccount:service-769002985772@gcp-sa-monitoring-notification.iam.gserviceaccount.com'`
  - **Result**: Imported pre-granted monitoring SA run.invoker IAM member into state.
  - **Rollback**: `terraform -chdir=infra/terraform/platform state rm 'module.godview.google_cloud_run_v2_service_iam_member.sovereign_invokers["serviceAccount:service-769002985772@gcp-sa-monitoring-notification.iam.gserviceaccount.com"]'`

- **2026-10-17T17:53:50Z**
  - **Command**: `terraform -chdir=infra/terraform/platform apply -input=false $HOME/tmp/platform-pass1a.tfplan` (targets: `module.project_iam`, `module.data.random_password.pg_readonly`, `module.data.google_secret_manager_secret.godview_pg_readonly_password`, `module.data.google_secret_manager_secret_version.godview_pg_readonly_password`, `module.data.google_secret_manager_secret_iam_member.pg_readonly_pass_sense`, `module.data.google_bigquery_table.events`, `module.data.google_bigquery_table.journeys`, `module.godview.google_cloud_run_v2_service.sovereign`, `module.godview.google_cloud_run_v2_service_iam_member.sovereign_invokers`, `module.godview.google_monitoring_uptime_check_config.sovereign_health`, `module.godview.google_monitoring_alert_policy.sovereign_heartbeat`, `module.godview.google_logging_project_sink.audit_to_nervous_system`, `module.godview.google_pubsub_topic.nervous_system_dlq`, `module.godview.google_pubsub_subscription.nervous_system_dlq`, `module.godview.google_pubsub_subscription_iam_member.dlq_sense_subscriber`)
  - **Result**: 23 added, 4 changed, 9 destroyed. BigQuery ledger tables recreated with neutral schemas; Secret Manager password version 1 mounted; `god-view-sovereign` deployed revision `god-view-sovereign-00007-fqm` (Ready, 0 plaintext passwords); DLQ subscription `godview-nervous-system-dlq-sub` created; IAM roles scoped. Note: custom role `godview_hibernate_operator` and uptime check returned 400 errors pending infra adjustment.
  - **Rollback**: Restore from `gs://general-ak-god-view-tfstate/platform/default.tfstate` backup taken before apply.

- **2026-10-17T17:55:28Z**
  - **Command**: `terraform -chdir=infra/terraform/platform apply -input=false $HOME/tmp/platform-pass1b.tfplan` (target: `module.data.google_sql_user.godview_readonly`)
  - **Result**: 1 changed. Cloud SQL user `godview_readonly` password rotated in-place to match Secret Manager `godview-pg-readonly-password:latest`.
  - **Rollback**: Re-apply previous password via `gcloud sql users set-password godview_readonly --instance=god-view-pg --password=...`

- **2026-10-17T17:56:04Z**
  - **Command**: `terraform -chdir=infra/terraform/platform apply -input=false $HOME/tmp/platform-pass1c.tfplan` (targets: `module.data.google_bigquery_table.events`, `state_transitions`, `journeys`, `kpi_rollups` with `-var="ledger_table_deletion_protection=true"`)
  - **Result**: 4 changed. Deletion protection locked (`true`) on all 4 BigQuery ledger tables. Outputs exported to `outputs.json`.
  - **Rollback**: `terraform -chdir=infra/terraform/platform apply -input=false -var="ledger_table_deletion_protection=false" ...`

- **2026-10-17T18:04:59Z**
  - **Command**: `terraform -chdir=infra/terraform/platform apply -input=false $HOME/tmp/platform-infra-rev4.tfplan` (targets: `module.project_iam.google_project_iam_custom_role.act_hibernate`, `module.project_iam.google_project_iam_member.act_hibernate`, `module.godview.google_monitoring_uptime_check_config.sovereign_health`, `module.godview.google_monitoring_alert_policy.sovereign_heartbeat`)
  - **Result**: 3 added, 2 changed, 1 destroyed. Custom role `projects/general-ak/roles/godview_hibernate_operator` created with valid permission `cloudscheduler.jobs.enable` and bound to `godview-act-sa`. Uptime check replaced with `monitored_resource { type = "cloud_run_revision" }` and OIDC service agent authentication against path `/public/healthz`. Alert policy condition filter and group_by updated to match.
  - **Rollback**: `terraform destroy -target=module.project_iam.google_project_iam_member.act_hibernate ...`

- **2026-10-17T18:08:02Z**
  - **Command**: `terraform -chdir=infra/terraform/platform apply -input=false $HOME/tmp/platform-infra-rev5.tfplan` (targets: `module.godview.google_monitoring_uptime_check_config.sovereign_health`, `module.godview.google_monitoring_alert_policy.sovereign_heartbeat`)
  - **Result**: 1 added, 2 changed, 1 destroyed. `google_monitoring_uptime_check_config.sovereign_health` recreated without `validate_ssl` using `cloud_run_revision` resource and OIDC service agent auth (`god-view-sovereign-uptime--m6bfN8PA-M`). Alert policy `sovereign_heartbeat` updated.
  - **Rollback**: `terraform destroy -target=module.godview.google_monitoring_uptime_check_config.sovereign_health ...`

- **2026-10-17T18:16:58Z**
  - **Command**: `terraform -chdir=infra/terraform/platform apply -input=false $HOME/tmp/platform-infra-rev7.tfplan` (targets: `module.data.google_firestore_field.change_events_ttl`, `module.godview.google_cloud_run_v2_service.sovereign`)
  - **Result**: 1 added, 1 changed, 0 destroyed. Firestore TTL policy on `change_events.expires_at` created in `god-view-memory-db`. Sovereign Cloud Run updated with `GODVIEW_SOVEREIGN_URL`, `SERVICE_URL`, and tightened `GODVIEW_SERVICE_PRINCIPALS`. New revision `god-view-sovereign-00008-l86` is Ready and `/public/healthz` verified 200 OK.
  - **Rollback**: `terraform destroy -target=module.data.google_firestore_field.change_events_ttl ...`

- **2026-10-18T07:44:08Z**
  - **Command**: `terraform -chdir=infra/terraform/platform apply -input=false $HOME/tmp/sovereign-v5.tfplan` (target: `module.godview.google_cloud_run_v2_service.sovereign`)
  - **Result**: 0 added, 1 changed, 0 destroyed. Injected `GODVIEW_TRACE_PRINCIPALS` into `god-view-sovereign` environment.
  - **Rollback**: Remove `GODVIEW_TRACE_PRINCIPALS` from `05-godview/main.tf` and re-apply.

- **2026-10-18T07:44:54Z**
  - **Command**: `./infra/deploy/deploy_run.sh god-view-sovereign us-central1-docker.pkg.dev/general-ak/god-view-containers/god-view-sovereign@sha256:33f61bd59024058bc154afad7b480a712540bda9561cb019152ce66fd0d1c213`
  - **Result**: Deployed v5 unified Sovereign image (Cloud Build `f2c48c20-0efd-4b72-b24f-cc818823902f`). Cloud Run deployed revision `god-view-sovereign-00010-9jz` (Ready, serving 100%). `/public/healthz` verified 200 OK: `mode=gcp`, real GCP stores (`FirestoreStore`, `BigQueryLedger`, `PubSubBus`, `AgentEngineRuntime`), 15/20 adapters healthy, and 4 loop heartbeats active.
  - **Rollback**: `gcloud run services update-traffic god-view-sovereign --to-revisions=god-view-sovereign-00008-l86=100 --project=general-ak --region=us-central1`




