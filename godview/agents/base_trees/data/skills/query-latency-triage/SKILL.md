---
description: Triage database query latency elevation and lock contention spikes.
autotrigger: database query latency exceeds threshold or query contention spikes
---

# Query Latency Triage Skill

## Purpose
Investigate elevated database query latency and diagnose active lock contention chains.

## Procedure
1. Query active queries and transaction lock tables.
2. Validate query latencies with `python3 scripts/check_query_latency.py`.
3. Check for distribution shifts with `python3 scripts/check_distribution_shift.py`.
