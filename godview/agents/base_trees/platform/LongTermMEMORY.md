# Platform Court Agent — Long-Term Architectural Memory (c3)

- Container exit code 137 (`OOMKilled`) on PDF processing workers occurs when multi-page attachments exceed pod memory limits, stalling `vehicle_loan_origination` and triggering `insurance_no_policy_write`.
- Always run `python3 scripts/check_dlq_saga_stall.py` to verify OOM exit code 137 and DLQ backlog correlation before recommending memory limit increases or pod restarts.
