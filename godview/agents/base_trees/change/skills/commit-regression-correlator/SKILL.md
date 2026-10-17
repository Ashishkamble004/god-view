---
id: commit-regression-correlator
name: commit-regression-correlator
version: 1.0.0
domain: change
triggers:
  - commit_regression
  - cibil_stub_900
autotrigger: when a deployment, commit, or config change precedes an invariant violation
description: Correlate recent Git commits and config changes with incident onset and invariant violations.
tools_allowed:
  - git.list_commits
  - git.get_commit
  - obs.query_logs_scoped
guardrail_ref: guardrails.md#tier-0-observe
---
# Skill: commit-regression-correlator

## Preconditions
- Triggered automatically when an incident or invariant violation (`cibil_stub_900`, `step_order_insurance_before_kyc`, `vehicle_loan_origination`) occurs following a code or config change.
- Requires read-only access to `git.list_commits` and `obs.query_logs_scoped`.

## Diagnostic Workflow
1. List recent commits via `git.list_commits` and inspect modified files.
2. Execute `python3 scripts/check_commit_regression.py` to compute temporal and path correlation scores.
3. Recommend a targeted revert or fix PR with commit SHA evidence.

## Expected Output Schema
```json
{
  "ok": true,
  "invariant": "commit_regression",
  "suspect_commit_detected": true,
  "correlation_score": 0.94
}
```

## Verification Criteria
- `python3 scripts/check_commit_regression.py` exits 0 and emits `"ok": true`.
- Confidence >= 0.85 with suspect commit SHA or config diff attached.
