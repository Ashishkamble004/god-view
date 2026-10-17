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
| `god-view-sre-web` | Cloud Run (`us-central1`, port 8080) | `cockpit` | Pending `verifier-edge` PASS | `us-central1-docker.pkg.dev/general-ak/god-view-containers/god-view-sre-web@sha256:016809f6dc8719c8f2472a7dc54334f396864b047ce54f8f5628de6a714281e9` (stale v4) | `god-view-sre-web-00002-rgp` | `https://god-view-sre-web-mcj3w7ujpq-uc.a.run.app` (`https://godview.ak-demos.com`) | **Pending v5 3D Globe Deploy** |
| `god-view-sovereign` | Cloud Run (`us-central1`, port 8080) | `runtime-core` + `discovery` + `sentinels` + `remediation` | Pending `verifier-core` PASS | `us-central1-docker.pkg.dev/general-ak/god-view-containers/god-view-sovereign@sha256:93fae7d66dc8254c794ce8b99d141e6c3fa983196c14175373307fc14ea8ec46` (stale v4) | `god-view-sovereign-00006-mfk` | `https://god-view-sovereign-mcj3w7ujpq-uc.a.run.app` (`https://godview.ak-demos.com/api/*`) | **Pending v5 Real Core Deploy** |
| `kyc-model-armor-svc` | GKE Autopilot (`cymbal`, port 8080) | `tenant-gke-utilities` | `agents-verifier` PASS | `us-central1-docker.pkg.dev/general-ak/god-view-containers/kyc-model-armor-svc@sha256:b0c15dd124dd80ddba5ebad4229744941c48812d7b2c74d3d30f97802d1c5dfa` | `pod/kyc-model-armor-svc-59cc4cb697-7972w` (`1/1 Running`) | `http://10.10.0.3:8080` (`god-view-kyc-ilb-ip`) | **Deployed & Running (Tenant Frozen)** |
| `underwriting-los-svc` | GKE Autopilot (`cymbal`, port 8080) | `tenant-gke-utilities` | `agents-verifier` PASS | `us-central1-docker.pkg.dev/general-ak/god-view-containers/underwriting-los-svc@sha256:5eaa5d1d2a737880ebb101ce6328e5b48f134698d6cbf864d9fbf86a45cbe45c` | `pod/underwriting-los-svc-7f87d55f7f-nnxvv` (`1/1 Running`) | `http://10.10.0.2:8080` (`god-view-los-ilb-ip`) | **Deployed & Running (Tenant Frozen)** |

### 2.2 Vertex AI Agent Engine Court (`us-central1`, `min_instances = 0`, `1 vCPU / 2 GiB`, Default Model `gemini-4-argon` @ `global`)

> [!WARNING]
> **Audit Finding (Issues Identified §5 G3):** No Court Agents were deployed in v4 (the previous runbook table recorded intended targets, but live GCP inspection showed 0 `godview_managed` Reasoning Engines).
>
> In v5, Court Agents are ADK multi-agent specialists (`LlmAgent` with generic instructions, reasoning over evidence packs + `BusinessContext` at runtime). They are deployed to Vertex AI Agent Engine dynamically by `CourtManager.spawn_agent` or by `@deployer` via `./infra/deploy/deploy_agent_engine.py` once `@agents` passes verification. In-process fallback ensures detection and diagnosis function immediately even while engines scale up.

