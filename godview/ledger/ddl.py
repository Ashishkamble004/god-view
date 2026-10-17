"""BigQuery DDL and JSON schema loader for dataset godview_ledger (TDD §2.2)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from godview_shared.contracts import LEDGER_TABLES

SCHEMA_DIR: Path = Path(__file__).resolve().parent / "schemas"
DDL_SQL_PATH: Path = Path(__file__).resolve().parent / "ddl.sql"


@dataclass(frozen=True)
class LedgerTableSpec:
    """Partitioning, clustering, and column specification for a BigQuery ledger table."""

    table_name: str
    partition_field: str
    cluster_fields: tuple[str, ...]
    schema_file: Path


TABLE_SPECS: dict[str, LedgerTableSpec] = {
    "events": LedgerTableSpec(
        table_name="events",
        partition_field="event_time",
        cluster_fields=("journey_id", "service", "event_type"),
        schema_file=SCHEMA_DIR / "events.json",
    ),
    "state_transitions": LedgerTableSpec(
        table_name="state_transitions",
        partition_field="transition_time",
        cluster_fields=("entity", "journey_id", "status_to"),
        schema_file=SCHEMA_DIR / "state_transitions.json",
    ),
    "journeys": LedgerTableSpec(
        table_name="journeys",
        partition_field="started_at",
        cluster_fields=("journey_name", "conformance", "final_status"),
        schema_file=SCHEMA_DIR / "journeys.json",
    ),
    "kpi_rollups": LedgerTableSpec(
        table_name="kpi_rollups",
        partition_field="window_start",
        cluster_fields=("journey_name", "kpi"),
        schema_file=SCHEMA_DIR / "kpi_rollups.json",
    ),
}

DDL_STATEMENTS: dict[str, str] = {
    "events": """CREATE TABLE IF NOT EXISTS `{dataset}.events` (
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
  entity_type STRING,
  entity_id STRING,
  state_from STRING,
  state_to STRING,
  duration_ms INT64,
  http_status INT64,
  is_synthetic BOOL,
  source STRING,
  attributes JSON,
  numeric_attributes JSON,
  currency STRING
)
PARTITION BY DATE(event_time)
CLUSTER BY journey_id, service, event_type""",
    "state_transitions": """CREATE TABLE IF NOT EXISTS `{dataset}.state_transitions` (
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
CLUSTER BY entity, journey_id, status_to""",
    "journeys": """CREATE TABLE IF NOT EXISTS `{dataset}.journeys` (
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
  total_value NUMERIC,
  total_duration_ms INT64,
  is_synthetic BOOL,
  journey_model_version STRING
)
PARTITION BY DATE(started_at)
CLUSTER BY journey_name, conformance, final_status""",
    "kpi_rollups": """CREATE TABLE IF NOT EXISTS `{dataset}.kpi_rollups` (
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
CLUSTER BY journey_name, kpi""",
}


def load_table_schema(table: str) -> list[dict[str, Any]]:
    """Load the BigQuery JSON schema array for one of the 4 ledger tables."""
    if table not in TABLE_SPECS:
        raise ValueError(f"Unknown ledger table '{table}' (allowed: {LEDGER_TABLES})")
    spec = TABLE_SPECS[table]
    if not spec.schema_file.is_file():
        raise FileNotFoundError(f"BigQuery schema file not found: {spec.schema_file}")
    data = json.loads(spec.schema_file.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Schema file {spec.schema_file} must contain a JSON array")
    return data


def get_table_ddl(table: str, dataset: str = "godview_ledger") -> str:
    """Return the BigQuery CREATE TABLE DDL statement for the specified table."""
    if table not in DDL_STATEMENTS:
        raise ValueError(f"Unknown ledger table '{table}' (allowed: {LEDGER_TABLES})")
    return DDL_STATEMENTS[table].format(dataset=dataset)


def get_all_ddl_statements(dataset: str = "godview_ledger") -> dict[str, str]:
    """Return a mapping of table_name -> BigQuery CREATE TABLE DDL for all 4 tables."""
    return {tbl: get_table_ddl(tbl, dataset=dataset) for tbl in LEDGER_TABLES}
