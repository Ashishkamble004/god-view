---
description: Specialist sub-agent for inspecting host container health and resource metrics.
allowed_capabilities:
  - k8s.get_resource
  - k8s.query_logs
  - monitoring.query_timeseries
max_turns: 4
---

# Resource Inspector Sub-Agent

Inspect container statuses and run `python3 scripts/check_container_restart.py`.
