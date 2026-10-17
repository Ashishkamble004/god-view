# `infra/` — Multi-Tier GCP Terraform Platform, GKE Workloads & Deployment Scripts

**Dedicated Documentation Folder**: [`../docs/infra/`](../docs/infra/README.md) ([System Design §2](../docs/SYSTEM_DESIGN.md) · [Terraform & GitOps Spec](../docs/TERRAFORM_GITOPS_SPEC.md) · [Deploy Runbook](../DEPLOY_RUNBOOK.md))

---

## Subdirectories inside `infra/`

| Subdirectory | Description |
| :--- | :--- |
| [`infra/terraform/platform/`](terraform/platform/) | 6-module OpenTofu/Terraform platform (`modules/01-foundation` through `06-operations`, `220` resources in `general-ak`, remote state in `gs://general-ak-god-view-tfstate`). |
| [`infra/terraform/workloads/`](terraform/workloads/) | GKE Autopilot (`god-view-gke-cluster`) Kubernetes resources in `namespace: cymbal` (`kyc-model-armor-svc` ILB `10.10.0.3`, `underwriting-los-svc` ILB `10.10.0.2`, Workload Identity, `NetworkPolicy`). |
| [`infra/api-gateway/`](api-gateway/) | OpenAPI 2.0 specification for Google Cloud API Gateway (`god-view-loan-api-gw`). |
| [`infra/deploy/`](deploy/) | Cloud Build pipeline (`build_and_push.sh`, `cloudbuild.yaml`), air-gapped GCE VM tarball deployer (`deploy_vm_payload.sh`), hibernation job (`hibernate/`), and live verification suite (`verify_live_gcp.sh`). |
| [`infra/tests/`](tests/) | Infrastructure contract and Terraform state verification tests. |
