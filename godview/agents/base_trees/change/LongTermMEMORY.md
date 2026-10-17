# Change Court Agent — Long-Term Architectural Memory (c3)

- Commits modifying fixed-width parser slice offsets or model temperature/routing configuration are high-probability root causes when `cibil_stub_900` or `step_order_insurance_before_kyc` fires within 30 minutes of deployment.
- Always run `python3 scripts/check_commit_regression.py` to score temporal and file-path correlation between recent commits and active alerts.
