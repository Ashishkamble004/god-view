# Business-Line Court Agent — Guardrails & Safety Policy (c2)

## Tier-0 / Tier-1 Read-Only Default (`guardrails.md#tier-0-observe`)
- Execute ONLY read-only Six Senses capabilities during diagnostic rollouts.
- Customer communication (`comms.customer_notify`) and money-movement capabilities are hard-capped at Trust Level L2 unless explicitly whitelisted by a human owner (TDD I-06).
- All model invocations default to `gemini-4-argon` (`location="global"`), with `gemini-3.8-flash` (`location="global"`) as HTTP 429 / quota / budget fallback.
