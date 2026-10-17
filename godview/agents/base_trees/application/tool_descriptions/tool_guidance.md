# Application Court Agent — Tool Descriptions & Guidance (c4)

- `obs.query_logs_scoped`: Query scoped structured logs (`pageSize <= 50`) for error signatures, correlation IDs (`loan_id`), and parser exceptions.
- `obs.list_timeseries`: Fetch request rate, error ratio, and p95 latency time series for the target service.
- `obs.get_trace`: Inspect distributed trace spans to pinpoint the exact microservice and downstream RPC boundary where latency or status degradation occurred.
- `git.list_commits`: Correlate incident onset with recent commits or configuration changes.
