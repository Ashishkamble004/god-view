# Business-Line Court Agent — Long-Term Architectural Memory (c3)

- Saga `vehicle_loan_origination` requires ordered completion of `kyc.verified`, `loan.decided`, and `insurance.issued` within the SLA window.
- When downstream worker pods suffer OOMKilled (exit 137) on large PDF attachments, unacknowledged messages accumulate in the Pub/Sub DLQ and trigger `insurance_no_policy_write`.
- Always run `python3 scripts/check_dlq_saga_stall.py` to correlate DLQ depth with container exit codes.
