"""Production BigQuery LedgerStore implementation for God-View Sovereign."""

from __future__ import annotations

import copy
import logging
import os
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Any

logger = logging.getLogger("godview.stores.bigquery")


def _serialize_bq_row(row: dict[str, Any]) -> dict[str, Any]:
    """Serialize row values for BigQuery JSON insertion."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, dict):
            out[k] = _serialize_bq_row(v)
        elif isinstance(v, (list, tuple)):
            out[k] = [
                _serialize_bq_row(item) if isinstance(item, dict)
                else (item.isoformat() if isinstance(item, datetime)
                else (float(item) if isinstance(item, Decimal) else item))
                for item in v
            ]
        else:
            out[k] = v
    return out


class BigQueryLedger:
    """Production BigQuery LedgerStore for godview_ledger."""

    def __init__(
        self,
        project: str | None = None,
        dataset: str | None = None,
        client: Any = None,
    ) -> None:
        self.project = (
            project
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or ""
        )
        self.dataset = (
            dataset
            or os.environ.get("BIGQUERY_LEDGER_DATASET")
            or "godview_ledger"
        )
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            from google.cloud import bigquery

            self._client = bigquery.Client(project=self.project or None)
        return self._client

    def insert_rows(self, table: str, rows: Iterable[dict[str, Any]]) -> int:
        """Insert rows into a ledger table with dedup row_ids and typed serialization."""
        row_list = [copy.deepcopy(r) for r in rows]
        if not row_list:
            return 0

        client = self._get_client()
        dataset_id = self.dataset
        table_short = table.strip().replace("`", "")
        if "." in table_short:
            table_short = table_short.split(".")[-1]

        table_ref = f"{self.project}.{dataset_id}.{table_short}" if self.project else f"{dataset_id}.{table_short}"

        serialized_rows: list[dict[str, Any]] = []
        row_ids: list[str] = []
        for r in row_list:
            serialized = _serialize_bq_row(r)
            serialized_rows.append(serialized)
            # Derive dedup row_id from primary keys if available
            rid = (
                str(r.get("event_id")
                or r.get("transition_id")
                or r.get("journey_id")
                or r.get("rollup_id")
                or "")
            )
            row_ids.append(rid if rid else "")

        # Only pass row_ids if all rows have non-empty IDs
        use_row_ids = row_ids if all(bool(x) for x in row_ids) else None
        errors = client.insert_rows_json(table_ref, serialized_rows, row_ids=use_row_ids)
        if errors:
            logger.error("BigQuery insert_rows_json errors on %s: %s", table_ref, errors)
            raise RuntimeError(f"BigQuery insert failed on {table_ref}: {errors}")
        return len(serialized_rows)

    def get_table(self, table: str) -> list[dict[str, Any]]:
        """Return rows in a ledger table."""
        dataset_id = self.dataset
        table_short = table.strip().replace("`", "")
        if "." in table_short:
            table_short = table_short.split(".")[-1]
        table_ref = f"`{self.project}.{dataset_id}.{table_short}`" if self.project else f"`{dataset_id}.{table_short}`"
        query_sql = f"SELECT * FROM {table_ref} LIMIT 1000"
        return self.query(query_sql)

    def query(
        self,
        statement: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a read-only SQL query against BigQuery ledger tables."""
        if not statement or not statement.strip():
            raise ValueError("SQL statement must not be empty")

        # Read-only guard: block DDL and DML
        stmt_upper = statement.strip().upper()
        if not (stmt_upper.startswith("SELECT") or stmt_upper.startswith("WITH")):
            raise ValueError("Only read queries (SELECT / WITH) are permitted on BigQueryLedger")

        client = self._get_client()
        from google.cloud import bigquery

        job_config = bigquery.QueryJobConfig()
        if self.project:
            job_config.default_dataset = f"{self.project}.{self.dataset}"
        else:
            job_config.default_dataset = self.dataset

        if params:
            query_params = []
            for k, v in params.items():
                if isinstance(v, bool):
                    p_type = "BOOL"
                elif isinstance(v, int):
                    p_type = "INT64"
                elif isinstance(v, float):
                    p_type = "FLOAT64"
                else:
                    p_type = "STRING"
                query_params.append(
                    bigquery.ScalarQueryParameter(k, p_type, v)
                )
            job_config.query_parameters = query_params

        query_job = client.query(statement, job_config=job_config)
        results: list[dict[str, Any]] = []
        for row in query_job.result(timeout=30.0):
            results.append(dict(row.items()))
        return results
