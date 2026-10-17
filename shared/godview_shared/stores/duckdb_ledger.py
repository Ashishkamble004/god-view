"""Local DuckDB-backed LedgerStore implementation for God-View local mode."""

from __future__ import annotations

import copy
import json
import logging
import os
from collections.abc import Iterable
from typing import Any

import duckdb

logger = logging.getLogger("godview.stores.duckdb_ledger")


class DuckDBLedger:
    """DuckDB-backed LedgerStore for local mode per Plan §1.1."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or os.environ.get("LOCAL_DUCKDB_PATH", ":memory:")
        self._conn = duckdb.connect(self.db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        # Standard generic schema per plan §3.6
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id VARCHAR PRIMARY KEY,
                event_time TIMESTAMPTZ,
                env_id VARCHAR,
                journey_id VARCHAR,
                trace_id VARCHAR,
                span_id VARCHAR,
                service VARCHAR,
                operation VARCHAR,
                event_type VARCHAR,
                entity_type VARCHAR,
                entity_id VARCHAR,
                decision VARCHAR,
                state_from VARCHAR,
                state_to VARCHAR,
                numeric_attributes VARCHAR,
                currency VARCHAR,
                http_status BIGINT,
                duration_ms BIGINT,
                is_synthetic BOOLEAN,
                source VARCHAR,
                attributes VARCHAR
            );
            CREATE TABLE IF NOT EXISTS state_transitions (
                transition_id VARCHAR PRIMARY KEY,
                transition_time TIMESTAMPTZ,
                env_id VARCHAR,
                entity VARCHAR,
                entity_id VARCHAR,
                journey_id VARCHAR,
                status_from VARCHAR,
                status_to VARCHAR,
                actor_service VARCHAR,
                source VARCHAR,
                dwell_ms BIGINT,
                is_synthetic BOOLEAN,
                attributes VARCHAR
            );
            CREATE TABLE IF NOT EXISTS journeys (
                journey_id VARCHAR PRIMARY KEY,
                journey_name VARCHAR,
                started_at TIMESTAMPTZ,
                ended_at TIMESTAMPTZ,
                env_id VARCHAR,
                customer_id VARCHAR,
                steps VARCHAR,
                observed_sequence VARCHAR,
                conformance VARCHAR,
                final_status VARCHAR,
                total_value DOUBLE,
                total_duration_ms BIGINT,
                is_synthetic BOOLEAN,
                journey_model_version VARCHAR
            );
            CREATE TABLE IF NOT EXISTS kpi_rollups (
                window_start TIMESTAMPTZ,
                window_end TIMESTAMPTZ,
                env_id VARCHAR,
                journey_name VARCHAR,
                kpi VARCHAR,
                segment VARCHAR,
                value DOUBLE,
                baseline_value DOUBLE
            );
            """
        )

    def insert_rows(self, table: str, rows: Iterable[dict[str, Any]]) -> int:
        row_list = [copy.deepcopy(r) for r in rows]
        if not row_list:
            return 0

        count = 0
        for r in row_list:
            cols = list(r.keys())
            placeholders = ", ".join(["?"] * len(cols))
            col_names = ", ".join(cols)
            vals = []
            for k in cols:
                v = r[k]
                if isinstance(v, (dict, list)):
                    vals.append(json.dumps(v))
                else:
                    vals.append(v)
            sql = f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})"
            self._conn.execute(sql, vals)
            count += 1
        return count

    def get_table(self, table: str) -> list[dict[str, Any]]:
        cursor = self._conn.execute(f"SELECT * FROM {table}")
        cols = [desc[0] for desc in cursor.description]
        results = []
        for row in cursor.fetchall():
            results.append(dict(zip(cols, row, strict=False)))
        return results

    def query(
        self,
        statement: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not statement or not statement.strip():
            raise ValueError("SQL statement must not be empty")

        if params:
            # DuckDB supports named or positional parameters
            cursor = self._conn.execute(statement, params)
        else:
            cursor = self._conn.execute(statement)

        if not cursor.description:
            return []
        cols = [desc[0] for desc in cursor.description]
        results = []
        for row in cursor.fetchall():
            results.append(dict(zip(cols, row, strict=False)))
        return results
