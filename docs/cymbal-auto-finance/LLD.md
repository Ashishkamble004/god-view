# Cymbal Auto Finance — Low-Level Design (LLD)

**Plane**: Customer Application & 5-Utility Digital Vehicle Loan Origination Tenant (`cymbal/`)
**Database**: Cloud SQL PostgreSQL 16 (`general-ak:us-central1:god-view-pg`, `10.186.0.3`, database `godview_lms`)
**Related Docs**: [Cymbal Hub](README.md) | [Cymbal HLD](HLD.md) | [Cymbal System Design](SYSTEM_DESIGN.md) | [Master LLD](../LLD.md)

---

## 1. Financial Formulas & Underwriting Rules

### 1.1 Reducing-Balance Monthly EMI (`cymbal/services/loan-orchestrator-api/app/main.py`)

Implemented in `calculate_emi_inr(principal_inr, annual_rate_pct, tenure_months, emi_rounding_bug=False)` using 28-digit `Decimal` precision:

- Monthly interest rate: $r = \frac{\text{annual\_rate\_pct}}{12 \times 100} = \frac{\text{annual\_rate\_pct}}{1200}$
- Exact reducing-balance EMI formula:
  $$\text{EMI} = \text{round\_half\_up}\left(\frac{P \cdot r \cdot (1 + r)^n}{(1 + r)^n - 1}, \text{whole ₹}\right)$$
- **Invariant `inv-emi-matches-formula`**: Every row in `loan_applications` must satisfy $|\text{emi\_inr} - \text{EMI}_{\text{formula}}| \le ₹1.00$.
- **Chaos Fault Hook (`emi_rounding`, `UC-06`)**: Truncates $r$ prematurely to 2 decimal places (`0.01`) and adds a `+₹250` bias, triggering `inv-emi-matches-formula`.

### 1.2 Underwriting Rules (`cymbal/services/underwriting-los-svc/app/rules_engine.py`)

Implemented in `evaluate_underwriting_rules()` (`RULES_VERSION = "v4.0.0"`):
- **Fixed Obligation to Income Ratio (`FOIR`)**:
  $$\text{foir\_pct} = \frac{\text{existing\_emi\_inr} + \text{emi\_inr}}{\text{monthly\_income\_inr}} \times 100 \le 60.00\%$$
- **Loan-to-Value Ratio (`LTV`)**:
  $$\text{ltv\_pct} = \frac{\text{loan\_amount\_inr}}{\text{ex\_showroom\_inr}} \times 100 \le 90.00\%$$
- **CIBIL Bureau Score**: $\text{cibil\_score} \in [300, 900]$ and $\text{cibil\_score} \ge 680$.

---

## 2. Microservice REST Schemas & Admin Endpoints

### 2.1 `loan-orchestrator-api` (`cymbal/services/loan-orchestrator-api/app/main.py`)

| Endpoint | Request / Query Parameters | Response Model | Notes |
| :--- | :--- | :--- | :--- |
| `GET /v1/vehicles` (`/api/v1/vehicles`) | None | `list[VehicleRow]` | Returns the 8 seeded Cymbal vehicles; rewrites relative SVG URLs to Cloud CDN when `CDN_BASE_URL` is set. |
| `GET /v1/me` (`/api/v1/me`) | Reads `X-Goog-Authenticated-User-Email` | `ApplicantMeResponse` | Returns IAP user identity on URL 2 or `anonymous` on URL 1. |
| `POST /v1/loans/apply` (`/api/v1/loans/apply`) | `LoanApplyRequest` | `LoanJourneyDetailResponse` | Executes Steps 1–5 synchronously, emits W3C OTel spans + SSE step events, and persists all utility rows. |
| `GET /v1/loans` (`/api/v1/loans`) | `status`, `utility_type`, `synthetic`, `limit`, `offset` | `LoanListResponse` | Paginated loan dossier list for URL 2 Internal LOS Queue. |
| `GET /v1/loans/{loan_id}` | `loan_id: UUID` | `LoanJourneyDetailResponse` | Full 5-utility dossier for a single loan. |
| `GET /v1/kyc-documents/{customer_id}/{doc_type}` | `customer_id: UUID`, `doc_type: pan\|aadhaar` | `KycSignedDocumentResponse` | Returns 15-minute V4 signed GCS URL for `IMAGE` mode journeys; returns `404` for `TEXT_ONLY`. |
| `GET /v1/observability/topology` | None | `ObservabilityTopologyResponse` | Live 5-utility nodes, edges, golden signals, and active chaos faults. |
| `GET /v1/observability/traces/{trace_id}` | `trace_id: str` | `ObservabilityTraceResponse` | Correlated W3C spans + DFG conformance (`CONFORMANT`, `REORDERED`, `SKIPPED`, `STALLED`). |
| `GET /v1/telemetry/stream` | `journey_id`, `loan_id`, `max_events` | `text/event-stream` (SSE) | Streams `event: step` and 15 s `event: heartbeat` frames; scoped by `journey_id` on URL 1. |
| `POST /admin/v1/disbursements/{loan_id}:replay` | `loan_id: UUID` | `DisbursementReplayResponse` | `L4` idempotent CBS disbursement replay (`x-godview-admin-action: biz.replay_disbursement`). |
| `POST /admin/v1/loans/{loan_id}:re-evaluate` | `loan_id: UUID` | `LoanReevaluateResponse` | `L4` underwriting re-evaluation (`x-godview-admin-action: biz.reevaluate_application`). |
| `POST /admin/v1/journeys/{journey_id}:compensate` | `journey_id: str` | `JourneyCompensateResponse` | `L4` compensating saga (`x-godview-admin-action: biz.compensate_journey`). |
| `GET /admin/v1/disbursements` | `status=PENDING\|FAILED` | `StuckDisbursementsResponse` | `L0` probe-safe query for stuck disbursements. |
| `POST /admin/v1/tenant/pause` & `/resume` | `TenantStateRequest` | `TenantStateResponse` | Pauses/resumes tenant traffic (`503 TENANT_PAUSED`). |
| `POST /admin/v1/chaos/faults` & `DELETE /{fault_id}` | `ChaosFaultActivateRequest` | `ChaosFaultStateRow` | Activates or clears opt-in chaos faults on `godview_managed="true"` services. |
