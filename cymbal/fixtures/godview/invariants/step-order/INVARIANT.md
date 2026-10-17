---
name: step-order
scope: {service: loan-orchestrator-api, entity: loan_application, journey: vehicle_loan_origination}
provenance: code
provenance_ref: "cymbal/services/loan-orchestrator-api/app/main.py@9f1c2ab#L110-L175"
check:
  dialect: bigquery
  window_s: 300
  statement: |
    SELECT COUNT(*) AS violations
    FROM `general-ak.godview_ledger.journeys`
    WHERE started_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 300 SECOND)
      AND (
        conformance IN ('REORDERED', 'SKIPPED')
        OR (
          final_status = 'DISBURSED'
          AND observed_sequence NOT IN (
            'KYC->LOS->INS->CBS',
            'KYC->LOS->FLEET->INS->CBS',
            'KYC->LOS->CREDIT_LINE->INS->CBS'
          )
        )
      )
severity: sev1
wake_condition: {operator: ">=", threshold: 1, consecutive: 1, target_agent_id: business_line__vehicle_loan_origination}
confidence: 0.97
created_by: business_line__vehicle_loan_origination
version: 1.0.0
status: enforced
---

# Workflow Step Order: KYC < LOS < INS < CBS

**Business meaning**: every vehicle loan origination journey must execute KYC verification before LOS underwriting, LOS underwriting before motor insurance issuance, and motor insurance issuance before core-banking disbursement (`KYC < LOS < INS < CBS`, with optional `FLEET` between `LOS` and `INS`).
**On breach**: wake the Business-Line Agent to isolate reordered (`REORDERED`) or bypassed (`SKIPPED`) steps in `loan-orchestrator-api`.
