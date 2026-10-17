"""In-memory BigQuery fake for godview_ledger (TDD §2.2 & Decision A13).

Uses sqlglot (BigQuery -> DuckDB transpilation) and in-memory DuckDB to evaluate real
SQL queries hermetically against the 4 §2.2 ledger tables, raising ValueError on
invalid or unsupported SQL statements.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Iterable
from datetime import datetime
from decimal import Decimal
from typing import Any

import duckdb
import sqlglot
from sqlglot import exp

from godview_shared.contracts import LEDGER_TABLES

_PRIMARY_KEYS: dict[str, str] = {
    "events": "event_id",
    "state_transitions": "transition_id",
    "journeys": "journey_id",
}

_BASE_SCHEMAS: dict[str, dict[str, str]] = {
    "events": {
        "event_id": "VARCHAR",
        "event_time": "TIMESTAMPTZ",
        "env_id": "VARCHAR",
        "journey_id": "VARCHAR",
        "trace_id": "VARCHAR",
        "span_id": "VARCHAR",
        "service": "VARCHAR",
        "operation": "VARCHAR",
        "event_type": "VARCHAR",
        "customer_id": "VARCHAR",
        "loan_id": "VARCHAR",
        "entity": "VARCHAR",
        "entity_id": "VARCHAR",
        "decision": "VARCHAR",
        "foir_pct": "DOUBLE",
        "cibil_score": "BIGINT",
        "emi_inr": "DOUBLE",
        "amount_inr": "DOUBLE",
        "http_status": "BIGINT",
        "duration_ms": "BIGINT",
        "is_synthetic": "BOOLEAN",
        "source": "VARCHAR",
        "attributes": "VARCHAR",
    },
    "state_transitions": {
        "transition_id": "VARCHAR",
        "transition_time": "TIMESTAMPTZ",
        "env_id": "VARCHAR",
        "entity": "VARCHAR",
        "entity_id": "VARCHAR",
        "journey_id": "VARCHAR",
        "status_from": "VARCHAR",
        "status_to": "VARCHAR",
        "actor_service": "VARCHAR",
        "source": "VARCHAR",
        "dwell_ms": "BIGINT",
        "is_synthetic": "BOOLEAN",
        "attributes": "VARCHAR",
    },
    "journeys": {
        "journey_id": "VARCHAR",
        "journey_name": "VARCHAR",
        "started_at": "TIMESTAMPTZ",
        "ended_at": "TIMESTAMPTZ",
        "env_id": "VARCHAR",
        "customer_id": "VARCHAR",
        "steps": "VARCHAR",
        "observed_sequence": "VARCHAR",
        "conformance": "VARCHAR",
        "final_status": "VARCHAR",
        "rupee_value": "DOUBLE",
        "total_duration_ms": "BIGINT",
        "is_synthetic": "BOOLEAN",
        "journey_model_version": "VARCHAR",
    },
    "kpi_rollups": {
        "window_start": "TIMESTAMPTZ",
        "window_end": "TIMESTAMPTZ",
        "env_id": "VARCHAR",
        "journey_name": "VARCHAR",
        "kpi": "VARCHAR",
        "segment": "VARCHAR",
        "value": "DOUBLE",
        "baseline_value": "DOUBLE",
        "zscore": "DOUBLE",
        "cusum": "DOUBLE",
        "sample_n": "BIGINT",
        "breach": "BOOLEAN",
    },
}


def _infer_duck_type(val: Any) -> str:
    if isinstance(val, bool):
        return "BOOLEAN"
    if isinstance(val, int):
        return "BIGINT"
    if isinstance(val, (float, Decimal)):
        return "DOUBLE"
    if isinstance(val, datetime):
        return "TIMESTAMPTZ"
    return "VARCHAR"


def _coerce_value(val: Any, col_type: str) -> Any:
    if val is None:
        return None
    if col_type == "DOUBLE":
        return float(val)
    if col_type == "BIGINT":
        return int(val)
    if col_type == "BOOLEAN":
        return bool(val)
    if col_type == "TIMESTAMPTZ":
        if isinstance(val, datetime):
            return val.isoformat()
        return str(val)
    if isinstance(val, (dict, list)):
        return json.dumps(val, default=str)
    return str(val)


def _dedup_key_for_row(table: str, row: dict[str, Any]) -> str:
    pk = _PRIMARY_KEYS.get(table)
    if pk and row.get(pk) is not None:
        return str(row[pk])
    if table == "kpi_rollups" and "window_start" in row and "kpi" in row:
        return (
            f"{row.get('window_start')}|{row.get('window_end')}|"
            f"{row.get('env_id')}|{row.get('journey_name')}|"
            f"{row.get('kpi')}|{row.get('segment', 'all')}"
        )
    return json.dumps(row, sort_keys=True, default=str)


class InMemoryBigQuery:
    """Hermetic in-memory implementation of BigQuery dataset godview_ledger."""

    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {tbl: [] for tbl in LEDGER_TABLES}
        self._seen_keys: dict[str, set[str]] = {tbl: set() for tbl in LEDGER_TABLES}
        self._query_handlers: list[
            tuple[str, Callable[[str, dict[str, list[dict[str, Any]]]], list[dict[str, Any]]]]
        ] = []

    def insert_rows(self, table: str, rows: Iterable[dict[str, Any]]) -> int:
        """Insert rows into a ledger table with deduplication (Decision A13)."""
        if table not in self._tables:
            raise ValueError(f"Unknown BigQuery ledger table '{table}' (allowed: {LEDGER_TABLES})")
        seen = self._seen_keys[table]
        target = self._tables[table]
        inserted = 0
        for row in rows:
            row_copy = copy.deepcopy(row)
            key_val = _dedup_key_for_row(table, row_copy)
            if key_val in seen:
                continue
            seen.add(key_val)
            target.append(row_copy)
            inserted += 1
        return inserted

    def get_table(self, table: str) -> list[dict[str, Any]]:
        """Return a deep copy of all rows in table."""
        if table not in self._tables:
            raise ValueError(f"Unknown BigQuery ledger table '{table}'")
        return copy.deepcopy(self._tables[table])

    def clear_table(self, table: str) -> None:
        """Clear all rows and dedup keys in table."""
        if table not in self._tables:
            raise ValueError(f"Unknown BigQuery ledger table '{table}'")
        self._tables[table].clear()
        self._seen_keys[table].clear()

    def register_query_handler(
        self,
        substring: str,
        handler: Callable[[str, dict[str, list[dict[str, Any]]]], list[dict[str, Any]]],
    ) -> None:
        """Register a custom SQL handler matched by substring for specialized test hooks."""
        self._query_handlers.append((substring, handler))

    def query(self, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Execute a BigQuery SQL statement against in-memory ledger tables via sqlglot + DuckDB."""
        if not statement or not statement.strip():
            raise ValueError("SQL statement must not be empty")

        for substring, handler in self._query_handlers:
            if substring in statement:
                return handler(statement, self._tables)

        sql_text = statement.strip()
        if params:
            for k, v in params.items():
                replacement = f"'{v}'" if isinstance(v, str) else str(v)
                sql_text = re.sub(rf"@{re.escape(k)}\b", replacement, sql_text)

        # Strip optional 3-part project prefix `project.godview_ledger.<table>` -> `godview_ledger.<table>`
        sql_text = re.sub(
            r"`?[a-zA-Z0-9_-]+\.godview_ledger\.([a-zA-Z0-9_]+)`?",
            r"godview_ledger.\1",
            sql_text,
        )

        try:
            parsed = sqlglot.parse_one(
                sql_text, read="bigquery", error_level=sqlglot.ErrorLevel.RAISE
            )
        except Exception as exc:
            raise ValueError(f"Invalid BigQuery SQL syntax: {exc}") from exc

        if not isinstance(parsed, (exp.Select, exp.Union, exp.Subquery)):
            raise ValueError(
                f"Unsupported BigQuery statement type '{type(parsed).__name__}'; only SELECT queries are allowed"
            )

        try:
            duck_sql = sqlglot.transpile(
                sql_text,
                read="bigquery",
                write="duckdb",
                error_level=sqlglot.ErrorLevel.RAISE,
            )[0]
        except Exception as exc:
            raise ValueError(f"Failed to transpile BigQuery SQL to DuckDB: {exc}") from exc

        conn = duckdb.connect(":memory:")
        try:
            conn.execute("CREATE SCHEMA IF NOT EXISTS godview_ledger")
            for tbl, base_cols in _BASE_SCHEMAS.items():
                col_defs = dict(base_cols)
                rows = self._tables[tbl]
                for r in rows:
                    for k, v in r.items():
                        if k not in col_defs and v is not None:
                            col_defs[k] = _infer_duck_type(v)
                cols_sql = ", ".join(f'"{c}" {t}' for c, t in col_defs.items())
                conn.execute(f'CREATE TABLE main."{tbl}" ({cols_sql})')
                if rows:
                    col_names = list(col_defs.keys())
                    placeholders = ", ".join("?" for _ in col_names)
                    col_list_sql = ", ".join(f'"{c}"' for c in col_names)
                    tuples = [
                        tuple(_coerce_value(r.get(c), col_defs[c]) for c in col_names) for r in rows
                    ]
                    conn.executemany(
                        f'INSERT INTO main."{tbl}" ({col_list_sql}) VALUES ({placeholders})',
                        tuples,
                    )
                conn.execute(f'CREATE VIEW godview_ledger."{tbl}" AS SELECT * FROM main."{tbl}"')

            cursor = conn.execute(duck_sql)
            raw_rows = cursor.fetchall()
            col_names = [desc[0] for desc in (cursor.description or [])]
        except duckdb.Error as exc:
            raise ValueError(f"BigQuery SQL execution error: {exc}") from exc
        finally:
            conn.close()

        results: list[dict[str, Any]] = []
        for raw_row in raw_rows:
            row_dict = dict(zip(col_names, raw_row, strict=True))
            if len(col_names) == 1 and col_names[0].lower().startswith("count"):
                row_dict.setdefault("count", raw_row[0])
            results.append(row_dict)
        return results
