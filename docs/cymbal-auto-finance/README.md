# Cymbal Auto Finance (Customer Application & LOS) — Documentation Hub

**Codebase Folder**: [`cymbal/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/cymbal) (`cymbal/services/*` & `cymbal/frontends/*`)
**Documentation Folder**: [`docs/cymbal-auto-finance/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/docs/cymbal-auto-finance)
**Live Customer Portal (URL 1)**: `https://cymbal.ak-demos.com` (`customer-loan-portal-web`)
**Live Internal Bank LOS Portal (URL 2)**: `https://los.cymbal.ak-demos.com` (`internal-bank-los-web`, IAP protected)
**Live Public API Gateway**: `https://cymbal.ak-demos.com/api/v1/*` (`god-view-loan-api-gw` $\rightarrow$ `loan-orchestrator-api`)

---

## 1. Overview of the Cymbal Auto Finance Application Plane

**Cymbal Auto Finance** is a 100% fictional Indian digital vehicle-financing platform located in [`cymbal/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/cymbal). It serves as the multi-tier enterprise proving ground monitored and self-healed by the [God-View v4.0 SRE Control Plane](../god-view/README.md).

### 1.1 Subdirectory Map (`cymbal/`)

| Component | Source Directory | Compute Substrate (`general-ak`) | Role in Loan Origination Journey |
| :--- | :--- | :--- | :--- |
| **Journey Coordinator API** | [`cymbal/services/loan-orchestrator-api/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/cymbal/services/loan-orchestrator-api) | Cloud Run v2 (`loan-orchestrator-api`) | Orchestrates the 5-utility vehicle loan saga, calculates reducing-balance EMIs, streams SSE telemetry, and exposes `/admin/v1/*` L4 remediation endpoints. |
| **Utility 1: KYC & Model Armor** | [`cymbal/services/kyc-model-armor-svc/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/cymbal/services/kyc-model-armor-svc) | GKE Autopilot (`10.10.0.3:8080`, `namespace: cymbal`) | Validates PAN/Aadhaar formats, checks PNG magic headers on specimen cards, and scans the 3 text fields via Vertex AI Model Armor (`cymbal-text-guard`). |
| **Utility 2: Underwriting & LOS** | [`cymbal/services/underwriting-los-svc/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/cymbal/services/underwriting-los-svc) | GKE Autopilot (`10.10.0.2:8080`, `namespace: cymbal`) | Evaluates deterministic underwriting rules (`cibil_score >= 680`, `foir_pct <= 60%`, `ltv_pct <= 90%`). |
| **Utility 3: Commercial Fleet Credit** | [`cymbal/services/commercial-fleet-credit-svc/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/cymbal/services/commercial-fleet-credit-svc) | Cloud Run v2 (`commercial-fleet-credit-svc`) | Evaluates working-capital credit lines for commercial fleet (`COMMERCIAL_FLEET`) and two-wheeler (`TWO_WHEELER`) borrowers. |
| **Utility 4: Motor Insurance & FASTag** | [`cymbal/services/motor-insurance-svc/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/cymbal/services/motor-insurance-svc) | Cloud Run v2 (`motor-insurance-svc`) | Issues 3-year comprehensive zero-depreciation motor insurance policies and links FASTag wallets. |
| **Utility 5: Core Banking CIBIL & CBS** | [`cymbal/services/core-banking-cibil-vm/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/cymbal/services/core-banking-cibil-vm) | GCE VM (`10.10.0.5:8085`, `us-central1-a`) | Generates deterministic persona-seeded CIBIL scores (`300–900`) and executes idempotent Core Banking Settlement (`CBS-UTR-2026-*`, `SANC-2026-*`, `NACH-2026-*`). |
| **AI Customer Stream Simulator** | [`cymbal/services/ai-customer-simulator/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/cymbal/services/ai-customer-simulator) | Cloud Run v2 (`ai-customer-simulator`) | Generates synthetic Indian borrower journeys (`gemini-2.5-flash` personas + `gemini-3.1-flash-image-preview` watermarked specimen KYC cards). |
| **URL 1: Customer Loan Portal** | [`cymbal/frontends/customer-loan-portal-web/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/cymbal/frontends/customer-loan-portal-web) | Cloud Run v2 (`customer-loan-portal-web`) | Public borrower web portal (`https://cymbal.ak-demos.com`). |
| **URL 2: Internal Bank LOS Portal** | [`cymbal/frontends/internal-bank-los-web/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/cymbal/frontends/internal-bank-los-web) | Cloud Run v2 (`internal-bank-los-web`) | IAP-protected bank operations & underwriting workbench (`https://los.cymbal.ak-demos.com`). |

---

## 2. Documents in `docs/cymbal-auto-finance/`

1. **[`HLD.md` — Cymbal Auto Finance High-Level Design](HLD.md)**
2. **[`LLD.md` — Cymbal Auto Finance Low-Level Design (Microservices, Schemas & Sagas)](LLD.md)**
3. **[`SYSTEM_DESIGN.md` — Cymbal Auto Finance System Design, Idempotency & Chaos Drills](SYSTEM_DESIGN.md)**
4. **[`../BRD.md` — Business Requirements Document](../BRD.md)**
5. **[`../SDD.md` — Software Design Document](../SDD.md)**
