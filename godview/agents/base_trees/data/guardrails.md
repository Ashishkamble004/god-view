# Data Court Agent — Guardrails & Safety Policy (c2)

## Tier-0 / Tier-1 Read-Only Default (`guardrails.md#tier-0-observe`)
- Execute ONLY read-only database and observability queries (`sql.list_tables`, `sql.list_active_queries`, `obs.query_logs_scoped`).
- Never issue `INSERT`, `UPDATE`, `DELETE`, `DROP`, or `ALTER` statements against `loan_applications`, `kyc_documents`, `underwriting_audits`, or `insurance_policies`.
- All model invocations default to `gemini-4-argon` (`location="global"`), with `gemini-3.8-flash` (`location="global"`) as HTTP 429 / quota / budget fallback.
