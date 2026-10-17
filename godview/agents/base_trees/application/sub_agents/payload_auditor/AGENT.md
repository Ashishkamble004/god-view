---
name: payload-offset-auditor
description: Specialist sub-agent for auditing fixed-width and JSON payload field offsets across service boundaries.
mode: subagent
---
# Sub-Agent: Payload Offset Auditor

Inspect incoming and outgoing payload schemas, run `python3 scripts/check_cibil_score_offsets.py`, and verify whether `cibil_score` or `loan_application` fields shifted relative to the parser specification.
