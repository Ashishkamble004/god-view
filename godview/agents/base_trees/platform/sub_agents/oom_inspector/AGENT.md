---
name: gke-oom-inspector
description: Specialist sub-agent for inspecting GKE pod OOMKilled events, memory limits, and container exit codes.
mode: subagent
---
# Sub-Agent: GKE OOM Inspector

Inspect pod termination states via `k8s.get_resource` and run `python3 scripts/check_dlq_saga_stall.py`.
