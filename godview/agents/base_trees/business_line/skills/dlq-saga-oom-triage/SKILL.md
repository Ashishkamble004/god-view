---
id: dlq-saga-oom-triage
name: dlq-saga-oom-triage
version: 1.0.0
domain: business_line
triggers:
  - vehicle_loan_origination
  - insurance_no_policy_write
autotrigger: when Pub/Sub DLQ backlog spikes, pod OOMKilled occurs, or loan_application saga stalls
description: Correlate GKE OOMKilled container exit code 137 with Pub/Sub DLQ saga stall in vehicle_loan_origination.
tools_allowed:
  - obs.list_timeseries
  - obs.query_logs_scoped
  - k8s.get_resource
guardrail_ref: guardrails.md#tier-0-observe
---
# Skill: dlq-saga-oom-triage

## Preconditions
- Triggered automatically when `vehicle_loan_origination` stalls or `insurance_no_policy_write` fires.
- Requires read-only access to `obs.list_timeseries`, `obs.query_logs_scoped`, and `k8s.get_resource`.

## Diagnostic Workflow
1. Query `obs.list_timeseries` for Pub/Sub DLQ backlog depth and `k8s.get_resource` for pod termination status.
2. Execute `python3 scripts/check_dlq_saga_stall.py` to verify OOM exit code 137 and stalled saga correlation.
3. Recommend memory limit adjustment and controlled DLQ replay under L2 human approval.

## Expected Output Schema
```json
{
  "ok": true,
  "invariant": "vehicle_loan_origination",
  "oom_exit_137": true,
  "dlq_depth": 42,
  "saga_stalled": true
}
```

## Verification Criteria
- `python3 scripts/check_dlq_saga_stall.py` exits 0 and emits `"ok": true`.
- Confidence >= 0.85 with DLQ depth and pod exit code citations.
