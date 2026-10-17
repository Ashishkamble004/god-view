# Business-Line Court Agent — Tool Descriptions & Guidance (c4)

- `obs.list_timeseries`: Query Pub/Sub `num_undelivered_messages`, DLQ depth, and saga step completion rates.
- `obs.query_logs_scoped`: Inspect saga orchestrator logs by `loan_id` for `kyc.verified`, `loan.decided`, and `insurance.issued`.
- `obs.get_trace`: Trace end-to-end `vehicle_loan_origination` transactions across services.
- `sql.list_tables`: Verify persistence across `loan_applications`, `kyc_documents`, `underwriting_audits`, and `insurance_policies`.
