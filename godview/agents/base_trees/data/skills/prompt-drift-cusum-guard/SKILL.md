---
id: prompt-drift-cusum-guard
name: prompt-drift-cusum-guard
version: 1.0.0
domain: data
triggers:
  - prompt_drift_cusum
  - step_order_insurance_before_kyc
autotrigger: when CUSUM drift score exceeds threshold or Model Armor flags prompt injection
description: Compute two-sided CUSUM drift statistic on underwriting_audits and verify Model Armor enforcement.
tools_allowed:
  - sql.list_tables
  - obs.query_logs_scoped
  - obs.list_timeseries
guardrail_ref: guardrails.md#tier-0-observe
---
# Skill: prompt-drift-cusum-guard

## Preconditions
- Triggered automatically when CUSUM drift exceeds `4.0` on `underwriting_audits` or `step_order_insurance_before_kyc` fires.
- Requires read-only access to `sql.list_tables` and `obs.query_logs_scoped`.

## Diagnostic Workflow
1. Inspect `underwriting_audits` decision distribution and `obs.query_logs_scoped` for Model Armor events.
2. Execute `python3 scripts/check_prompt_drift_cusum.py` to compute the two-sided CUSUM statistic.
3. Attribute root cause to unpinned model routing or disabled Model Armor template.

## Expected Output Schema
```json
{
  "ok": true,
  "invariant": "prompt_drift_cusum",
  "cusum_score": 5.4,
  "drift_detected": true,
  "model_armor_enforced": true
}
```

## Verification Criteria
- `python3 scripts/check_prompt_drift_cusum.py` exits 0 and emits `"ok": true`.
- Confidence >= 0.85 with CUSUM statistic and table evidence attached.
