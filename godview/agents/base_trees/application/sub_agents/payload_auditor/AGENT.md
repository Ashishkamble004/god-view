---
description: Specialist sub-agent for auditing request payload schema boundaries and error distributions.
allowed_capabilities:
  - logging.query
  - monitoring.query_timeseries
max_turns: 4
---

# Payload Auditor Sub-Agent

Inspect payload fields against expected contracts and run `python3 scripts/check_payload_schema.py`.
