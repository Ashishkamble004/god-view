---
name: disbursement-requires-sanction-and-approval
scope: {service: core-banking-cibil-vm, entity: disbursement, journey: vehicle_loan_origination}
provenance: code
provenance_ref: "cymbal/services/core-banking-cibil-vm/app/main.py@9f1c2ab#L82-L124"
check:
  dialect: bigquery
  window_s: 300
  statement: |
    SELECT COUNT(*) AS violations
    FROM `general-ak.godview_ledger.events` AS d
    LEFT JOIN `general-ak.godview_ledger.events` AS u
      ON d.journey_id = u.journey_id
     AND u.event_type = 'loan.decided'
     AND u.decision = 'APPROVED'
     AND COALESCE(u.source, 'otel') != 'probe'
    WHERE d.event_type = 'disbursement.settled'
      AND COALESCE(d.source, 'otel') != 'probe'
      AND d.event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 300 SECOND)
      AND (
        u.event_id IS NULL
        OR JSON_VALUE(d.attributes, '$.sanction_letter_no') IS NULL
        OR JSON_VALUE(d.attributes, '$.sanction_letter_no') = ''
      )
severity: sev1
wake_condition: {operator: ">=", threshold: 1, consecutive: 1, target_agent_id: business_line__vehicle_loan_origination}
confidence: 0.96
created_by: data__god-view-pg (gemini-3.8-flash, MEDIUM)
version: 1.0.0
status: enforced
---

# Disbursement ⇒ Sanction Letter ∧ Underwriting Decision = APPROVED

**Business meaning**: no vehicle loan funds may be settled to a dealer account unless the journey has a recorded `APPROVED` underwriting decision and a non-empty `sanction_letter_no`. **₹ at risk** = full disbursed amount of violating rows.
**On breach**: wake the Business-Line Agent and Data Agent to halt settlement and prepare a compensation plan.
