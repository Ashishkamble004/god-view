# God-View v5 & Cymbal Auto Finance — Deployment Runbook

> [!CAUTION]
> **DEPLOYER OPERATIONAL RULE: UNTARGETED APPLY IS STRICTLY FORBIDDEN**
> An untargeted `terraform apply` on `infra/terraform/platform` will attempt to reconcile drifted tenant Cloud Run services, the cockpit, and the hibernate job (manual scaling, client=gcloud drift). ALL terraform applies MUST use targeted resource flags (`-target=...`) as specified in Step 1 until tenant Cloud Run drift is explicitly reconciled.

> [!NOTE]
> **God-View v5 Rebuild (2026-10-17) — see [`issues-identified-17-10-26.md`](issues-identified-17-10-26.md) & [`plans/2026-10-17-god-view-rebuild-plan.md`](plans/2026-10-17-god-view-rebuild-plan.md).**
> - **Zero Fakes in Production (v5 Target)**: Production Sovereign runs with real GCP composition root (`bootstrap.py`) over Firestore (`god-view-memory-db`), BigQuery (`godview_ledger`), Pub/Sub (`godview-nervous-system`), and Vertex AI Agent Engine. `GODVIEW_ENV=gcp` fails closed if any in-memory store is active. (Live Cloud Run revision `god-view-sovereign-00006-mfk` is the legacy v4 baseline running on in-memory fakes; replaced upon v5 deployment).
> - **Section 2.1 Correction**: The stale v4 Sovereign image digest (`god-view-sovereign-00006-mfk` `@sha256:93fae7d6…`) ran on in-memory fakes with zero sense adapters (`app = create_app()`). Replaced by the v5 rebuild container.
> - **Section 2.2 Correction**: **No Court Agents were deployed in v4** (the previous table reflected planned specs; live Vertex AI had 0 `godview_managed` Reasoning Engines). In v5, ADK specialists on Vertex AI Agent Engine (`min_instances = 0`, `gemini-4-argon` default) are deployed dynamically by the Sovereign / deployer once agent engine slice lands.
> - **Infrastructure & Edge (Rev 5 & Rev 6)**:
>   - God-View backends (`god-view-bs-cockpit`, `-sovereign-api`, `-sovereign-public`) use Cloud Armor `god-view-control-policy` (WAF sensitivity 1, verbose logging, 600 req/min/IP rate limit). Tenant backends remain on `god-view-edge-policy` (sensitivity 4; test case T1).
>   - Sovereign Cloud Run sized to 2 vCPU / 2 GiB (`min=max=1`).
>   - Database passwords moved to Secret Manager (`godview-pg-readonly-password:latest`) via `secret_key_ref`.
>   - Monitoring uptime check upgraded to direct Sovereign `.run.app` host with OIDC service agent authentication and `create_before_destroy`.
>   - Audit log sink configured with exclusions for GKE Autopilot churn (`leases`, `configmaps`, `pods.binding`, `endpointslices`).
>   - Pub/Sub DLQ topic backed by a dedicated subscription `godview-nervous-system-dlq-sub` (7-day retention).

- **Project**: `general-ak` (`769002985772`)
- **Primary Region / Zone**: `us-central1` / `us-central1-a` (Gemini `gemini-4-argon` default & `gemini-3.8-flash` fallback model calls at `global`; embeddings `text-embedding-005`, Firestore, Agent Engine, Model Armor at `us-central1`)
- **Terraform State Bucket**: `gs://general-ak-god-view-tfstate` (prefixes: `platform`, `workloads`)
- **Artifact Registry**: `us-central1-docker.pkg.dev/general-ak/god-view-containers`
- **Agent Engine Staging Bucket**: `gs://general-ak-god-view-agent-staging`
- **Owner / Operator Session Window**: `admin@kambleashish.altostrat.com`
- **Safety Contract**: Only resources carrying `godview_managed = "true"` are ever modified. Cloud Run updates use `--image`-only deployments (`./infra/deploy/deploy_run.sh`) so Terraform-managed revision configurations (scaling, VPC egress, service accounts, secrets, env vars, ingress) are inherited intact. Zero `allUsers` IAM invoker bindings are used on Cloud Run (§A3).

---

## 1. Applied (Terraform Platform & Workloads Roots)

### 1.1 Platform Root (`infra/terraform/platform` — Owner: `infra-platform`, `220/220` Baseline + Rev 5 & Rev 6 Updates)

