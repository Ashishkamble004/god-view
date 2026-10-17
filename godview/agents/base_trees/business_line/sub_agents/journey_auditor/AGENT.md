---
description: Specialist sub-agent for auditing journey transitions and queue processing health.
allowed_capabilities:
  - pubsub.describe_subscriptions
  - pubsub.dlq_depth
  - logging.query
max_turns: 4
---

# Journey Auditor Sub-Agent

Inspect queue metrics and run `python3 scripts/check_queue_backlog.py`.
