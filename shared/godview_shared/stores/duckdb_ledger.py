"""Local DuckDB-backed LedgerStore implementation for God-View local mode."""

from __future__ import annotations

import copy
import json
import logging
import os
import re
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
        # Create godview_ledger schema and tables per plan §3.6
        self._conn.execute("CREATE SCHEMA IF NOT EXISTS godview_ledger;")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS godview_ledger.events (
                event_id VARCHAR PRIMARY KEY,
                event_time TIMESTAMP,
                env_id VARCHAR,
                customer_id VARCHAR,
                entity VARCHAR,
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
            CREATE TABLE IF NOT EXISTS godview_ledger.state_transitions (
                transition_id VARCHAR PRIMARY KEY,
                transition_time TIMESTAMP,
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
            CREATE TABLE IF NOT EXISTS godview_ledger.journeys (
                journey_id VARCHAR PRIMARY KEY,
                journey_name VARCHAR,
                started_at TIMESTAMP,
                ended_at TIMESTAMP,
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
            CREATE TABLE IF NOT EXISTS godview_ledger.kpi_rollups (
                rollup_id VARCHAR PRIMARY KEY,
                window_start TIMESTAMP,
                window_end TIMESTAMP,
                env_id VARCHAR,
                journey_name VARCHAR,
                kpi VARCHAR,
                segment VARCHAR,
                value DOUBLE,
                baseline_value DOUBLE,
                zscore DOUBLE,
                cusum DOUBLE,
                sample_n BIGINT,
                breach BOOLEAN
            );
            -- Also create views in main schema for unqualified queries
            CREATE VIEW IF NOT EXISTS events AS SELECT * FROM godview_ledger.events;
            CREATE VIEW IF NOT EXISTS state_transitions AS SELECT * FROM godview_ledger.state_transitions;
            CREATE VIEW IF NOT EXISTS journeys AS SELECT * FROM godview_ledger.journeys;
            CREATE VIEW IF NOT EXISTS kpi_rollups AS SELECT * FROM godview_ledger.kpi_rollups;
            """
        )

    def _resolve_table_name(self, table: str) -> str:
        clean = table.strip().replace("`", "")
        if clean.startswith("godview_ledger."):
            return clean
        if "." in clean:
            # e.g. project.dataset.table -> take last part
            clean = clean.split(".")[-1]
        return f"godview_ledger.{clean}"

    def insert_rows(self, table: str, rows: Iterable[dict[str, Any]]) -> int:
        row_list = [copy.deepcopy(r) for r in rows]
        if not row_list:
            return 0

        target_table = self._resolve_table_name(table)
        count = 0
        for r in row_list:
            # Ensure rollup_id exists for kpi_rollups
            if "kpi_rollups" in target_table and "rollup_id" not in r:
                r["rollup_id"] = f"rlp-{r.get('kpi', 'k')}-{r.get('window_start', count)}"

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
            sql = f"INSERT OR REPLACE INTO {target_table} ({col_names}) VALUES ({placeholders})"
            self._conn.execute(sql, vals)
            count += 1
        return count

    def get_table(self, table: str) -> list[dict[str, Any]]:
        target_table = self._resolve_table_name(table)
        cursor = self._conn.execute(f"SELECT * FROM {target_table}")
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

        stmt_upper = statement.strip().upper()
        # Security guard: block DDL / destructive queries
        destructive_verbs = ("DROP", "ALTER", "TRUNCATE", "DELETE", "UPDATE", "INSERT", "CREATE")
        for verb in destructive_verbs:
            if stmt_upper.startswith(verb) or f" {verb} " in stmt_upper:
                raise ValueError(f"Destructive or DDL SQL ({verb}) is not permitted on DuckDBLedger")

        # Handle BigQuery parameter syntax (@name -> $name for DuckDB)
        adapted_statement = re.sub(r"@([a-zA-Z_][a-zA-Z0-9_]*)", r"$\1", statement)

        if params:
            cursor = self._conn.execute(adapted_statement, params)
        else:
            cursor = self._conn.execute(adapted_statement)

        if not cursor.description:
            return []
        cols = [desc[0] for desc in cursor.description]
        results = []
        for row in cursor.fetchall():
            results.append(dict(zip(cols, row, strict=False)))
        return results
