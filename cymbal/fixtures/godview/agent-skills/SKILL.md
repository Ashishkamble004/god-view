---
name: diagnose-and-heal-postgres-lock-contention
description: Isolates and resolves PostgreSQL lock contention that stalls vehicle-loan underwriting journeys, through the capability adapter layer only.
version: 1.2.0
created_by: data__god-view-pg
owner_agent_id: data__god-view-pg
provenance: human_runbook
confidence_score: 1.0
executions_count: 0
avg_mttr_seconds: 0.0
trigger:
  sentinel_classes: [golden_signal_gate, journey_conformance]
  symptom: "p95 above 3.5 s on underwriting or orchestrator routes, error rate below 10 %, pods ready, journeys dwelling in LOS"
required_capabilities: [obs.list_traces, obs.get_trace, sql.blocking_tree, sql.list_active_queries, k8s.get_resource, sql.terminate_backend]
steps:
  - {capability: obs.get_trace, min_trust_level: 0}
  - {capability: sql.blocking_tree, min_trust_level: 0}
  - {capability: k8s.get_resource, min_trust_level: 0}
  - {capability: sql.terminate_backend, min_trust_level: 3}
blast_radius: one backend pid
reversal: none required — the terminated statement is retried by the orchestrator saga
verification: p95 below 1 s on the next three 10 s scrapes and no journey stuck in LOS for more than 60 s
---

# Skill: Diagnose & Heal PostgreSQL Lock Contention in Loan Underwriting
**Trigger**: the golden-signal gate flags p95 > 3.5 s on underwriting or orchestrator routes while GKE readiness is green and journeys dwell in the LOS step.
**Diagnosis (bounded, no raw log scraping)**
1. `obs.get_trace` on one exemplar from `obs.list_traces` (affected service, last 5 min) — confirm the `db.query` span is > 80 % of the trace.
2. `sql.blocking_tree` → MCP Toolbox `execute_sql`: `SELECT a.pid, a.wait_event_type, a.query, pg_blocking_pids(a.pid) AS blocked_by FROM pg_stat_activity a JOIN pg_locks l ON l.pid = a.pid WHERE NOT l.granted;` then `sql.list_active_queries` for the root blocker's age.
3. `k8s.get_resource` (gke-mcp) on the `underwriting-los-svc` Deployment and Pods — rule out OOM or CPU throttling as the real cause.
**Remediation & verification**
1. `sql.terminate_backend` → `execute_sql`: `SELECT pg_terminate_backend(<root_blocker_pid>);` (L3, act SA, one pid).
2. Verify as declared; on success bump `executions_count` and `avg_mttr_seconds`; on failure open an approval for a human-led step and demote the action type (§10).