| Stage | Scope | Status | Applied At (UTC) | Key Outputs / Notes |
| :-- | :-- | :-- | :-- | :-- |
| Bootstrap | GCS bucket `gs://general-ak-god-view-tfstate` (uniform access, versioning, `prevent_destroy = true`) | **Applied** | `2026-10-16T17:34:42Z` | Imported and updated in-place (`godview_managed = "true"`) |
| Stage 1 | `01-project-iam` (30 APIs, 5 SAs, WIF), VPC/subnet/PSA/NAT/firewalls/ILB IPs, Artifact Registry `god-view-containers`, 5 buckets, Secret Manager secrets (`cymbal-pg-app-password`, `godview-github-*` applied; `godview-pg-readonly-password` target created in Rev 6 apply), Firestore `god-view-memory-db` + 4 vector indexes, BigQuery `godview_ledger` + 4 tables, Cloud SQL `god-view-pg` (`godview_lms`, `app_user`, `godview_readonly`), GKE Autopilot `god-view-gke-cluster` + WI, Pub/Sub `godview-nervous-system` + `-dlq` + audit sink, Model Armor `cymbal-text-guard` | **Applied / Rev 6 Ready** | `2026-10-17T07:51:52Z` (base) | `sql_private_ip = 10.186.0.3`, `kyc_ilb = 10.10.0.3`, `los_ilb = 10.10.0.2`, `gke_dns_endpoint = gke-00dca33da82641e7a02cb81ed031de9e7418-769002985772.us-central1.gke.goog`. Rev 6: Scoped SA IAM roles strictly to §3.8 + custom hibernate role; rotated `godview_readonly` password in Secret Manager. |
| Stage 2 | 8 Cloud Run v2 services, API Gateway `god-view-loan-api-gw`, 5 serverless NEGs, Global LB `god-view-edge` (`35.244.128.165`) + `god-view-edge-cert` (`ACTIVE`), Cloud CDN backend bucket, Cloud Armor policies (`god-view-control-policy` for God-View backends; `god-view-edge-policy` for tenant), DNS `A` records (`cymbal`, `los.cymbal`, `api.cymbal`, `godview` in `ak-demos-com`), IAP bindings, GCE VM `core-banking-cibil-vm` (`10.10.0.5`), Pub/Sub push `godview-sovereign-push` | **Applied / Rev 6 Ready** | `2026-10-17T08:19:07Z` (base) | `lb_ip_address = 35.244.128.165`, `api_gateway_hostname = god-view-loan-api-gw-9t9witd8.uc.gateway.dev`, `cbs_vm_internal_ip = 10.10.0.5`, `sovereign_service_url = https://god-view-sovereign-mcj3w7ujpq-uc.a.run.app`. Rev 5: `control_policy` sensitivity 1 + LB logging. Rev 6: Sovereign 2 vCPU / 2 GiB, min=max=1, `IAP_BACKEND_SERVICE_IDS`, Secret Manager refs. |
| Stage 3 | 5 Cloud Scheduler jobs (`godview-tenant-pause`, `godview-tenant-resume`, `godview-probe-run`, `godview-agent-reflection`, `godview-simulator-tick` in `Asia/Kolkata`), uptime check `sovereign_health` (OIDC auth to Sovereign `.run.app` host) + alert policy `sovereign_heartbeat` + email channel `operator_email`, `$600` Billing Budget `god-view-monthly-budget-600`, Cloud Run Job `god-view-hibernate-job`, Vertex AI Reasoning Engine staging bucket IAM `staging_re_service_agent` | **Applied / Rev 6 Ready** | `2026-10-17T08:27:47Z` (base) | `hibernate_job_name = god-view-hibernate-job`. Rev 6: OIDC uptime check + monitoring service agent `roles/run.invoker`; DLQ subscription `godview-nervous-system-dlq-sub` (7d retention); audit sink GKE churn exclusions. |

### 1.2 Workloads Root (`infra/terraform/workloads` — Applied by `deployer`, `10/10` Resources Applied)

| Component | Target | Status | Applied At (UTC) | Notes |
| :-- | :-- | :-- | :-- | :-- |
| Namespace `cymbal`, KSAs (`kyc-model-armor-svc`, `underwriting-los-svc`), ConfigMap `cymbal-tenant-config`, 2 Deployments (`250m`/`512Mi` × 1), 2 Internal LoadBalancer Services (`10.10.0.3:8080`, `10.10.0.2:8080`), 2 NetworkPolicies | `god-view-gke-cluster` (`us-central1`) | **Applied (10/10)** | `2026-10-17T08:29:56Z` | Applied via `./infra/deploy/apply_workloads.sh`; outputs saved to `infra/terraform/workloads/outputs.json`; both pods `1/1 Running` |

---

## 2. Deployed (Cloud Build Images, Cloud Run Revisions, GKE Workloads, GCE VM & Agent Engine)

> [!IMPORTANT]
> Services are built and deployed **only after** their slice verifier posts `PASS` on the swarm board. Every deployed digest is pinned in `infra/terraform/platform/images.auto.tfvars` or `infra/terraform/workloads/images.auto.tfvars` via `./infra/deploy/build_image.sh <svc> --pin` / `./infra/deploy/pin_image.py`.

### 2.1 Container Services & Workloads (Live Baseline & v5 Rebuild Targets)

