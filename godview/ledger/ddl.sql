-- BigQuery Business Event Ledger DDL (TDD §2.2)
-- Dataset: godview_ledger (us-central1)

CREATE TABLE IF NOT EXISTS `godview_ledger.events` (
  event_id STRING NOT NULL,
  event_time TIMESTAMP NOT NULL,
  env_id STRING NOT NULL,
  journey_id STRING,
  trace_id STRING,
  span_id STRING,
  service STRING NOT NULL,
  operation STRING,
  event_type STRING NOT NULL,
  customer_id STRING,
  loan_id STRING,
  entity STRING,
  entity_id STRING,
  decision STRING,
  foir_pct NUMERIC,
  cibil_score INT64,
  emi_inr NUMERIC,
  amount_inr NUMERIC,
  http_status INT64,
  duration_ms INT64,
  is_synthetic BOOL,
  source STRING,
  attributes JSON
)
PARTITION BY DATE(event_time)
CLUSTER BY journey_id, service, event_type;

CREATE TABLE IF NOT EXISTS `godview_ledger.state_transitions` (
  transition_id STRING NOT NULL,
  transition_time TIMESTAMP NOT NULL,
  env_id STRING NOT NULL,
  entity STRING NOT NULL,
  entity_id STRING NOT NULL,
  journey_id STRING,
  status_from STRING,
  status_to STRING NOT NULL,
  actor_service STRING,
  source STRING,
  dwell_ms INT64,
  is_synthetic BOOL,
  attributes JSON
)
PARTITION BY DATE(transition_time)
CLUSTER BY entity, journey_id, status_to;

CREATE TABLE IF NOT EXISTS `godview_ledger.journeys` (
  journey_id STRING NOT NULL,
  journey_name STRING NOT NULL,
  started_at TIMESTAMP NOT NULL,
  ended_at TIMESTAMP,
  env_id STRING NOT NULL,
  customer_id STRING,
  steps ARRAY<STRUCT<
    seq INT64,
    service STRING,
    operation STRING,
    at TIMESTAMP,
    status STRING,
    duration_ms INT64
  >>,
  observed_sequence STRING,
  conformance STRING,
  final_status STRING,
  rupee_value NUMERIC,
  total_duration_ms INT64,
  is_synthetic BOOL,
  journey_model_version STRING
)
PARTITION BY DATE(started_at)
CLUSTER BY journey_name, conformance, final_status;

CREATE TABLE IF NOT EXISTS `godview_ledger.kpi_rollups` (
  window_start TIMESTAMP NOT NULL,
  window_end TIMESTAMP NOT NULL,
  env_id STRING NOT NULL,
  journey_name STRING NOT NULL,
  kpi STRING NOT NULL,
  segment STRING,
  value NUMERIC,
  baseline_value NUMERIC,
  zscore NUMERIC,
  cusum NUMERIC,
  sample_n INT64,
  breach BOOL
)
PARTITION BY DATE(window_start)
CLUSTER BY journey_name, kpi;
