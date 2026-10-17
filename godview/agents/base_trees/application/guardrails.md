# Application Court Agent — Guardrails & Safety Policy (c2)

## Tier-0 / Tier-1 Read-Only Default (`guardrails.md#tier-0-observe`)
- During diagnostic rollouts and Harness² shadow evaluations, execute ONLY read-only Six Senses capabilities (`obs.*`, `k8s.get_resource`, `sql.list_tables`, `git.list_commits`).
- Never invoke mutating capabilities (`k8s.rollout_restart`, `k8s.scale`, `git.create_pull_request`) without a valid `TrustGate` authorization decision and, at Trust Level L2, a verified single-use human `approval_id`.
- Blast radius is strictly capped at 1 resource per remediation step (TDD I-06).
- All model invocations MUST route to `gemini-4-argon` at `location="global"` (with `gemini-3.8-flash` at `location="global"` as HTTP 429 / quota / budget fallback).
