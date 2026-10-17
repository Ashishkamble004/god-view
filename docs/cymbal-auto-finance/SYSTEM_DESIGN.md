# Cymbal Auto Finance — System Design, Saga Recovery & Chaos Drills

**Plane**: Customer Application & 5-Utility Digital Vehicle Loan Origination Tenant (`cymbal/`)
**Related Docs**: [Cymbal Hub](README.md) | [Cymbal HLD](HLD.md) | [Cymbal LLD](LLD.md) | [Master System Design](../SYSTEM_DESIGN.md)

---

## 1. End-to-End Saga Recovery & Idempotency Guarantees

1. **Idempotent CBS UTR Settlement (`core-banking-cibil-vm`)**:
   - Every disbursement is keyed by `loan_id` in `disbursements`.
   - Deterministic reference generators (`SHA-256` over namespace + `loan_id`):
     - Sanction Letter: `SANC-2026-<6_digits>`
     - NACH Mandate: `NACH-2026-<6_digits>`
     - CBS UTR Number: `CBS-UTR-2026-<6_digits>`
   - Re-invoking `POST /internal/cibil-cbs/disburse` or `POST /admin/v1/disbursements/{loan_id}:replay` for an already settled loan returns the existing UTR with `idempotent_replay: true` without creating duplicate ledger rows.
2. **Multi-Worker Cloud SQL Schema Safety (`loan-orchestrator-api`)**:
   - Uses PostgreSQL session advisory lock `pg_advisory_lock(84920174)` and `to_regclass('public.<table>')` pre-checks so concurrent Cloud Run container startups never collide or emit `ERROR` entries in `postgres.log`.

---

## 2. Built-In Chaos Fault Injection Matrix (`chaos_fault_states`)

| Fault Type | Fault Class | Target Service | Triggered Breach & Observed Behavior |
| :--- | :--- | :--- | :--- |
| `insurance_no_policy_write` (`UC-05`) | `BUSINESS` | `motor-insurance-svc` | Returns `HTTP 200 OK` (`ACTIVE`), skips writing `insurance_policies` row; triggers `inv-EveryApprovedLoanHasInsurance` and DFG `SKIPPED`. |
| `foir_threshold_regression` (`UC-06`) | `BUSINESS` | `underwriting-los-svc` | Raises FOIR cap from `60%` to `75%`, approving over-leveraged loans; triggers `inv-foir-under-cap`. |
| `emi_rounding` (`UC-06`) | `BUSINESS` | `loan-orchestrator-api` | Truncates monthly rate to 2 decimals + `+₹250` bias; triggers `inv-emi-matches-formula`. |
| `cibil_stub_900` (`UC-07`) | `BUSINESS` | `core-banking-cibil-vm` | Returns constant `cibil_score = 900` for all borrowers; triggers `inv-cibil-score-valid` (zero score variance). |
| `step_order_insurance_before_kyc` (`UC-08`) | `BUSINESS` | `loan-orchestrator-api` | Executes Utility 4 (`INS`) before Utility 1 (`KYC`); triggers `inv-LoanStepOrder` (DFG `REORDERED`). |
| `gke_oom_503` (`UC-15`) | `INFRA` | `kyc-model-armor-svc` / `underwriting-los-svc` | Simulates GKE pod OOM and returns `503 GKE_OOM_503`. |
| `gce_vm_latency_504` (`UC-16`) | `INFRA` | `core-banking-cibil-vm` | Injects `6.0 s` delay and raises `504 CBS_GATEWAY_TIMEOUT`, leaving loan in `DISBURSEMENT_PENDING`. |
| `cloudsql_lock_contention` | `INFRA` | `god-view-pg` | Simulates lock contention on `loan_applications` / `disbursements`. |