| Service | Compute Target | Author Slice | Verifier Gate | Image Digest (`@sha256:...`) | Revision / Pod / Instance | Service URL / Internal VIP | Status |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| `loan-orchestrator-api` | Cloud Run (`us-central1`, port 8080) | `tenant-orchestrator` | `agents-verifier` PASS | `us-central1-docker.pkg.dev/general-ak/god-view-containers/loan-orchestrator-api@sha256:a08923d490e0b84bd294ff776930e980af469b9626090bf2a96691e6c37d74c8` | `loan-orchestrator-api-00002-4st` | `https://loan-orchestrator-api-mcj3w7ujpq-uc.a.run.app` (`https://api.cymbal.ak-demos.com`) | **Deployed & Serving (Tenant Frozen)** |
| `commercial-fleet-credit-svc` | Cloud Run (`us-central1`, port 8080) | `tenant-vm-cbs` | `infra-verifier` PASS | `us-central1-docker.pkg.dev/general-ak/god-view-containers/commercial-fleet-credit-svc@sha256:85a3ec8d81fafc5b55e608552f8a9503907ca3a48f0e10a4fc32ffbbf732da9b` | `commercial-fleet-credit-svc-00002-j5x` | `https://commercial-fleet-credit-svc-mcj3w7ujpq-uc.a.run.app` (INTERNAL) | **Deployed & Serving (Tenant Frozen)** |
| `motor-insurance-svc` | Cloud Run (`us-central1`, port 8080) | `tenant-vm-cbs` | `infra-verifier` PASS | `us-central1-docker.pkg.dev/general-ak/god-view-containers/motor-insurance-svc@sha256:4b33f97805bdf798d0395fbaef9cbc9ed225785c9dbbcd1cc741d64252b096ad` | `motor-insurance-svc-00002-k48` | `https://motor-insurance-svc-mcj3w7ujpq-uc.a.run.app` (INTERNAL) | **Deployed & Serving (Tenant Frozen)** |
| `core-banking-cibil-vm` | GCE `e2-micro` (`us-central1-a`, port 8085) | `tenant-vm-cbs` | `infra-verifier` PASS | `gs://general-ak-god-view-kyc-vault/vm-artifacts/core-banking-cibil-vm-0f8d1eac7558.tar.gz` (`@sha256:e94e9bcffc9ea0fe10cd600051944c4e87d231d75f9073e9e53e7cb90da84f8a`) | `core-banking-cibil-vm` (`systemd cibil-cbs.service`) | `http://10.10.0.5:8085` (Private VPC) | **Deployed & Serving (Tenant Frozen)** |
| `ai-customer-simulator` | Cloud Run (`us-central1`, port 8080) | `ai-customer-simulator` | `agents-verifier` PASS | `us-central1-docker.pkg.dev/general-ak/god-view-containers/ai-customer-simulator@sha256:3bbaf3d170e67cc735944a55c95a6d262661c6e133ec4f76ca3b4d0992245b98` | `ai-customer-simulator-00002-2x7` | `https://ai-customer-simulator-mcj3w7ujpq-uc.a.run.app` (INTERNAL) | **Deployed & Serving (Tenant Frozen)** |
| `customer-loan-portal-web` | Cloud Run (`us-central1`, port 8080) | `frontends-tenant` | `frontend-verifier` PASS | `us-central1-docker.pkg.dev/general-ak/god-view-containers/customer-loan-portal-web@sha256:01d710e41e20a1358f82ca95e250c7d7d774cc8e404d1d5cd65d5696b9c3d7c7` | `customer-loan-portal-web-00002-9vt` | `https://customer-loan-portal-web-mcj3w7ujpq-uc.a.run.app` (`https://cymbal.ak-demos.com`) | **Deployed & Serving (Tenant Frozen)** |
| `internal-bank-los-web` | Cloud Run (`us-central1`, port 8080) | `frontends-tenant` | `frontend-verifier` PASS | `us-central1-docker.pkg.dev/general-ak/god-view-containers/internal-bank-los-web@sha256:f2ef782277ed6e9a40483c32eac78877d16fa150719af78e78fc5fd6773ba2d5` | `internal-bank-los-web-00002-qgj` | `https://internal-bank-los-web-mcj3w7ujpq-uc.a.run.app` (`https://los.cymbal.ak-demos.com`) | **Deployed & Serving (Tenant Frozen)** |
| `god-view-sre-web` | Cloud Run (`us-central1`, port 8080) | `cockpit` | `verifier-edge` PASS | `us-central1-docker.pkg.dev/general-ak/god-view-containers/god-view-sre-web@sha256:e4d9a2c6e8585449bf9e0ce0aa2beadebd29e9a447ebc09c58649e6f78f3f387` | `god-view-sre-web-00005-84s` | `https://god-view-sre-web-mcj3w7ujpq-uc.a.run.app` (`https://godview.ak-demos.com`) | **Deployed & Serving (v5 3D Globe + D1-D10 Fixes)** |
| `god-view-sovereign` | Cloud Run (`us-central1`, port 8080) | `runtime-core` + `discovery` + `sentinels` + `remediation` | `verifier-core` PASS | `us-central1-docker.pkg.dev/general-ak/god-view-containers/god-view-sovereign@sha256:76d17542f99f4c0cb01bd3dd568615debb99d8372e149fdaa16891c778c578fe` | `god-view-sovereign-00017-xzq` | `https://god-view-sovereign-mcj3w7ujpq-uc.a.run.app` (`https://godview.ak-demos.com/api/*`) | **Deployed & Serving (v5 Real Core + D1-D10 Fixes)** |
| `kyc-model-armor-svc` | GKE Autopilot (`cymbal`, port 8080) | `tenant-gke-utilities` | `agents-verifier` PASS | `us-central1-docker.pkg.dev/general-ak/god-view-containers/kyc-model-armor-svc@sha256:b0c15dd124dd80ddba5ebad4229744941c48812d7b2c74d3d30f97802d1c5dfa` | `pod/kyc-model-armor-svc-59cc4cb697-7972w` (`1/1 Running`) | `http://10.10.0.3:8080` (`god-view-kyc-ilb-ip`) | **Deployed & Running (Tenant Frozen)** |
| `underwriting-los-svc` | GKE Autopilot (`cymbal`, port 8080) | `tenant-gke-utilities` | `agents-verifier` PASS | `us-central1-docker.pkg.dev/general-ak/god-view-containers/underwriting-los-svc@sha256:5eaa5d1d2a737880ebb101ce6328e5b48f134698d6cbf864d9fbf86a45cbe45c` | `pod/underwriting-los-svc-7f87d55f7f-nnxvv` (`1/1 Running`) | `http://10.10.0.2:8080` (`god-view-los-ilb-ip`) | **Deployed & Running (Tenant Frozen)** |