| Agent Archetype | Target Role | Reasoning Engine Deployment | Model | In-Process Fallback | Status |
| :-- | :-- | :-- | :-- | :-- | :-- |
| `application__<svc>` | Service runtime specialist | Vertex AI Reasoning Engine (`min_instances=0`) | `gemini-4-argon` @ global | Yes | Pending slice PASS & deploy |
| `data__<store>` | Data and state specialist | Vertex AI Reasoning Engine (`min_instances=0`) | `gemini-4-argon` @ global | Yes | Pending slice PASS & deploy |
| `business_line__<journey>` | Business journey specialist | Vertex AI Reasoning Engine (`min_instances=0`) | `gemini-4-argon` @ global | Yes | Pending slice PASS & deploy |
| `change__<repo>` | Software change specialist | Vertex AI Reasoning Engine (`min_instances=0`) | `gemini-4-argon` @ global | Yes | Pending slice PASS & deploy |
| `platform__<env>` | Cloud platform specialist | Vertex AI Reasoning Engine (`min_instances=0`) | `gemini-4-argon` @ global | Yes | Pending slice PASS & deploy |

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
```bash
# 1. Import pre-granted monitoring invoker IAM member into state
terraform -chdir=infra/terraform/platform import \
  'module.godview.google_cloud_run_v2_service_iam_member.sovereign_invokers["serviceAccount:service-769002985772@gcp-sa-monitoring-notification.iam.gserviceaccount.com"]' \
  'projects/general-ak/locations/us-central1/services/god-view-sovereign roles/run.invoker serviceAccount:service-769002985772@gcp-sa-monitoring-notification.iam.gserviceaccount.com'

# 2. Plan and apply platform updates
# NOTE: To avoid reconciling drifted tenant Cloud Run services, use targeted apply:
terraform -chdir=infra/terraform/platform plan -input=false \
  -target=module.project_iam \
  -target=module.data.random_password.pg_readonly \
  -target=module.data.google_secret_manager_secret.godview_pg_readonly_password \
  -target=module.data.google_secret_manager_secret_version.godview_pg_readonly_password \
  -target=module.data.google_secret_manager_secret_iam_member.pg_readonly_pass_sense \
  -target=module.data.google_sql_user.godview_readonly \
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
  -out=/usr/local/google/home/kambleashish/tmp/platform-v5.tfplan

terraform -chdir=infra/terraform/platform apply -input=false /usr/local/google/home/kambleashish/tmp/platform-v5.tfplan
terraform -chdir=infra/terraform/platform output -json > infra/terraform/platform/outputs.json
```

> [!IMPORTANT]
> **Key Architecture Decisions for Rev 6 Apply:**
> 1. **Password Rotation Dependency & Rollback Caveat**:
>    `google_sql_user.godview_readonly` and `google_cloud_run_v2_service.sovereign` explicitly depend on `google_secret_manager_secret_version.godview_pg_readonly_password` and the IAM `secretAccessor` binding. The secret version and IAM are created BEFORE the Cloud SQL user password rotates and BEFORE the Sovereign revision is created, eliminating the rotation race.
>    *Rollback Caveat*: Rolling back to a legacy revision (e.g. `god-view-sovereign-00006-mfk`) will fail DB authentication because legacy revisions expect the old plaintext password, whereas the Cloud SQL user is rotated. Any rollback must use a revision configured with `godview-pg-readonly-password:latest`.
> 2. **Agent Engine Identity**:
>    Reasoning engines run as `godview-sense-sa` (`ReasoningEngineSpec.service_account = godview-sense-sa@general-ak.iam.gserviceaccount.com`). The Reasoning Engine service agent (`serviceAccount:service-769002985772@gcp-sa-aiplatform-re.iam.gserviceaccount.com`) is granted `roles/iam.serviceAccountUser` on `godview-sense-sa`. The RE service agent is NOT added to `sovereign_invokers` because calls from engines authenticate as `godview-sense-sa` (already an authorized invoker).
> 3. **Act SA Scoped Hibernate Role**:
>    `godview-act-sa` is granted a custom project role `godview_hibernate_operator` containing only `compute.instances.{get,start,stop}`, `cloudsql.instances.{get,update}`, and `cloudscheduler.jobs.{get,pause,resume,run}` for the `god-view-hibernate-job` and operational remediation. Broad roles (`instanceAdmin.v1`, `cloudsql.editor`, `cloudscheduler.admin`, `run.developer`) are destroyed per plan §3.8.
> 4. **BigQuery Ledger Tables Replacement**:
>    `google_bigquery_table.events` and `google_bigquery_table.journeys` are recreated in this apply with the generic neutral schema (`entity_type`, `state_from`, `state_to`, `numeric_attributes`, `currency`, `total_value`). The tables currently hold 0 rows. Future schema changes to the ledger must be additive.
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
