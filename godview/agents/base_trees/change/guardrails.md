# Change Court Agent — Guardrails & Safety Policy (c2)

## Tier-0 / Tier-1 Read-Only Default (`guardrails.md#tier-0-observe`)
- During diagnostic rollouts, invoke ONLY read-only Git and observability capabilities (`git.list_commits`, `git.get_commit`, `git.get_file_contents`, `obs.query_logs_scoped`).
- Never open branches or pull requests (`git.create_branch`, `git.create_pull_request`) without a valid `TrustGate` authorization decision and L2 approval when required.
- All model invocations default to `gemini-4-argon` (`location="global"`), with `gemini-3.8-flash` (`location="global"`) as HTTP 429 / quota / budget fallback.
