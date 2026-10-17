---
description: Triage regression incidents following recent commits, deployments, or configuration updates.
autotrigger: deployment incident occurs or commit regression is detected
---

# Commit Regression Triage Skill

## Purpose
Correlate service anomalies with recent code commits and configuration changes.

## Procedure
1. Query recent commit history and pull request descriptions.
2. Validate change timing with `python3 scripts/check_commit_regression.py`.
3. Check for configuration drift with `python3 scripts/check_config_drift.py`.
