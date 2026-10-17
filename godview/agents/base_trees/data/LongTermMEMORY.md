# Data Court Agent — Long-Term Architectural Memory (c3)

- Two-sided CUSUM drift > 4.0 on `underwriting_audits` indicates prompt drift or model routing degradation (`gemini-3.8-flash` vs `gemini-4-argon`).
- When `cibil_score` equals 900 across a batch of `loan_applications`, verify `cibil_stub_900` via `python3 scripts/check_cibil_score_offsets.py`.
- Ordering invariant `step_order_insurance_before_kyc` prohibits `insurance.issued` timestamps earlier than `kyc.verified` for the same `loan_id`.
