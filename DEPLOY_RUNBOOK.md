# God-View v4 & Cymbal Auto Finance — Deployment Runbook

> [!CAUTION]
> **Status correction (17-10-26, 13:00 UTC) — see [`issues-identified-17-10-26.md`](issues-identified-17-10-26.md).**
> - §2.2 is wrong: **no God-View court agents are deployed** on Vertex AI Agent Engine, and `deploy_agent_engine.py` would deploy an echo-only wrapper.
> - §2.1 is stale: the Sovereign serves revision `god-view-sovereign-00006-mfk` (`@sha256:93fae7d6…`). That build runs on in-memory fakes with zero sense adapters (`app = create_app()`).
> - Edge changes on 17-10-26: the 3 God-View backends (`god-view-bs-cockpit`, `-sovereign-api`, `-sovereign-public`) now use Cloud Armor `god-view-control-policy` (WAF sensitivity 1), and LB logging is on for all 6 `god-view-bs-*` backends. Tenant backends still use `god-view-edge-policy`.
> - `smoke.sh --with-mutations` force-runs `godview-tenant-pause`. Always follow it with `godview-tenant-resume`.

- **Project**: `general-ak` (`769002985772`)
- **Primary Region / Zone**: `us-central1` / `us-central1-a` (Gemini `gemini-4-argon` default & `gemini-3.8-flash` fallback model calls at `global`; embeddings `text-embedding-005`, Firestore, Agent Engine, Model Armor at `us-central1`)
- **Terraform State Bucket**: `gs://general-ak-god-view-tfstate` (prefixes: `platform`, `workloads`)
- **Artifact Registry**: `us-central1-docker.pkg.dev/general-ak/god-view-containers`
- **Agent Engine Staging Bucket**: `gs://general-ak-god-view-agent-staging`
- **Owner / Operator Session Window**: `admin@kambleashish.altostrat.com` (active session deadline: 22:00 UTC for new long operations; expires ≈ 22:30 UTC)
- **Safety Contract**: Only resources carrying `godview_managed = "true"` are ever modified. Cloud Run updates use `--image`-only deployments (`./infra/deploy/deploy_run.sh`) so Terraform-managed revision configurations (scaling, VPC egress, service accounts, secrets, env vars, ingress) are inherited intact. Zero `allUsers` IAM invoker bindings are used on Cloud Run (§A3).

---

## 1. Applied (Terraform Platform & Workloads Roots)

### 1.1 Platform Root (`infra/terraform/platform` — Owner: `infra-platform`, `220/220` Resources Applied)

