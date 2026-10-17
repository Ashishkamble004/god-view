# God-View v4.0 + Harness² SRE Control Plane — System Design

**Plane**: God-View v4.0 Autonomous SRE & Business Continuity Organism (`godview/`)
**Primary Reasoning & Harness Proposer Model**: `gemini-4-argon` (`global`), with automatic HTTP 429 / quota / reserve fallback to `gemini-3.8-flash` (`global`)
**Related Docs**: [God-View Hub](README.md) | [God-View HLD](HLD.md) | [God-View LLD](LLD.md) | [Master System Design](../SYSTEM_DESIGN.md)

---

## 1. Statistical & Process-Mining Sentinel Algorithms

### 1.1 Two-Sided Tabular CUSUM & EWMA (`godview/sovereign/sentinel_engine/__init__.py`)

For continuous metrics without static hard bounds (such as underwriting approval rate, average CIBIL score distribution, and Model Armor block rate), `SentinelEngine` runs a zero-token two-sided **Cumulative Sum (CUSUM)** and **Exponentially Weighted Moving Average (EWMA)** detector over 1-minute and 5-minute BigQuery `kpi_rollups`:

- **Normalized Residual**: $z_t = \frac{x_t - \mu_0}{\sigma_0}$
- **Upper & Lower CUSUM Accumulators**:
  - $S_t^+ = \max(0, S_{t-1}^+ + z_t - k)$
  - $S_t^- = \max(0, S_{t-1}^- - z_t - k)$
  - Default slack parameter $k = 0.5\sigma_0$, decision threshold $h = 4.0\sigma_0$.
- **EWMA Smoother**:
  - $E_t = \lambda x_t + (1 - \lambda) E_{t-1}$ with $\lambda = 0.2$.
- **Confirmation Rule**: A Sentinel fires a `SentinelBreachEvent` only when **2 out of 3 consecutive 10-second evaluation windows** breach the predicate or threshold, eliminating transient single-tick jitter.

### 1.2 Directly-Follows Graph (DFG) Process Mining (`godview/ledger/process_mining.py`)

For multi-step business journeys, `ProcessMiner` reconstructs the ordered sequence of span events per `journey_id` (`KYC`, `LOS`, `FLEET_CREDIT`, `INS`, `CBS`) and checks conformance against the intended grammar:

- **Retail Auto Loan Grammar**: `KYC -> LOS -> INS -> CBS` (with early terminal states `KYC -> MODEL_ARMOR_BLOCKED` or `KYC -> LOS -> REJECTED`).
- **Commercial Fleet Top-Up Grammar**: `KYC -> LOS -> FLEET_CREDIT -> INS -> CBS`.
- **Detected Conformance Anomalies**:
  - `REORDERED`: e.g., `INS -> KYC -> LOS -> CBS` (`step_order_insurance_before_kyc`, `UC-08`).
  - `SKIPPED`: e.g., `KYC -> LOS -> CBS` reaching `DISBURSED` without `INS` (`insurance_no_policy_write`, `UC-05`).
  - `STALLED`: Journey remains in `DISBURSEMENT_PENDING` beyond 120 seconds (`gce_vm_latency_504`, `UC-16`).

---

## 2. TokenBudgetGovernor & Harness² Recursion Budgets

| Budget Scope | Daily / Per-Incident Allocation | Primary Model | Fallback / Reserve Behavior |
| :--- | :--- | :--- | :--- |
| **Sovereign Orchestrator** | `$1.50 / day` | `gemini-4-argon` (`global`) | Switches to `gemini-3.8-flash` (`global`) at 80% daily spend or on HTTP 429. |
| **Court Agent: `application`** | `$0.75 / day` | `gemini-4-argon` (`global`) | Final 20% (`$0.15`) reserved for high-severity escalations; falls back to `gemini-3.8-flash`. |
| **Court Agent: `business_line`** | `$0.75 / day` | `gemini-4-argon` (`global`) | Final 20% (`$0.15`) reserved for L4 saga consensus; falls back to `gemini-3.8-flash`. |
| **Court Agent: `change`** | `$0.75 / day` | `gemini-4-argon` (`global`) | Final 20% (`$0.15`) reserved for L3 GitOps PR synthesis; falls back to `gemini-3.8-flash`. |
| **Court Agent: `data`** | `$0.50 / day` | `gemini-4-argon` (`global`) | Falls back to `gemini-3.8-flash` at 80% spend. |
| **Court Agent: `platform`** | `$0.50 / day` | `gemini-4-argon` (`global`) | Falls back to `gemini-3.8-flash` at 80% spend. |
| **Harness² Level-1 `BudgetLedger`** | $R = 4{,}000$ recursion tok, $P = 3{,}000$ probe tok | `gemini-4-argon` (`global`) | Raises `BudgetExhausted` and selects best validated `HarnessTree` $H^*$ when $R$ or $P$ is exhausted. |
| **Harness² `TokenLedger` (`c1..c4`)** | `always_on_max = 12,000` chars/tokens | Enforced by `GodViewCourtAdapter.validate_tree()` | Rejects candidate `HarnessTree` mutations that exceed the 12,000 always-on cap. |
