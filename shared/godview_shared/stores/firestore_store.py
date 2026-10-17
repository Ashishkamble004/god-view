"""Real Firestore DocumentStore implementation for God-View Sovereign."""

from __future__ import annotations

import copy
import os
from collections.abc import Callable, Sequence
from typing import Any


class FirestoreStore:
    """Production Firestore Native DocumentStore."""

    def __init__(
        self,
        project: str | None = None,
        database: str | None = None,
        client: Any = None,
    ) -> None:
        self.project = (
            project
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or ""
        )
        self.database = (
            database
            or os.environ.get("FIRESTORE_DATABASE")
            or "(default)"
        )
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            from google.cloud import firestore

            db_name = self.database if self.database and self.database != "(default)" else None
            self._client = firestore.Client(
                project=self.project or None,
                database=db_name,
            )
        return self._client

    def set(
        self,
        collection: str,
        doc_id: str,
        data: dict[str, Any],
        merge: bool = False,
    ) -> dict[str, Any]:
        """Create or update a document in a top-level collection."""
        client = self._get_client()
        stored = copy.deepcopy(data)
        stored["_id"] = doc_id
        ref = client.collection(collection).document(doc_id)
        ref.set(stored, merge=merge)
        return copy.deepcopy(stored)

    def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        """Fetch a single document by ID, or None if not found."""
        client = self._get_client()
        ref = client.collection(collection).document(doc_id)
        snap = ref.get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        data["_id"] = doc_id
        return copy.deepcopy(data)

    def delete(self, collection: str, doc_id: str) -> bool:
        """Delete a document by ID."""
        client = self._get_client()
        ref = client.collection(collection).document(doc_id)
        snap = ref.get()
        if not snap.exists:
            return False
        ref.delete()
        return True

    def list_docs(
        self,
        collection: str,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        where: dict[str, Any] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> list[dict[str, Any]]:
        """List documents in a collection with optional filters and pagination."""
        client = self._get_client()
        from google.cloud.firestore_v1.base_query import FieldFilter

        col_ref = client.collection(collection)
        query = col_ref

        if where:
            for k, v in where.items():
                query = query.where(filter=FieldFilter(k, "==", v))

        if cursor is not None:
            cursor_ref = col_ref.document(cursor)
            cursor_snap = cursor_ref.get()
            if cursor_snap.exists:
                query = query.start_after(cursor_snap)

        if limit is not None:
            query = query.limit(limit)

        results: list[dict[str, Any]] = []
        for snap in query.stream():
            d = snap.to_dict() or {}
            d["_id"] = snap.id
            if predicate is None or predicate(d):
                results.append(copy.deepcopy(d))
        return results

    def set_subdoc(
        self,
        collection: str,
        doc_id: str,
        subcollection: str,
        sub_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Create or update a subcollection document."""
        client = self._get_client()
        stored = copy.deepcopy(data)
        stored["_id"] = sub_id
        ref = (
            client.collection(collection)
            .document(doc_id)
            .collection(subcollection)
            .document(sub_id)
        )
        ref.set(stored)
        return copy.deepcopy(stored)

    def get_subdoc(
        self,
        collection: str,
        doc_id: str,
        subcollection: str,
        sub_id: str,
    ) -> dict[str, Any] | None:
        """Fetch a subcollection document by ID, or None if not found."""
        client = self._get_client()
        ref = (
            client.collection(collection)
            .document(doc_id)
            .collection(subcollection)
            .document(sub_id)
        )
        snap = ref.get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        data["_id"] = sub_id
        return copy.deepcopy(data)

    def list_subdocs(
        self,
        collection: str,
        doc_id: str,
        subcollection: str,
    ) -> list[dict[str, Any]]:
        """List all documents in a subcollection."""
        client = self._get_client()
        query = (
            client.collection(collection)
            .document(doc_id)
            .collection(subcollection)
        )
        results: list[dict[str, Any]] = []
        for snap in query.stream():
            d = snap.to_dict() or {}
            d["_id"] = snap.id
            results.append(copy.deepcopy(d))
        return results

    def find_nearest(
        self,
        collection: str,
        vector_field: str,
        query_vector: Sequence[float],
        limit: int = 3,
        min_similarity: float = 0.0,
    ) -> list[tuple[dict[str, Any], float]]:
        """Execute vector KNN search on indexed vector fields."""
        client = self._get_client()
        from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
        from google.cloud.firestore_v1.vector import Vector

        col_ref = client.collection(collection)
        query = col_ref.find_nearest(
            vector_field=vector_field,
            query_vector=Vector(list(query_vector)),
            distance_measure=DistanceMeasure.COSINE,
            limit=limit,
            distance_result_field="_vector_distance",
        )
        scored: list[tuple[dict[str, Any], float]] = []
        for snap in query.stream():
            d = snap.to_dict() or {}
            d["_id"] = snap.id
            dist = float(d.pop("_vector_distance", 0.0))
            sim = 1.0 - dist
            if sim >= min_similarity:
                scored.append((copy.deepcopy(d), sim))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]