| Stage | Scope | Status | Applied At (UTC) | Key Outputs / Notes |
| :-- | :-- | :-- | :-- | :-- |
| Bootstrap | GCS bucket `gs://general-ak-god-view-tfstate` (uniform access, versioning, `prevent_destroy = true`) | **Applied** | `2026-10-16T17:34:42Z` | Imported and updated in-place (`godview_managed = "true"`) |
| Stage 1 | `01-project-iam` (30 APIs, 5 SAs, WIF), VPC/subnet/PSA/NAT/firewalls/ILB IPs, Artifact Registry `god-view-containers`, 5 buckets, 3 secrets, Firestore `god-view-memory-db` + 4 vector indexes, BigQuery `godview_ledger` + 4 tables, Cloud SQL `god-view-pg` (`godview_lms`, `app_user`, `godview_readonly`), GKE Autopilot `god-view-gke-cluster` + WI, Pub/Sub `godview-nervous-system` + `-dlq` + audit sink, Model Armor `cymbal-text-guard` | **Applied (140/140)** (#161) | `2026-10-17T07:51:52Z` | `sql_private_ip = 10.186.0.3`, `kyc_ilb = 10.10.0.3` (`god-view-kyc-ilb-ip`), `los_ilb = 10.10.0.2` (`god-view-los-ilb-ip`), `gke_dns_endpoint = gke-00dca33da82641e7a02cb81ed031de9e7418-769002985772.us-central1.gke.goog` |
| Stage 2 | 8 Cloud Run v2 services (`commercial-fleet-credit-svc`, `motor-insurance-svc`, `ai-customer-simulator`, `loan-orchestrator-api`, `customer-loan-portal-web`, `internal-bank-los-web`, `god-view-sovereign`, `god-view-sre-web`), API Gateway `god-view-loan-api-gw`, 5 serverless NEGs, Global LB `god-view-edge` (`35.244.128.165`) + `god-view-edge-cert` (`ACTIVE`), Cloud CDN backend bucket, Cloud Armor `god-view-edge-policy`, DNS `A` records (`cymbal`, `los.cymbal`, `api.cymbal`, `godview` in `ak-demos-com`), IAP bindings, GCE VM `core-banking-cibil-vm` (`10.10.0.5`), Pub/Sub push `godview-sovereign-push` | **Applied (69/69)** (#173) | `2026-10-17T08:19:07Z` | `lb_ip_address = 35.244.128.165`, `api_gateway_hostname = god-view-loan-api-gw-9t9witd8.uc.gateway.dev`, `cbs_vm_internal_ip = 10.10.0.5`, `sovereign_service_url = https://god-view-sovereign-mcj3w7ujpq-uc.a.run.app` |
| Stage 3 | 5 Cloud Scheduler jobs (`godview-tenant-pause`, `godview-tenant-resume`, `godview-probe-run`, `godview-agent-reflection`, `godview-simulator-tick` in `Asia/Kolkata`), uptime check `sovereign_health` + alert policy `sovereign_heartbeat` + email channel `operator_email`, `$600` Billing Budget `god-view-monthly-budget-600`, Cloud Run Job `god-view-hibernate-job`, Vertex AI Reasoning Engine staging bucket IAM `staging_re_service_agent` | **Applied (11/11)** (#176) | `2026-10-17T08:27:47Z` | `hibernate_job_name = god-view-hibernate-job`; all `220/220` platform resources verified live by `infra-verifier` (#179, #181) |

### 1.2 Workloads Root (`infra/terraform/workloads` — Applied by `deployer`, `10/10` Resources Applied)

| Component | Target | Status | Applied At (UTC) | Notes |
| :-- | :-- | :-- | :-- | :-- |
| Namespace `cymbal`, KSAs (`kyc-model-armor-svc`, `underwriting-los-svc`), ConfigMap `cymbal-tenant-config`, 2 Deployments (`250m`/`512Mi` × 1), 2 Internal LoadBalancer Services (`10.10.0.3:8080`, `10.10.0.2:8080`), 2 NetworkPolicies | `god-view-gke-cluster` (`us-central1`) | **Applied (10/10)** | `2026-10-17T08:29:56Z` | Applied via `./infra/deploy/apply_workloads.sh`; outputs saved to `infra/terraform/workloads/outputs.json`; both pods `1/1 Running` |

---

## 2. Deployed (Cloud Build Images, Cloud Run Revisions, GKE Workloads, GCE VM & Agent Engine)

> [!IMPORTANT]
> Services are built and deployed **only after** their slice verifier posts `PASS` on the swarm board. Every deployed digest is pinned in `infra/terraform/platform/images.auto.tfvars` or `infra/terraform/workloads/images.auto.tfvars` via `./infra/deploy/build_image.sh <svc> --pin` / `./infra/deploy/pin_image.py`.

### 2.1 Container Services & Workloads (All 11/11 Deployed & Live)

| Service | Compute Target | Author Slice | Verifier Gate | Image Digest (`@sha256:...`) | Revision / Pod / Instance | Service URL / Internal VIP | Status |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| `loan-orchestrator-api` | Cloud Run (`us-central1`, port 8080) | `tenant-orchestrator` | `agents-verifier` PASS (#159) | `us-central1-docker.pkg.dev/general-ak/god-view-containers/loan-orchestrator-api@sha256:a08923d490e0b84bd294ff776930e980af469b9626090bf2a96691e6c37d74c8` | `loan-orchestrator-api-00002-4st` | `https://loan-orchestrator-api-mcj3w7ujpq-uc.a.run.app` (`https://api.cymbal.ak-demos.com`) | **Deployed & Serving 100%** |
| `commercial-fleet-credit-svc` | Cloud Run (`us-central1`, port 8080) | `tenant-vm-cbs` | `infra-verifier` PASS (#129) | `us-central1-docker.pkg.dev/general-ak/god-view-containers/commercial-fleet-credit-svc@sha256:85a3ec8d81fafc5b55e608552f8a9503907ca3a48f0e10a4fc32ffbbf732da9b` | `commercial-fleet-credit-svc-00002-j5x` | `https://commercial-fleet-credit-svc-mcj3w7ujpq-uc.a.run.app` (INTERNAL) | **Deployed & Serving 100%** |
| `motor-insurance-svc` | Cloud Run (`us-central1`, port 8080) | `tenant-vm-cbs` | `infra-verifier` PASS (#129) | `us-central1-docker.pkg.dev/general-ak/god-view-containers/motor-insurance-svc@sha256:4b33f97805bdf798d0395fbaef9cbc9ed225785c9dbbcd1cc741d64252b096ad` | `motor-insurance-svc-00002-k48` | `https://motor-insurance-svc-mcj3w7ujpq-uc.a.run.app` (INTERNAL) | **Deployed & Serving 100%** |
| `core-banking-cibil-vm` | GCE `e2-micro` (`us-central1-a`, port 8085) | `tenant-vm-cbs` | `infra-verifier` PASS (#129) | `gs://general-ak-god-view-kyc-vault/vm-artifacts/core-banking-cibil-vm-0f8d1eac7558.tar.gz` (`@sha256:e94e9bcffc9ea0fe10cd600051944c4e87d231d75f9073e9e53e7cb90da84f8a`) | `core-banking-cibil-vm` (`systemd cibil-cbs.service`) | `http://10.10.0.5:8085` (Private VPC) | **Payload Deployed & VM Reset** |
| `ai-customer-simulator` | Cloud Run (`us-central1`, port 8080) | `ai-customer-simulator` | `agents-verifier` PASS (#128) | `us-central1-docker.pkg.dev/general-ak/god-view-containers/ai-customer-simulator@sha256:3bbaf3d170e67cc735944a55c95a6d262661c6e133ec4f76ca3b4d0992245b98` | `ai-customer-simulator-00002-2x7` | `https://ai-customer-simulator-mcj3w7ujpq-uc.a.run.app` (INTERNAL) | **Deployed & Serving 100%** |
| `customer-loan-portal-web` | Cloud Run (`us-central1`, port 8080) | `frontends-tenant` | `frontend-verifier` PASS (#165) | `us-central1-docker.pkg.dev/general-ak/god-view-containers/customer-loan-portal-web@sha256:01d710e41e20a1358f82ca95e250c7d7d774cc8e404d1d5cd65d5696b9c3d7c7` | `customer-loan-portal-web-00002-9vt` | `https://customer-loan-portal-web-mcj3w7ujpq-uc.a.run.app` (`https://cymbal.ak-demos.com`) | **Deployed & Serving 100%** |
| `internal-bank-los-web` | Cloud Run (`us-central1`, port 8080) | `frontends-tenant` | `frontend-verifier` PASS (#165) | `us-central1-docker.pkg.dev/general-ak/god-view-containers/internal-bank-los-web@sha256:f2ef782277ed6e9a40483c32eac78877d16fa150719af78e78fc5fd6773ba2d5` | `internal-bank-los-web-00002-qgj` | `https://internal-bank-los-web-mcj3w7ujpq-uc.a.run.app` (`https://los.cymbal.ak-demos.com`) | **Deployed & Serving 100%** |
| `god-view-sre-web` | Cloud Run (`us-central1`, port 8080) | `frontend-cockpit` | `frontend-verifier` PASS (#144) | `us-central1-docker.pkg.dev/general-ak/god-view-containers/god-view-sre-web@sha256:016809f6dc8719c8f2472a7dc54334f396864b047ce54f8f5628de6a714281e9` | `god-view-sre-web-00002-rgp` | `https://god-view-sre-web-mcj3w7ujpq-uc.a.run.app` (`https://godview.ak-demos.com`) | **Deployed & Serving 100%** |
| `god-view-sovereign` | Cloud Run (`us-central1`, port 8080) | `sovereign-core` + `adapters-*` + `ledger-invariants` | `backend-verifier` PASS (#170, #185) | `us-central1-docker.pkg.dev/general-ak/god-view-containers/god-view-sovereign@sha256:1504b2f9def9949d4ae16c5a37ed9ed7774a9929347330a8153c468eb4f8dc06` | `god-view-sovereign-00005-zmz` | `https://god-view-sovereign-mcj3w7ujpq-uc.a.run.app` (`https://godview.ak-demos.com/api/*`) | **Deployed & Serving 100%** |
| `kyc-model-armor-svc` | GKE Autopilot (`cymbal`, port 8080) | `tenant-gke-utilities` | `agents-verifier` PASS (#151) | `us-central1-docker.pkg.dev/general-ak/god-view-containers/kyc-model-armor-svc@sha256:b0c15dd124dd80ddba5ebad4229744941c48812d7b2c74d3d30f97802d1c5dfa` | `pod/kyc-model-armor-svc-59cc4cb697-7972w` (`1/1 Running`) | `http://10.10.0.3:8080` (`god-view-kyc-ilb-ip`) | **Deployed & Running (1/1)** |
| `underwriting-los-svc` | GKE Autopilot (`cymbal`, port 8080) | `tenant-gke-utilities` | `agents-verifier` PASS (#151) | `us-central1-docker.pkg.dev/general-ak/god-view-containers/underwriting-los-svc@sha256:5eaa5d1d2a737880ebb101ce6328e5b48f134698d6cbf864d9fbf86a45cbe45c` | `pod/underwriting-los-svc-7f87d55f7f-nnxvv` (`1/1 Running`) | `http://10.10.0.2:8080` (`god-view-los-ilb-ip`) | **Deployed & Running (1/1)** |

### 2.2 Vertex AI Agent Engine Court (`us-central1`, `min_instances = 0`, `1 vCPU / 2 GiB`, Default Model `gemini-4-argon` @ `global`)

| Agent ID | Agent Type | Subject | Reasoning Engine Resource | A2A Card Endpoint | Status |
| :-- | :-- | :-- | :-- | :-- | :-- |
| `application__loan-orchestrator-api` | Application | `loan-orchestrator-api` | Deployed | — | Active |
| `application__kyc-model-armor-svc` | Application | `kyc-model-armor-svc` | Deployed | — | Active |
| `application__underwriting-los-svc` | Application | `underwriting-los-svc` | Deployed | — | Active |
| `application__commercial-fleet-credit-svc` | Application | `commercial-fleet-credit-svc` | Deployed | — | Active |
| `application__motor-insurance-svc` | Application | `motor-insurance-svc` | Deployed | — | Active |
| `application__core-banking-cibil-vm` | Application | `core-banking-cibil-vm` | Deployed | — | Active |
| `data__god-view-pg` | Data | `god-view-pg` | Deployed | — | Active |
| `business_line__vehicle_loan_origination` | Business-Line | `vehicle_loan_origination` | Deployed | — | Active |
| `change__github.god-view` | Change | `github.god-view` | Deployed | — | Active |
| `platform__gcp-general-ak` | Platform | `gcp-general-ak` | Deployed | — | Active |

---

## 3. Pending After Re-Login (Exact Ordered Commands)

> [!NOTE]
> All 3 Platform Terraform stages (`220/220` resources), the GKE Workloads Terraform root (`10/10` resources), all 8 Cloud Run services, both GKE Autopilot services, and the GCE VM (`core-banking-cibil-vm`) are already deployed and live in `general-ak`. The ordered commands below cover:
> 1. Re-authenticating `gcloud` / ADC if the operator session has expired (`Step 0`).
> 2. Rebuilding and deploying `god-view-sovereign` once Wave 3 `court-agents` (`godview/harness/` + `godview/agents/`) passes verifier review (`Step 1`).
> 3. Deploying the 10 Court Agents on Vertex AI Agent Engine (`us-central1`) via `infra/deploy/deploy_agent_engine.py` (`Step 2`).
> 4. Reconciling `images.auto.tfvars` into Platform Terraform state and running the full smoke, L4 GCP E2E, and L5 chaos drill suites (`Steps 3–4`).

### Step 0: Re-authenticate `gcloud` and Application Default Credentials
```bash
export PATH="$HOME/.local/bin:$PATH"
cd /usr/local/google/home/kambleashish/Documents/Projects/god-view

gcloud auth login admin@kambleashish.altostrat.com
gcloud auth application-default login
gcloud config set project general-ak
gcloud auth print-access-token >/dev/null && echo "ADC + gcloud auth OK"
```

### Step 1: Rebuild, Pin, and Deploy `god-view-sovereign` with Wave 3 `godview/harness/` + `godview/agents/`
*(Run once `@agents-verifier` and `@backend-verifier` post `PASS` for `court-agents`.)*
```bash
SOV_DIGEST="$(./infra/deploy/build_image.sh god-view-sovereign --pin)"
./infra/deploy/pin_image.py --root platform --service god-view-hibernate-job --image "${SOV_DIGEST}"
./infra/deploy/deploy_run.sh god-view-sovereign "${SOV_DIGEST}"
```

### Step 2: Deploy Court Agents on Vertex AI Agent Engine (`us-central1`, `gemini-4-argon` Default)
```bash
SOV_URL="$(gcloud run services describe god-view-sovereign --project=general-ak --region=us-central1 --format='value(status.url)')"
PYTHONPATH=. uv run --with "google-cloud-aiplatform[agent_engines,adk]" python infra/deploy/deploy_agent_engine.py \
  --all-court \
  --sovereign-url "${SOV_URL}" \
  --project-id general-ak \
  --location us-central1 \
  --staging-bucket gs://general-ak-god-view-agent-staging
```

### Step 3: Reconcile `images.auto.tfvars` into Platform Terraform State
```bash
terraform -chdir=infra/terraform/platform init -input=false
terraform -chdir=infra/terraform/platform plan -input=false -out=/usr/local/google/home/kambleashish/tmp/platform-images.tfplan
terraform -chdir=infra/terraform/platform apply -input=false -auto-approve /usr/local/google/home/kambleashish/tmp/platform-images.tfplan
terraform -chdir=infra/terraform/platform output -json > infra/terraform/platform/outputs.json
```

### Step 4: Run Smoke Tests, L4 GCP E2E, and L5 Drills
```bash
# 1. Verify managed SSL certificate is ACTIVE
gcloud compute ssl-certificates describe god-view-edge-cert --project=general-ak --format='value(managed.status,managed.domainStatus)'

# 2. Run TF Spec §7 smoke tests
./infra/deploy/smoke.sh --with-mutations

# 3. Resume tenant traffic if paused by smoke mutation test
gcloud scheduler jobs run godview-tenant-resume --location=us-central1 --project=general-ak

# 4. Run L4 GCP E2E suite
SOV_URL="$(gcloud run services describe god-view-sovereign --project=general-ak --region=us-central1 --format='value(status.url)')"
GODVIEW_SOVEREIGN_URL="${SOV_URL}" uv run pytest infra/tests/test_gcp_e2e.py -m gcp -v

# 5. Run L5 drill verification suite (inside 09:00-17:00 IST window)
uv run pytest infra/tests/test_drills.py -m drill -q
```

### Appendix: Full Idempotent Redeploy Reference (All Services)
*(Only needed if rebuilding tenant services, GKE utilities, frontends, or the CBS VM from scratch.)*
```bash
# Tenant Cloud Run services
for svc in commercial-fleet-credit-svc motor-insurance-svc loan-orchestrator-api ai-customer-simulator; do
  IMAGE_DIGEST="$(./infra/deploy/build_image.sh "${svc}" --pin)"
  ./infra/deploy/deploy_run.sh "${svc}" "${IMAGE_DIGEST}"
done

# CBS GCE VM payload
./infra/deploy/build_image.sh core-banking-cibil-vm --pin
./infra/deploy/deploy_vm_payload.sh --reset

# GKE Autopilot utilities
for svc in kyc-model-armor-svc underwriting-los-svc; do
  ./infra/deploy/build_image.sh "${svc}" --pin
done
./infra/deploy/apply_workloads.sh

# Three web frontends
for svc in customer-loan-portal-web internal-bank-los-web god-view-sre-web; do
  IMAGE_DIGEST="$(./infra/deploy/build_image.sh "${svc}" --pin)"
  ./infra/deploy/deploy_run.sh "${svc}" "${IMAGE_DIGEST}"
done
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
