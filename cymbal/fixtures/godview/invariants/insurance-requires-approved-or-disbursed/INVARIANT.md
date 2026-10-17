---
name: insurance-requires-approved-or-disbursed
scope: {service: motor-insurance-svc, entity: insurance_policy, journey: vehicle_loan_origination}
provenance: code
provenance_ref: "cymbal/services/motor-insurance-svc/app/main.py@9f1c2ab#L35-L70"
check:
  dialect: bigquery
  window_s: 300
  statement: |
    SELECT COUNT(*) AS violations
    FROM `general-ak.godview_ledger.events` AS i
    LEFT JOIN `general-ak.godview_ledger.events` AS u
      ON i.journey_id = u.journey_id
     AND u.event_type = 'loan.decided'
     AND u.decision IN ('APPROVED', 'DISBURSED')
     AND COALESCE(u.source, 'otel') != 'probe'
    WHERE i.event_type = 'insurance.issued'
      AND COALESCE(i.source, 'otel') != 'probe'
      AND i.event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 300 SECOND)
      AND u.event_id IS NULL
severity: sev2
wake_condition: {operator: ">=", threshold: 1, consecutive: 1, target_agent_id: application__motor-insurance-svc}
confidence: 0.94
created_by: application__motor-insurance-svc
version: 1.0.0
status: enforced
---

# Insurance Policy ⇒ Loan Status ∈ {APPROVED, DISBURSED}

**Business meaning**: a motor insurance policy (and bundled FASTag) may only be bound for a loan application whose underwriting status is `APPROVED` or `DISBURSED`.
**On breach**: wake the Application Agent for `motor-insurance-svc` and the Business-Line Agent to inspect journey ordering and policy writes.
