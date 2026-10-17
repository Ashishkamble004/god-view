---
name: saga-funnel-auditor
description: Specialist sub-agent for auditing vehicle_loan_origination saga transitions and DLQ backlogs.
mode: subagent
---
# Sub-Agent: Saga Funnel Auditor

Verify `kyc.verified` -> `loan.decided` -> `insurance.issued` completion and run `python3 scripts/check_dlq_saga_stall.py`.