### 2.2 Vertex AI Agent Engine Court (`us-central1`, `min_instances = 0`, `1 vCPU / 2 GiB`, Default Model `gemini-4-argon` @ `global`)

> [!WARNING]
> **Audit Finding (Issues Identified §5 G3):** No Court Agents were deployed in v4 (the previous runbook table recorded intended targets, but live GCP inspection showed 0 `godview_managed` Reasoning Engines).
>
> In v5, Court Agents are ADK multi-agent specialists (`LlmAgent` with generic instructions, reasoning over evidence packs + `BusinessContext` at runtime). They are deployed to Vertex AI Agent Engine dynamically by `CourtManager.spawn_agent` or by `@deployer` via `./infra/deploy/deploy_agent_engine.py` once `@agents` passes verification. In-process fallback ensures detection and diagnosis function immediately even while engines scale up.

| Agent Archetype | Target Role | Reasoning Engine Deployment | Model | In-Process Fallback | Status |
| :-- | :-- | :-- | :-- | :-- | :-- |
| `application__god-view-sre-web` | Service runtime specialist | `projects/769002985772/locations/us-central1/reasoningEngines/3177452539708702720` (`min_instances=0`) | `gemini-4-argon` @ global | Yes | **Deployed & Verified (`:query` 200 OK)** |
| `application__god-view-sovereign` | Service runtime specialist | `projects/769002985772/locations/us-central1/reasoningEngines/7325267796516929536` (`min_instances=0`) | `gemini-4-argon` @ global | Yes | **Deployed & Verified (`:query` 200 OK)** |
| `data__cloudsql-god-view-pg` | Data and state specialist | `projects/769002985772/locations/us-central1/reasoningEngines/6636217053529243648` (`min_instances=0`) | `gemini-4-argon` @ global | Yes | **Deployed & Verified (`:query` 200 OK)** |
| `business_line__journey-main` | Business journey specialist | `projects/769002985772/locations/us-central1/reasoningEngines/2646027783678984192` (`min_instances=0`) | `gemini-4-argon` @ global | Yes | **Deployed & Verified (`:query` 200 OK)** |
| `change__repo-default` | Software change specialist | `projects/769002985772/locations/us-central1/reasoningEngines/918897326582398976` (`min_instances=0`) | `gemini-4-argon` @ global | Yes | **Deployed & Verified (`:query` 200 OK)** |
| `platform__gcp-general-ak` | Cloud platform specialist | `projects/769002985772/locations/us-central1/reasoningEngines/1240904699939389440` (`min_instances=0`) | `gemini-4-argon` @ global | Yes | **Deployed & Verified (`:query` 200 OK)** |

---

## 3. Ordered v5 Deployment Sequence

> [!IMPORTANT]
> Slices are deployed **only after** their respective verifier posts `PASS` on the swarm board (`@verifier-core` for runtime-core/discovery/sentinels/remediation, `@verifier-edge` for agents/neutrality/cockpit/infra). All production mutations must be performed by `@deployer`.

### Step 0: Verify Operator Session & ADC
```bash
export PATH="$HOME/.local/bin:$PATH"
cd /usr/local/google/home/kambleashish/Documents/Projects/god-view

gcloud config set project general-ak
gcloud auth print-access-token >/dev/null && echo "ADC + gcloud auth OK"
```

### Step 1: Reconcile Platform Infrastructure (Rev 6 Terraform)
*(Run after `@verifier-edge` confirms infra review PASS.)*

To completely eliminate the secret rotation race and avoid reconciling drifted tenant Cloud Run services, apply in two targeted passes:

