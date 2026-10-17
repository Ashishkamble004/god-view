# Cymbal Auto Finance — Internal Bank LOS & Underwriting Portal (`internal-bank-los-web`)

IAP-protected bank operations, credit underwriting, KYC vault audit, multi-utility ledger, and read-only saga replay workstation (`https://los.cymbal.ak-demos.com`, URL 2).

## Architecture & Views

- **Hash-Routed Workstations**:
  - `#queue` — Dense Loan Origination System queue with sticky headers, sorting (`loan_amount_inr`, `foir_pct`, `cibil_score`, `emi_inr`, `total_latency_ms`, `created_at`), filtering (Status, Utility Type, Synthetic vs Human), keyboard navigation (`ArrowUp`/`ArrowDown`/`Enter`), and full 5-utility drawer inspector.
  - `#vault` — KYC Vault Inspector for `gs://general-ak-god-view-kyc-vault` specimen Aadhaar and PAN cards (`GET /api/v1/kyc-documents/{customer_id}/{doc_type}`) with explicit **Text-Only Model Armor Verdict Badge** explaining that images are never scanned by Model Armor.
  - `#ledgers` — Dedicated audit ledgers across Utility 2 (`underwriting_audits` with `rules_version` git SHA), Utility 3 (`commercial_credit_lines`), Utility 4 (`insurance_policies` + FASTag), and Utility 5 (`disbursements` + NACH e-Mandate + CBS UTR).
  - `#replay` — Read-Only Saga Replay Queue showing `PENDING`, `FAILED`, and `REPLAYED` disbursements (`biz.replay_disbursement` is L4 Trust-Gated via the Sovereign).
  - `#topology` — Tenant 5-utility service topology (`GET /api/v1/observability/topology`) and W3C distributed trace waterfall (`GET /api/v1/observability/traces/{trace_id}`).
- **IAP Identity Chip**: Calls `/api/v1/me` to surface `X-Goog-Authenticated-User-Email` (shows `"local"` when running outside IAP).

## Running Locally with MSW Mocks

```bash
npm install
npm run dev:mock
```

Open `http://127.0.0.1:5174/#queue` (or `#vault`, `#ledgers`, `#replay`, `#topology`).

## Verification & Build

```bash
npm run typecheck
npm test
npm run build
```
