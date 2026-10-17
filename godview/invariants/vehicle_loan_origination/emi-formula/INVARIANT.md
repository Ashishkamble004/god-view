---
name: emi-formula
scope: {service: loan-orchestrator-api, entity: loan_application, journey: vehicle_loan_origination}
provenance: policy
provenance_ref: "cymbal/services/underwriting-los-svc/app/rules_engine.py@9f1c2ab#L18-L39"
check:
  dialect: bigquery
  window_s: 300
  statement: |
    SELECT COUNT(*) AS violations
    FROM `general-ak.godview_ledger.events`
    WHERE event_type = 'loan.decided'
      AND emi_inr IS NOT NULL
      AND amount_inr IS NOT NULL
      AND SAFE_CAST(JSON_VALUE(attributes, '$.interest_rate_pct') AS FLOAT64) > 0
      AND SAFE_CAST(JSON_VALUE(attributes, '$.tenure_months') AS INT64) > 0
      AND COALESCE(source, 'otel') != 'probe'
      AND event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 300 SECOND)
      AND ABS(
        CAST(emi_inr AS FLOAT64) - (
          CAST(amount_inr AS FLOAT64)
          * (SAFE_CAST(JSON_VALUE(attributes, '$.interest_rate_pct') AS FLOAT64) / 1200.0)
          * POW(
              1.0 + (SAFE_CAST(JSON_VALUE(attributes, '$.interest_rate_pct') AS FLOAT64) / 1200.0),
              SAFE_CAST(JSON_VALUE(attributes, '$.tenure_months') AS FLOAT64)
            )
          / NULLIF(
              POW(
                1.0 + (SAFE_CAST(JSON_VALUE(attributes, '$.interest_rate_pct') AS FLOAT64) / 1200.0),
                SAFE_CAST(JSON_VALUE(attributes, '$.tenure_months') AS FLOAT64)
              ) - 1.0,
              0.0
            )
        )
      ) > 1.0
severity: sev2
wake_condition: {operator: ">=", threshold: 1, consecutive: 2, target_agent_id: application__loan-orchestrator-api}
confidence: 0.95
created_by: application__loan-orchestrator-api (gemini-3.8-flash, MEDIUM)
version: 1.0.0
status: enforced
---

# Reducing-Balance EMI Formula Tolerance: |EMI − f(P, r, n)| ≤ ₹1

**Business meaning**: the monthly installment `emi_inr` on every decided loan must equal the standard reducing-balance amortization formula $P \cdot r \cdot (1+r)^n / ((1+r)^n - 1)$ (where $r = \text{rate\_pct} / 1200$) within a tolerance of ±₹1.00.
**On breach**: wake the Application Agent to inspect EMI rounding or interest-rate conversion logic.
