"""Local SQLite-backed DocumentStore implementation for God-View local mode."""

from __future__ import annotations

import copy
import json
import math
import os
import sqlite3
from collections.abc import Callable, Sequence
from typing import Any


class LocalDocumentStore:
    """SQLite-backed DocumentStore for local development and offline runs."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or os.environ.get("LOCAL_DOCS_DB_PATH", ":memory:")
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    collection TEXT NOT NULL,
                    doc_id TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    PRIMARY KEY (collection, doc_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS subdocuments (
                    collection TEXT NOT NULL,
                    doc_id TEXT NOT NULL,
                    subcollection TEXT NOT NULL,
                    sub_id TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    PRIMARY KEY (collection, doc_id, subcollection, sub_id)
                )
                """
            )

    def set(
        self,
        collection: str,
        doc_id: str,
        data: dict[str, Any],
        merge: bool = False,
    ) -> dict[str, Any]:
        stored = copy.deepcopy(data)
        stored["_id"] = doc_id
        conn = self._get_conn()
        with conn:
            if merge:
                cur = conn.execute(
                    "SELECT data_json FROM documents WHERE collection = ? AND doc_id = ?",
                    (collection, doc_id),
                )
                row = cur.fetchone()
                if row:
                    existing = json.loads(row["data_json"])
                    existing.update(stored)
                    stored = existing

            conn.execute(
                """
                INSERT INTO documents (collection, doc_id, data_json)
                VALUES (?, ?, ?)
                ON CONFLICT(collection, doc_id) DO UPDATE SET data_json = excluded.data_json
                """,
                (collection, doc_id, json.dumps(stored)),
            )
        return copy.deepcopy(stored)

    def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT data_json FROM documents WHERE collection = ? AND doc_id = ?",
            (collection, doc_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        data = json.loads(row["data_json"])
        data["_id"] = doc_id
        return copy.deepcopy(data)

    def delete(self, collection: str, doc_id: str) -> bool:
        conn = self._get_conn()
        with conn:
            cur = conn.execute(
                "DELETE FROM documents WHERE collection = ? AND doc_id = ?",
                (collection, doc_id),
            )
            return cur.rowcount > 0

    def list_docs(
        self,
        collection: str,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        where: dict[str, Any] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> list[dict[str, Any]]:
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT doc_id, data_json FROM documents WHERE collection = ? ORDER BY doc_id ASC",
            (collection,),
        )
        rows = cur.fetchall()
        results: list[dict[str, Any]] = []
        started = cursor is None

        for r in rows:
            doc_id = r["doc_id"]
            if not started:
                if doc_id == cursor:
                    started = True
                continue

            doc = json.loads(r["data_json"])
            doc["_id"] = doc_id

            if where:
                if not all(doc.get(k) == v for k, v in where.items()):
                    continue

            if predicate is not None and not predicate(doc):
                continue

            results.append(copy.deepcopy(doc))
            if limit is not None and len(results) >= limit:
                break

        return results

    def set_subdoc(
        self,
        collection: str,
        doc_id: str,
        subcollection: str,
        sub_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        stored = copy.deepcopy(data)
        stored["_id"] = sub_id
        conn = self._get_conn()
        with conn:
            conn.execute(
                """
                INSERT INTO subdocuments (collection, doc_id, subcollection, sub_id, data_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(collection, doc_id, subcollection, sub_id) DO UPDATE SET data_json = excluded.data_json
                """,
                (collection, doc_id, subcollection, sub_id, json.dumps(stored)),
            )
        return copy.deepcopy(stored)

    def get_subdoc(
        self,
        collection: str,
        doc_id: str,
        subcollection: str,
        sub_id: str,
    ) -> dict[str, Any] | None:
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT data_json FROM subdocuments WHERE collection = ? AND doc_id = ? AND subcollection = ? AND sub_id = ?",
            (collection, doc_id, subcollection, sub_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        data = json.loads(row["data_json"])
        data["_id"] = sub_id
        return copy.deepcopy(data)

    def list_subdocs(
        self,
        collection: str,
        doc_id: str,
        subcollection: str,
    ) -> list[dict[str, Any]]:
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT sub_id, data_json FROM subdocuments WHERE collection = ? AND doc_id = ? AND subcollection = ?",
            (collection, doc_id, subcollection),
        )
        rows = cur.fetchall()
        results: list[dict[str, Any]] = []
        for r in rows:
            doc = json.loads(r["data_json"])
            doc["_id"] = r["sub_id"]
            results.append(copy.deepcopy(doc))
        return results

    def find_nearest(
        self,
        collection: str,
        vector_field: str,
        query_vector: Sequence[float],
        limit: int = 3,
        min_similarity: float = 0.0,
    ) -> list[tuple[dict[str, Any], float]]:
        docs = self.list_docs(collection)
        scored: list[tuple[dict[str, Any], float]] = []

        q_norm = math.sqrt(sum(x * x for x in query_vector))
        if q_norm == 0:
            return []

        for d in docs:
            v = d.get(vector_field)
            if not v or not isinstance(v, (list, tuple)):
                continue
            dot = sum(a * b for a, b in zip(query_vector, v, strict=False))
            v_norm = math.sqrt(sum(x * x for x in v))
            sim = dot / (q_norm * v_norm) if v_norm > 0 else 0.0
            if sim >= min_similarity:
                scored.append((d, sim))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]
