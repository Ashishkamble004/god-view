# Cymbal Auto Finance — Customer Loan Portal (`customer-loan-portal-web`)

Public customer-facing vehicle showroom, EMI/LTV/FOIR financial calculator, and 5-utility origination wizard (`https://cymbal.ak-demos.com`, URL 1).

## Architecture & Invariants

- **Same-origin API routing (`§A18`)**: Calls relative `/api/v1/*` routes only (`GET /api/v1/vehicles`, `POST /api/v1/loans/apply`, `GET /api/v1/loans`, `GET /api/v1/loans/{loan_id}`, and SSE `GET /api/v1/telemetry/stream`).
- **5-Utility Journey Rail**:
  1. **Utility 1 — Digital KYC & Guardrail (`kyc-model-armor-svc`)**: Supports `TEXT_ONLY` or watermarked `IMAGE` specimen mode. Text fields (`loan_purpose_text`, `employment_remarks`, `applicant_notes`) are scanned by Model Armor (`cymbal-text-guard`); specimen images are never scanned.
  2. **Utility 2 — Retail Passenger & EV Loan LOS (`underwriting-los-svc`)**: Evaluates FOIR (`<= 60%`), LTV, and bureau score (`>= 680`).
  3. **Utility 3 — Commercial & Two-Wheeler Fleet Credit (`commercial-fleet-credit-svc`)**: Working-capital fleet credit lines for `COMMERCIAL_FLEET_TOPUP` journeys.
  4. **Utility 4 — Motor Insurance & FASTag Add-On (`motor-insurance-svc`)**: Comprehensive zero-depreciation cover and FASTag wallet linkage.
  5. **Utility 5 — Credit Bureau, NACH e-Mandate & CBS Settlement (`core-banking-cibil-vm`)**: Bureau pull, sanction letter, NACH mandate, and NEFT/RTGS dealer disbursement with UTR.
- **No public mutation controls (`I-10`)**: Simulator and chaos drill controls live exclusively in the IAP-protected God-View Cockpit.

## Running Locally with MSW Mocks

```bash
npm install
npm run dev:mock
```

Open `http://127.0.0.1:5173/#showroom` (or `#calculator`, `#journey`, `#applications`).

## Verification & Build

```bash
npm run typecheck
npm test
npm run build
```
