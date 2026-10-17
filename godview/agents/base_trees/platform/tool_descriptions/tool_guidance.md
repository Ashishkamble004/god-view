# Platform Court Agent — Tool Descriptions & Guidance (c4)

- `k8s.get_resource`: Inspect Pod, Deployment, and HorizontalPodAutoscaler status, including `lastState.terminated.exitCode` and `reason: OOMKilled`.
- `k8s.query_logs`: Query GKE container and node event logs.
- `k8s.list_recommendations`: Inspect GKE vertical/horizontal sizing recommendations.
- `obs.list_timeseries`: Query container memory working set bytes, CPU throttling, and Pub/Sub DLQ depth.
