---
id: gke-oom-dlq-guard
name: gke-oom-dlq-guard
version: 1.0.0
domain: platform
triggers:
  - oom_killed_137
  - vehicle_loan_origination
autotrigger: when pod OOMKilled exit 137 occurs, memory working set saturates, or DLQ spikes
description: Diagnose GKE Autopilot OOMKilled (exit 137) pod crashes and correlate with Pub/Sub DLQ stalls.
tools_allowed:
  - k8s.get_resource
  - k8s.query_logs
  - obs.list_timeseries
guardrail_ref: guardrails.md#tier-0-observe
---
# Skill: gke-oom-dlq-guard

## Preconditions
- Triggered automatically when a GKE worker pod terminates with exit code 137 (`OOMKilled`) or memory saturation alerts fire.
- Requires read-only access to `k8s.get_resource` and `obs.list_timeseries`.

## Diagnostic Workflow
1. Inspect `k8s.get_resource` for `OOMKilled` (`exitCode: 137`) and container memory limits.
2. Execute `python3 scripts/check_dlq_saga_stall.py` to confirm OOM + DLQ stall correlation.
3. Propose single-deployment resource adjustment or rolling restart gated by `TrustGate`.

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
- Confidence >= 0.85 with pod status and exit code 137 evidence.
