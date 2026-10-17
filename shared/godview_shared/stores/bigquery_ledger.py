"""Production BigQuery LedgerStore implementation for God-View Sovereign."""

from __future__ import annotations

import copy
import logging
import os
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger("godview.stores.bigquery")


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
        """Insert rows into a ledger table, returning the count of rows successfully inserted."""
        row_list = [copy.deepcopy(r) for r in rows]
        if not row_list:
            return 0

        client = self._get_client()
        dataset_id = self.dataset
        table_ref = f"{self.project}.{dataset_id}.{table}" if self.project else f"{dataset_id}.{table}"

        errors = client.insert_rows_json(table_ref, row_list)
        if errors:
            logger.error("BigQuery insert_rows_json errors on %s: %s", table_ref, errors)
            raise RuntimeError(f"BigQuery insert failed on {table_ref}: {errors}")
        return len(row_list)

    def get_table(self, table: str) -> list[dict[str, Any]]:
        """Return all rows in a ledger table."""
        dataset_id = self.dataset
        table_ref = f"`{self.project}.{dataset_id}.{table}`" if self.project else f"`{dataset_id}.{table}`"
        query_sql = f"SELECT * FROM {table_ref}"
        return self.query(query_sql)

    def query(
        self,
        statement: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a SQL query against BigQuery ledger tables."""
        if not statement or not statement.strip():
            raise ValueError("SQL statement must not be empty")

        client = self._get_client()
        from google.cloud import bigquery

        job_config = bigquery.QueryJobConfig()
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
        for row in query_job.result():
            results.append(dict(row.items()))
        return results
