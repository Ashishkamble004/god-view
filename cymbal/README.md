# `cymbal/` — Cymbal Auto Finance Customer Application & 5-Utility Banking Tenant

**Dedicated Documentation Folder**: [`../docs/cymbal-auto-finance/`](../docs/cymbal-auto-finance/README.md) ([HLD](../docs/cymbal-auto-finance/HLD.md) · [LLD](../docs/cymbal-auto-finance/LLD.md) · [System Design](../docs/cymbal-auto-finance/SYSTEM_DESIGN.md))

---

## Subdirectories inside `cymbal/`

| Subdirectory | Compute Tier (`general-ak`) | Description |
| :--- | :--- | :--- |
| [`cymbal/services/loan-orchestrator-api/`](services/loan-orchestrator-api/) | Cloud Run v2 | 5-Utility Loan Origination Saga Coordinator (`/v1/*`, `/api/v1/*`, `/admin/v1/*`). |
| [`cymbal/services/kyc-model-armor-svc/`](services/kyc-model-armor-svc/) | GKE Autopilot (`10.10.0.3:8080`) | **Utility 1**: PAN/Aadhaar verification, PNG magic-header check, and Text-Only Model Armor (`cymbal-text-guard`) prompt-injection scanner. |
| [`cymbal/services/underwriting-los-svc/`](services/underwriting-los-svc/) | GKE Autopilot (`10.10.0.2:8080`) | **Utility 2**: Deterministic EMI, FOIR ($\le 60\%$), LTV ($\le 90\%$), and CIBIL ($\ge 680$) underwriting engine. |
| [`cymbal/services/commercial-fleet-credit-svc/`](services/commercial-fleet-credit-svc/) | Cloud Run v2 | **Utility 3**: Commercial fleet (`COMMERCIAL_FLEET`) and two-wheeler (`TWO_WHEELER`) credit line evaluation. |
| [`cymbal/services/motor-insurance-svc/`](services/motor-insurance-svc/) | Cloud Run v2 | **Utility 4**: Comprehensive motor insurance policy issuance and FASTag wallet linkage. |
| [`cymbal/services/core-banking-cibil-vm/`](services/core-banking-cibil-vm/) | GCE VM (`10.10.0.5:8085`) | **Utility 5**: Persona-seeded CIBIL bureau scoring (`300–900`) and idempotent Core Banking Settlement (`CBS-UTR-2026-*`). |
| [`cymbal/services/ai-customer-simulator/`](services/ai-customer-simulator/) | Cloud Run v2 | Synthetic Indian borrower stream (`gemini-2.5-flash` personas + `gemini-3.1-flash-image-preview` watermarked specimen cards). |
| [`cymbal/frontends/customer-loan-portal-web/`](frontends/customer-loan-portal-web/) | Cloud Run v2 (`cymbal.ak-demos.com`) | **URL 1**: Public Customer Vehicle Showroom, EMI Calculator & 5-Step Loan Journey Wizard. |
| [`cymbal/frontends/internal-bank-los-web/`](frontends/internal-bank-los-web/) | Cloud Run v2 (`los.cymbal.ak-demos.com`) | **URL 2**: IAP-Protected Internal Bank Underwriting Queue, KYC Vault Inspector, Multi-Utility Ledgers & Saga Replay Workbench. |