```bash
# 1. Import pre-granted monitoring invoker IAM member into state
terraform -chdir=infra/terraform/platform import \
  'module.godview.google_cloud_run_v2_service_iam_member.sovereign_invokers["serviceAccount:service-769002985772@gcp-sa-monitoring-notification.iam.gserviceaccount.com"]' \
  'projects/general-ak/locations/us-central1/services/god-view-sovereign roles/run.invoker serviceAccount:service-769002985772@gcp-sa-monitoring-notification.iam.gserviceaccount.com'

# Pass 1a: Apply platform updates and Sovereign secret mounting (everything EXCEPT Cloud SQL user)
terraform -chdir=infra/terraform/platform plan -input=false \
  -target=module.project_iam \
  -target=module.data.random_password.pg_readonly \
  -target=module.data.google_secret_manager_secret.godview_pg_readonly_password \
  -target=module.data.google_secret_manager_secret_version.godview_pg_readonly_password \
  -target=module.data.google_secret_manager_secret_iam_member.pg_readonly_pass_sense \
  -target=module.data.google_bigquery_table.events \
  -target=module.data.google_bigquery_table.journeys \
  -target=module.godview.google_cloud_run_v2_service.sovereign \
  -target=module.godview.google_cloud_run_v2_service_iam_member.sovereign_invokers \
  -target=module.godview.google_monitoring_uptime_check_config.sovereign_health \
  -target=module.godview.google_monitoring_alert_policy.sovereign_heartbeat \
  -target=module.godview.google_logging_project_sink.audit_to_nervous_system \
  -target=module.godview.google_pubsub_topic.nervous_system_dlq \
  -target=module.godview.google_pubsub_subscription.nervous_system_dlq \
  -target=module.godview.google_pubsub_subscription_iam_member.dlq_sense_subscriber \
  -out=/usr/local/google/home/kambleashish/tmp/platform-v5-pass1a.tfplan

terraform -chdir=infra/terraform/platform apply -input=false /usr/local/google/home/kambleashish/tmp/platform-v5-pass1a.tfplan

# Verify Sovereign revision is Ready with Secret Manager mounted
gcloud run services describe god-view-sovereign --project=general-ak --region=us-central1 --format='value(status.conditions[0].status)'

# Pass 1b: Rotate Cloud SQL user password to match the new Secret Manager secret
terraform -chdir=infra/terraform/platform apply -input=false \
  -target=module.data.google_sql_user.godview_readonly

terraform -chdir=infra/terraform/platform output -json > infra/terraform/platform/outputs.json
```

### Step 1c: Enable BigQuery Deletion Protection
*(Run immediately after table recreation in Step 1a to safeguard ledger data.)*
Apply deletion protection on the 4 ledger tables using `-var="ledger_table_deletion_protection=true"`:
```bash
terraform -chdir=infra/terraform/platform apply -input=false \
  -var="ledger_table_deletion_protection=true" \
  -target=module.data.google_bigquery_table.events \
  -target=module.data.google_bigquery_table.state_transitions \
  -target=module.data.google_bigquery_table.journeys \
  -target=module.data.google_bigquery_table.kpi_rollups
```

### Step 1d: Reconcile Custom Hibernate Role & Cloud Run Revision Uptime Check (Rev 7)
Apply the custom hibernate role with `cloudscheduler.jobs.enable` and the Cloud Run revision-targeted uptime check with OIDC authentication:
```bash
terraform -chdir=infra/terraform/platform apply -input=false \
  -target=module.project_iam.google_project_iam_custom_role.act_hibernate \
  -target=module.project_iam.google_project_iam_member.act_hibernate \
  -target=module.godview.google_monitoring_uptime_check_config.sovereign_health \
  -target=module.godview.google_monitoring_alert_policy.sovereign_heartbeat
```

### Step 1e: Apply Firestore TTL Policy & Sovereign OIDC / Service Principals Environment (Rev 8/9)
Apply the Firestore TTL policy on `change_events.expires_at` and update `god-view-sovereign` environment variables (`GODVIEW_SOVEREIGN_URL`, `SERVICE_URL`, `GODVIEW_SERVICE_PRINCIPALS`, `GODVIEW_TRACE_PRINCIPALS`):
```bash
terraform -chdir=infra/terraform/platform apply -input=false \
  -target=module.data.google_firestore_field.change_events_ttl \
  -target=module.godview.google_cloud_run_v2_service.sovereign
```

### Step 1f: Reconcile BigQuery Events Table Schema for parent_span_id (Rev 10)
Apply the additive schema update adding `parent_span_id` (STRING NULLABLE) to `google_bigquery_table.events` (`deletion_protection = true` is preserved):
```bash
terraform -chdir=infra/terraform/platform apply -input=false \
  -target=module.data.google_bigquery_table.events
```

> [!IMPORTANT]
> **Key Architecture Decisions for Rev 6 Apply:**
> 1. **Two-Pass Secret Rotation & Rollback Caveat**:
>    Applying in 2 passes (Pass 1a: secret version + IAM + Sovereign revision mount; Pass 1b: Cloud SQL user password rotation) ensures the Cloud Run revision mounts the secret cleanly before the DB password changes.
>    *Rollback Caveat*: Rolling back to a legacy revision (e.g. `god-view-sovereign-00006-mfk`) will fail DB authentication because legacy revisions expect the old plaintext password, whereas the Cloud SQL user is rotated. Any rollback must use a revision configured with `godview-pg-readonly-password:latest`.
> 2. **Agent Engine Identity**:
>    Reasoning engines run as `godview-sense-sa` (`ReasoningEngineSpec.service_account = godview-sense-sa@general-ak.iam.gserviceaccount.com`). The Reasoning Engine service agent (`serviceAccount:service-769002985772@gcp-sa-aiplatform-re.iam.gserviceaccount.com`) is granted `roles/iam.serviceAccountUser` on `godview-sense-sa`. The RE service agent is NOT added to `sovereign_invokers` because calls from engines authenticate as `godview-sense-sa` (already an authorized invoker).
> 3. **Act SA Scoped Hibernate Role**:
>    `godview-act-sa` is granted a custom project role `godview_hibernate_operator` containing only `compute.instances.{get,start,stop}`, `compute.zoneOperations.get`, `cloudsql.instances.{get,update}`, `cloudscheduler.jobs.{get,pause,resume,run}`, and `aiplatform.reasoningEngines.{get,list,update}` for the `god-view-hibernate-job` and operational remediation. Broad roles (`instanceAdmin.v1`, `cloudsql.editor`, `cloudscheduler.admin`, `run.developer`) are destroyed per plan §3.8.
> 4. **BigQuery Ledger Tables Replacement**:
>    `google_bigquery_table.events` and `google_bigquery_table.journeys` are recreated in this apply with the generic neutral schema (`entity_type`, `state_from`, `state_to`, `numeric_attributes`, `currency`, `total_value`). The tables currently hold 0 rows. Step 1c locks `deletion_protection = true`.
> 5. **Uptime Check Replacement**:
>    `google_monitoring_uptime_check_config.sovereign_health` uses `lifecycle { create_before_destroy = true }` and `google_monitoring_alert_policy.sovereign_heartbeat` is targeted in the same apply to prevent orphan alert errors.

