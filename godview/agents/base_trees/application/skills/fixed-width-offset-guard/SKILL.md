---
id: fixed-width-offset-guard
name: fixed-width-offset-guard
version: 1.0.0
domain: application
triggers:
  - cibil_stub_900
  - fixed_width_offset_shift
autotrigger: when cibil_score equals 900 stub or fixed-width segment byte offsets shift
description: Detect 120-byte fixed-width bureau response shift causing cibil_stub_900 fallback in loan_applications.
tools_allowed:
  - obs.query_logs_scoped
  - obs.get_trace
  - git.list_commits
guardrail_ref: guardrails.md#tier-0-observe
---
# Skill: fixed-width-offset-guard

## Preconditions
- Triggered automatically when `cibil_score` values cluster at `900` (`cibil_stub_900`) or parser logs report segment offset mismatches.
- Requires read-only access to `obs.query_logs_scoped` and `git.list_commits`.

## Diagnostic Workflow
1. Query `obs.query_logs_scoped` for `cibil_stub_900` and `loan_id` parser warnings.
2. Execute `python3 scripts/check_cibil_score_offsets.py` to compute the byte offset delta between expected (108) and actual (120) segment positions.
3. Emit a structured root-cause hypothesis citing the +120-byte shift and affected `loan_applications`.

## Expected Output Schema
```json
{
  "ok": true,
  "invariant": "cibil_stub_900",
  "shift_bytes": 120,
  "stub_900_detected": true,
  "recommended_offset": 120
}
```

## Verification Criteria
- `python3 scripts/check_cibil_score_offsets.py` exits 0 and confirms `"ok": true`.
- Hypothesis confidence >= 0.85 with concrete byte-offset evidence.
