# Platform Court Agent — Guardrails & Safety Policy (c2)

## Tier-0 / Tier-1 Read-Only Default (`guardrails.md#tier-0-observe`)
- Execute ONLY read-only GKE and observability capabilities (`k8s.get_resource`, `k8s.query_logs`, `k8s.list_recommendations`, `obs.list_timeseries`) during diagnostic rollouts.
- Mutating Kubernetes actions (`k8s.rollout_restart`, `k8s.scale`) require `TrustGate.authorize()` approval and are limited to a blast radius of 1 resource per step (TDD I-06).
- All model invocations default to `gemini-4-argon` (`location="global"`), with `gemini-3.8-flash` (`location="global"`) as HTTP 429 / quota / budget fallback.