### Step 2: Build, Pin, and Deploy v5 Sovereign Control Plane
*(Run once `@verifier-core` posts PASS for runtime-core, discovery, sentinels, remediation.)*
```bash
SOV_DIGEST="$(./infra/deploy/build_image.sh god-view-sovereign --pin)"
./infra/deploy/pin_image.py --root platform --service god-view-hibernate-job --image "${SOV_DIGEST}"
./infra/deploy/deploy_run.sh god-view-sovereign "${SOV_DIGEST}"

# Immediate health check verification
SOV_URL="$(gcloud run services describe god-view-sovereign --project=general-ak --region=us-central1 --format='value(status.url)')"
curl -s -H "Authorization: Bearer $(gcloud auth print-identity-token)" "${SOV_URL}/public/healthz" | jq .
```

### Step 3: Build, Pin, and Deploy v5 Cockpit (3D Globe)
*(Run once `@verifier-edge` posts PASS for cockpit.)*
```bash
COCKPIT_DIGEST="$(./infra/deploy/build_image.sh god-view-sre-web --pin)"
./infra/deploy/deploy_run.sh god-view-sre-web "${COCKPIT_DIGEST}"
```

### Step 4: Deploy Vertex AI Agent Engine Court
*(Run once `@verifier-edge` posts PASS for agents.)*
```bash
SOV_URL="$(gcloud run services describe god-view-sovereign --project=general-ak --region=us-central1 --format='value(status.url)')"
PYTHONPATH=. uv run --with "google-cloud-aiplatform[agent_engines,adk]" python infra/deploy/deploy_agent_engine.py \
  --from-perceive \
  --sovereign-url "${SOV_URL}" \
  --project-id general-ak \
  --location us-central1 \
  --staging-bucket gs://general-ak-god-view-agent-staging \
  --service-account godview-sense-sa@general-ak.iam.gserviceaccount.com
```

### Step 5: Verification & Acceptance Gates (Verifier-Live)
*(Run acceptance gates A1-A8 per Rebuild Plan §4.)*
```bash
# Verify managed SSL certificate
gcloud compute ssl-certificates describe god-view-edge-cert --project=general-ak --format='value(managed.status,managed.domainStatus)'

# Smoke test suite
./infra/deploy/smoke.sh

# Resume tenant traffic if paused
gcloud scheduler jobs run godview-tenant-resume --location=us-central1 --project=general-ak
```

---

## 4. Verification (Smoke Tests, L4 GCP E2E & L5 Drills)

### 4.1 Deployer Scaffolding Self-Test
- `uv run pytest infra/deploy/test_deploy_agent_engine.py -q`: **`8 passed in 0.45s`**. Verifies `AgentEnginePayload`, `DeploymentSpec` (`min_instances=0`, `1 vCPU / 2 GiB`, `container_concurrency=4`, `global` location for `gemini-4-argon` default and `gemini-3.8-flash` fallback, `us-central1` for `text-embedding-005`), `COURT_MAX_AGENTS=40` enforcement, unmanaged-resource protection (`godview_managed="true"` required), earned-warmth `0 <-> 1` toggling, and `images.auto.tfvars` pinning round-trip.

