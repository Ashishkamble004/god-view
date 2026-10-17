---
description: Triage container crash loops, OOM exit code 137 terminations, and resource limits.
autotrigger: container crashes or resource limit is exceeded
---

# Container Crash Triage Skill

## Purpose
Investigate container terminations and diagnose out-of-memory or startup probe failures.

## Procedure
1. Query container statuses, exit codes, and resource limits.
2. Validate termination codes with `python3 scripts/check_container_restart.py`.
3. Check memory saturation with `python3 scripts/check_resource_limits.py`.
