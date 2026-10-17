# Multi-Tier GCP Infrastructure, Terraform & Deployment (`general-ak`) — Documentation Hub

**Codebase Folders**: [`infra/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/infra) & [`local-env/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/local-env)
**Documentation Folder**: [`docs/infra/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/docs/infra)
**Target GCP Project**: `general-ak` (`769002985772`, `us-central1` / `us-central1-a`)
**Remote State Bucket**: `gs://general-ak-god-view-tfstate` (`220` Platform resources + `10` GKE Workload resources)

---

## 1. Infrastructure Subdirectory Map (`infra/` & `local-env/`)

| Path | Purpose & Contents |
| :--- | :--- |
| [`infra/terraform/platform/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/infra/terraform/platform) | 6-module Terraform root (`modules/01-foundation`, `02-data`, `03-compute`, `04-cloud-run`, `05-edge`, `06-operations`) + live [`outputs.json`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/infra/terraform/platform/outputs.json) (`220` resources). |
| [`infra/terraform/workloads/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/infra/terraform/workloads) | Kubernetes Terraform stack for GKE Autopilot `god-view-gke-cluster` (`namespace: cymbal`, Workload Identity `cymbal-runtime-ksa`, `NetworkPolicy`, `kyc-model-armor-svc` ILB `10.10.0.3`, `underwriting-los-svc` ILB `10.10.0.2`). |
| [`infra/api-gateway/openapi-spec.yaml`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/infra/api-gateway/openapi-spec.yaml) | OpenAPI 2.0 specification deployed to Google Cloud API Gateway (`god-view-loan-api-gw-9t9witd8.uc.gateway.dev`). |
| [`infra/deploy/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/infra/deploy) | `build_and_push.sh`, `cloudbuild.yaml`, `deploy_vm_payload.sh` (air-gapped wheel tarball deployer for `core-banking-cibil-vm`), `verify_live_gcp.sh`, and `hibernate/` job container. |
| [`local-env/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/local-env) & [`docker-compose.yml`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/docker-compose.yml) | Hermetic offline local stack: PostgreSQL 16 seed SQL (`01_roles.sql`, `02_schema_and_seed.sql`), Nginx edge router, and Prometheus config. |

---

## 2. Detailed Infrastructure Specifications

- **[`../SYSTEM_DESIGN.md` (§2: 3-Stage Terraform Platform, GKE Workloads & Deployment Pipeline)](../SYSTEM_DESIGN.md)**
- **[`../TERRAFORM_GITOPS_SPEC.md` — Terraform & GitOps Specification](../TERRAFORM_GITOPS_SPEC.md)**
- **[`../../DEPLOY_RUNBOOK.md` — Live `general-ak` Deployment Runbook & Verification Receipts](../../DEPLOY_RUNBOOK.md)**