### 4.2 Smoke Test Results (`./infra/deploy/smoke.sh --with-mutations` — `PASS=17 WARN=1 FAIL=0`)
| Check | Target | Result | Timestamp (UTC) | Details |
| :-- | :-- | :-- | :-- | :-- |
| Managed SSL Certificate | `god-view-edge-cert` | **PASS (`ACTIVE`)** | `2026-10-17T08:35:00Z` | Active for `cymbal.ak-demos.com`, `los.cymbal.ak-demos.com`, `api.cymbal.ak-demos.com`, `godview.ak-demos.com` |
| URL 1 (Customer Portal) | `https://cymbal.ak-demos.com` | **PASS (`200`)** | `2026-10-17T08:35:00Z` | Serves React 19 SPA via Global LB + Serverless NEG |
| URL 2 (Internal LOS Portal, IAP) | `https://los.cymbal.ak-demos.com` | **PASS (`302`)** | `2026-10-17T08:35:00Z` | Redirects unauthenticated callers to `accounts.google.com` (IAP enforced) |
| URL 3 (God-View Cockpit, IAP) | `https://godview.ak-demos.com` | **PASS (`302`)** | `2026-10-17T08:35:00Z` | Redirects unauthenticated callers to `accounts.google.com` (IAP enforced) |
| API Gateway | `https://api.cymbal.ak-demos.com/v1/vehicles` | **PASS (`200`)** | `2026-10-17T08:35:00Z` | Returns 8 seeded vehicles via API Gateway -> `loan-orchestrator-api` -> Cloud SQL `god-view-pg` |
| Sovereign Heartbeat | `https://god-view-sovereign-mcj3w7ujpq-uc.a.run.app/public/healthz` | **PASS (`200`)** | `2026-10-17T08:44:00Z` | Returns `{"status":"ok","service":"god-view-sovereign","mcp_servers":["observability-mcp","gke-mcp","mcp-toolbox-postgres","github-mcp"],"sentinel_cadence_s":10,"sentinel_interval_seconds":10}` |
| Cloud Run Ingress Isolation | 6 internal/LB-only services | **PASS** | `2026-10-17T08:35:00Z` | Direct `.run.app` access denied externally (`INGRESS_TRAFFIC_INTERNAL_ONLY` / `INTERNAL_LOAD_BALANCER`) |
| GKE Autopilot Workloads | `kyc-model-armor-svc` (`10.10.0.3:8080`), `underwriting-los-svc` (`10.10.0.2:8080`) | **PASS (`1/1 Running`)** | `2026-10-17T08:38:10Z` | Both pods running in namespace `cymbal` with Internal LoadBalancer IPs bound |
| Cloud SQL Private Networking | `god-view-pg` (`10.186.0.3`) | **PASS (`PRIVATE`)** | `2026-10-17T08:35:00Z` | Private IP only (`ipAddresses.type = PRIVATE`) |
| GCE VM Private Networking | `core-banking-cibil-vm` (`10.10.0.5`) | **PASS (No External IP)** | `2026-10-17T08:35:00Z` | `networkInterfaces[0].accessConfigs` is empty |
| Pub/Sub Nervous System | `godview-nervous-system` | **PASS** | `2026-10-17T08:35:00Z` | Published test CloudEvent to `godview-nervous-system` |
| Cloud Scheduler Pause/Resume | `godview-tenant-pause` | **PASS** | `2026-10-17T08:35:00Z` | Triggered `godview-tenant-pause` job in `us-central1` |

### 4.3 L4 GCP E2E (`infra/tests/test_gcp_e2e.py`) & Auth Session Log
- **L4 GCP E2E Status**:
  - `POST /v1/perceive` on `god-view-sovereign`: **200 OK** in `~0.8s` (`reasoning_model = gemini-4-argon`, `fallback_model = gemini-3.8-flash`, 6 senses active).
  - `GET /v1/vehicles` on `https://api.cymbal.ak-demos.com/v1/vehicles`: **200 OK** (8 seeded vehicles from Cloud SQL `god-view-pg`).
  - `GET /v1/court`, `/v1/sentinels`, `/v1/incidents`, `/v1/chaos/drills` on `god-view-sovereign`: **200 OK**.
  - Noted test-harness adjustments in `#186` for `@infra-workloads-cicd` (`outputs["sovereign_service_url"]` key lookup, `senses` list schema in `assert_uc01_perception_response`, ID token header on `/public/healthz`).
- **Auth Session Health (`gcloud auth print-access-token`)**: Verified active at `2026-10-17T08:45:00Z` (`admin@kambleashish.altostrat.com`).

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
  - **Result**: Custom role `godview_hibernate_operator` created and bound to `godview-act-sa` successfully. Uptime check returned 400 (`validate_ssl feature is currently only allowed on uptime_url checks`).
  - **Rollback**: `terraform destroy -target=module.project_iam.google_project_iam_member.act_hibernate ...`

- **2026-10-17T18:08:02Z**
  - **Command**: `terraform -chdir=infra/terraform/platform apply -input=false $HOME/tmp/platform-infra-rev5.tfplan` (targets: `module.godview.google_monitoring_uptime_check_config.sovereign_health`, `module.godview.google_monitoring_alert_policy.sovereign_heartbeat`)
  - **Result**: 1 added, 2 changed, 1 destroyed. `google_monitoring_uptime_check_config.sovereign_health` recreated successfully with `monitored_resource { type = "cloud_run_revision" }` and OIDC service agent authentication against `/public/healthz`. Alert policy updated.
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

- **2026-10-18T08:04:19Z**
  - **Command**: `./infra/deploy/deploy_run.sh god-view-sovereign us-central1-docker.pkg.dev/general-ak/god-view-containers/god-view-sovereign@sha256:fb9f38720b173704505da8de37a889521dc964d157112c9ece0ebab1acf76815`
  - **Result**: Deployed v5 Sovereign image (Cloud Build `3b51f364-7851-4d3f-bcdd-15b32b970514`) incorporating runtime-core rev 7 (BigQuery streaming insert json serialization + metadata-server OIDC uniqueId matching) and discovery rev 8 (eliminate post-hoc evidence synthesis + bundle reference validation). Cloud Run deployed revision `god-view-sovereign-00011-trb` (Ready, serving 100%). `/public/healthz` verified 200 OK: `mode=gcp`, real GCP stores (`FirestoreStore`, `BigQueryLedger`, `PubSubBus`, `AgentEngineRuntime`), 15/20 adapters healthy, 50 sentinels registered, and all 4 loop heartbeats active.
  - **Rollback**: `gcloud run services update-traffic god-view-sovereign --to-revisions=god-view-sovereign-00010-9jz=100 --project=general-ak --region=us-central1`

