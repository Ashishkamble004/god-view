---
description: Triage unexpected HTTP 4xx and 5xx error spikes using structured request logs.
autotrigger: service error rate exceeds threshold or HTTP 5xx responses spike
---

# HTTP Error Triage Skill

## Purpose
Investigate elevated error rates and correlate status codes with backend failure details.

## Procedure
1. Query scoped structured logs for HTTP status codes >= 400.
2. Group occurrences by request path and statusDetails.
3. Validate error ratios with `python3 scripts/check_error_rate.py`.
4. If failures relate to missing payload attributes, run `python3 scripts/check_payload_schema.py`.
