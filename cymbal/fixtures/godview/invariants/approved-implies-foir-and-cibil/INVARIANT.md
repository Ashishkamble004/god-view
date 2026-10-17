---
name: approved-implies-foir-and-cibil
scope: {service: underwriting-los-svc, entity: loan_application, journey: vehicle_loan_origination}
provenance: code
provenance_ref: "cymbal/services/underwriting-los-svc/app/rules_engine.py@9f1c2ab#L41-L58"
check:
  dialect: bigquery
  window_s: 300
  statement: |
    SELECT COUNT(*) AS violations FROM `general-ak.godview_ledger.events`
    WHERE event_type = 'loan.decided' AND decision = 'APPROVED'
      AND (foir_pct > 60 OR cibil_score < 680)
      AND COALESCE(source, 'otel') != 'probe'
      AND event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 300 SECOND)
severity: sev1
wake_condition: {operator: ">=", threshold: 1, consecutive: 2, target_agent_id: application__underwriting-los-svc}
confidence: 0.93
created_by: application__underwriting-los-svc
version: 1.0.0
status: enforced
---

# APPROVED ⇒ FOIR ≤ 60 ∧ CIBIL ≥ 680

**Business meaning**: an APPROVED underwriting decision requires a fixed-obligation-to-income ratio of at most 60 % and a bureau score of at least 680 (credit policy, enforced in `rules_engine.py`). **₹ at risk** = sum of `amount_inr` over violations.
**On breach**: wake the owning Application Agent; the swarm checks the change-plane first — threshold edits are the usual cause.
