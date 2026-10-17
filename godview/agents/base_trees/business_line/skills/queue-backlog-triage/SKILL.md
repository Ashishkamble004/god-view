---
description: Triage message queue backlog spikes and dead-letter message accumulation.
autotrigger: queue backlog exceeds threshold or message delivery is delayed
---

# Queue Backlog Triage Skill

## Purpose
Investigate message backlog accumulation and stalled asynchronous journey steps.

## Procedure
1. Query subscription health and DLQ depth.
2. Validate backlog counts with `python3 scripts/check_queue_backlog.py`.
3. Check transition step conformance with `python3 scripts/check_step_sequence.py`.