- **2026-10-18T08:08:35Z**
  - **Command**: `terraform -chdir=infra/terraform/platform apply -input=false $HOME/tmp/bq-events-parent-span-id.tfplan` (target: `module.data.google_bigquery_table.events`)
  - **Result**: 0 added, 1 changed, 0 destroyed. In-place additive schema update adding nullable `parent_span_id` (STRING) to BigQuery table `godview_ledger.events`. Deletion protection preserved. Verified live via BigQuery client API.
  - **Rollback**: Schema cannot drop columns in BigQuery once added; no destructive action needed as column is nullable.

- **2026-10-19T06:00:00Z – 06:38:00Z**
  - **Command**: `./infra/deploy/build_image.sh god-view-sovereign --pin && ./infra/deploy/deploy_run.sh god-view-sovereign us-central1-docker.pkg.dev/general-ak/god-view-containers/god-view-sovereign@sha256:39f887a38e27505369382739b3ecd829f59c5b4be6049cafa014b799882055be`
  - **Result**: Deployed v5 Sovereign image (Cloud Build `6e12d47b-0572-4d4a-ba2a-cfbe2d82de7f`, commit `555cb43`) with all D1–D9 fixes (Gemini 4 Argon `thought_signature` preservation, universal probe asset integrity check on `/v1/vehicles`, journey_linked workload inference prioritization, hash-suffixed `sentinel_id` on `build_probe_sentinel`, and calibrated confidence floor). Cloud Run deployed revision `god-view-sovereign-00014-78l` (Ready, serving 100%) and updated `god-view-hibernate-job`. `/public/healthz` verified 200 OK.
  - **Rollback**: `gcloud run services update-traffic god-view-sovereign --to-revisions=god-view-sovereign-00012-2r2=100 --project=general-ak --region=us-central1`

- **2026-10-19T06:02:00Z**
  - **Command**: `./infra/deploy/build_image.sh god-view-sre-web --pin && ./infra/deploy/deploy_run.sh god-view-sre-web us-central1-docker.pkg.dev/general-ak/god-view-containers/god-view-sre-web@sha256:f93d5644e84c13951b1c670979c6fc4b6be4ab6c0d4830723681b332390598d4`
  - **Result**: Deployed v5 Cockpit image (Cloud Build `04488556-2404-47f7-aaa8-37bff23e1d35`) with D2/D4/D5/D7/D8 UI & proxy fixes and synced `dist/` to `~/.gemini/config/plugins/godview_cockpit/sidecars/globe/dist/`. Cloud Run deployed revision `god-view-sre-web-00004-64s` (Ready, serving 100%).
  - **Rollback**: `gcloud run services update-traffic god-view-sre-web --to-revisions=god-view-sre-web-00003-r98=100 --project=general-ak --region=us-central1`

- **2026-10-19T06:28:00Z**
  - **Command**: `PYTHONPATH=. uv run --with "google-cloud-aiplatform[agent_engines,adk]" --with a2a-sdk --with google-cloud-firestore --with google-cloud-pubsub --with google-cloud-bigquery --with google-cloud-logging --with google-cloud-monitoring --with google-cloud-compute --with google-cloud-run --with google-cloud-asset --with google-cloud-storage --with google-cloud-secret-manager python infra/deploy/deploy_agent_engine.py --from-perceive --sovereign-url https://god-view-sovereign-mcj3w7ujpq-uc.a.run.app --project-id general-ak --location us-central1 --staging-bucket gs://general-ak-god-view-agent-staging --service-account godview-sense-sa@general-ak.iam.gserviceaccount.com`
  - **Result**: Updated all 6 `godview_managed=true` Vertex AI Reasoning Engines (`3177452539708702720`, `7325267796516929536`, `6636217053529243648`, `2646027783678984192`, `918897326582398976`, `1240904699939389440`) in-place with D3 (`thought_signature`) and D9 (relative `extra_packages`, `a2a-sdk`, `AuthService`, numeric `card.url`) fixes, and synced to Firestore `court_agents`. Verified live `:query` (`card` + `diagnose`) returning 200 OK with `gemini-4-argon` Diagnosis & ProposedAction.
  - **Rollback**: Re-run `deploy_agent_engine.py` from previous git revision.

- **2026-10-19T06:35:00Z**
  - **Command**: `gcloud pubsub subscriptions seek godview-nervous-system-dlq-sub --time="2026-10-17T08:00:00Z" --project=general-ak && gcloud scheduler jobs run godview-tenant-resume --location=us-central1 --project=general-ak`
  - **Result**: DLQ replayed, tenant traffic resumed, core workload `BusinessContext` records re-inferred (`confidence = 0.79..0.88`, `inferred_by = "gemini-4-argon"`), and T1 + T2 incidents diagnosed with L2 `ApprovalCard`s in Firestore `approvals`.







