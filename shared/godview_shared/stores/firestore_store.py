"""Real Firestore DocumentStore implementation for God-View Sovereign."""

from __future__ import annotations

import copy
import os
from collections.abc import Callable, Sequence
from typing import Any

from godview_shared.fakes.firestore import ALL_FIRESTORE_COLLECTIONS


def _wrap_vectors(data: dict[str, Any]) -> dict[str, Any]:
    """Wrap 768-dimensional float lists in Firestore Vector objects."""
    try:
        from google.cloud.firestore_v1.vector import Vector
    except ImportError:
        return data

    out: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, (list, tuple)) and len(v) == 768 and all(isinstance(x, (int, float)) for x in v):
            out[k] = Vector(list(v))
        elif isinstance(v, dict):
            out[k] = _wrap_vectors(v)
        else:
            out[k] = v
    return out


def _unwrap_vectors(data: dict[str, Any]) -> dict[str, Any]:
    """Convert Firestore Vector objects back to standard Python float lists."""
    out: dict[str, Any] = {}
    for k, v in data.items():
        if hasattr(v, "__class__") and v.__class__.__name__ == "Vector":
            out[k] = list(v)
        elif isinstance(v, dict):
            out[k] = _unwrap_vectors(v)
        else:
            out[k] = v
    return out


def _sanitize_doc_id(doc_id: str) -> str:
    """Sanitize a document ID so it does not contain forward slashes."""
    return str(doc_id).replace("/", "__")


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

    def _validate_collection(self, collection: str) -> None:
        if collection not in ALL_FIRESTORE_COLLECTIONS:
            # Allow known subcollections or dynamic extensions
            if not collection.startswith("test_") and collection not in ALL_FIRESTORE_COLLECTIONS:
                raise ValueError(
                    f"Unknown Firestore collection '{collection}' (allowed: {ALL_FIRESTORE_COLLECTIONS})"
                )

    def set(
        self,
        collection: str,
        doc_id: str,
        data: dict[str, Any],
        merge: bool = False,
    ) -> dict[str, Any]:
        """Create or update a document in a top-level collection."""
        self._validate_collection(collection)
        safe_id = _sanitize_doc_id(doc_id)
        client = self._get_client()
        stored = copy.deepcopy(data)
        stored["_id"] = doc_id
        ref = client.collection(collection).document(safe_id)
        firestore_data = _wrap_vectors(stored)
        ref.set(firestore_data, merge=merge)
        return copy.deepcopy(stored)

    def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        """Fetch a single document by ID, or None if not found."""
        self._validate_collection(collection)
        safe_id = _sanitize_doc_id(doc_id)
        client = self._get_client()
        ref = client.collection(collection).document(safe_id)
        snap = ref.get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        data["_id"] = data.get("_id") or doc_id
        unwrapped = _unwrap_vectors(data)
        return copy.deepcopy(unwrapped)

    def delete(self, collection: str, doc_id: str) -> bool:
        """Delete a document by ID."""
        self._validate_collection(collection)
        safe_id = _sanitize_doc_id(doc_id)
        client = self._get_client()
        ref = client.collection(collection).document(safe_id)
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
        self._validate_collection(collection)
        client = self._get_client()
        from google.cloud.firestore_v1.base_query import FieldFilter

        col_ref = client.collection(collection)
        query = col_ref

        if where:
            for k, v in where.items():
                query = query.where(filter=FieldFilter(k, "==", v))

        if cursor is not None:
            cursor_ref = col_ref.document(_sanitize_doc_id(cursor))
            cursor_snap = cursor_ref.get()
            if cursor_snap.exists:
                query = query.start_after(cursor_snap)

        # If predicate is NOT provided, we can push down limit to Firestore directly
        if predicate is None and limit is not None:
            query = query.limit(limit)

        results: list[dict[str, Any]] = []
        for snap in query.stream():
            d = snap.to_dict() or {}
            d["_id"] = d.get("_id") or snap.id
            unwrapped = _unwrap_vectors(d)
            if predicate is None or predicate(unwrapped):
                results.append(copy.deepcopy(unwrapped))
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
        """Create or update a subcollection document."""
        self._validate_collection(collection)
        client = self._get_client()
        stored = copy.deepcopy(data)
        stored["_id"] = sub_id
        ref = (
            client.collection(collection)
            .document(_sanitize_doc_id(doc_id))
            .collection(subcollection)
            .document(_sanitize_doc_id(sub_id))
        )
        firestore_data = _wrap_vectors(stored)
        ref.set(firestore_data)
        return copy.deepcopy(stored)

    def get_subdoc(
        self,
        collection: str,
        doc_id: str,
        subcollection: str,
        sub_id: str,
    ) -> dict[str, Any] | None:
        """Fetch a subcollection document by ID, or None if not found."""
        self._validate_collection(collection)
        client = self._get_client()
        ref = (
            client.collection(collection)
            .document(_sanitize_doc_id(doc_id))
            .collection(subcollection)
            .document(_sanitize_doc_id(sub_id))
        )
        snap = ref.get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        data["_id"] = data.get("_id") or sub_id
        unwrapped = _unwrap_vectors(data)
        return copy.deepcopy(unwrapped)

    def list_subdocs(
        self,
        collection: str,
        doc_id: str,
        subcollection: str,
    ) -> list[dict[str, Any]]:
        """List all documents in a subcollection."""
        self._validate_collection(collection)
        client = self._get_client()
        query = (
            client.collection(collection)
            .document(_sanitize_doc_id(doc_id))
            .collection(subcollection)
        )
        results: list[dict[str, Any]] = []
        for snap in query.stream():
            d = snap.to_dict() or {}
            d["_id"] = d.get("_id") or snap.id
            unwrapped = _unwrap_vectors(d)
            results.append(copy.deepcopy(unwrapped))
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
        self._validate_collection(collection)
        if len(query_vector) != 768:
            raise ValueError(
                f"Query vector must be 768 dimensions (got {len(query_vector)})"
            )

        client = self._get_client()
        from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
        from google.cloud.firestore_v1.vector import Vector

        col_ref = client.collection(collection)
        query = col_ref.find_nearest(
            vector_field=vector_field,
            query_vector=Vector(list(query_vector)),
            distance_measure=DistanceMeasure.COSINE,
            limit=limit,
            distance_result_field="vector_distance",
        )
        scored: list[tuple[dict[str, Any], float]] = []
        for snap in query.stream():
            d = snap.to_dict() or {}
            d["_id"] = snap.id
            dist = float(d.pop("vector_distance", d.pop("_vector_distance", 0.0)))
            sim = 1.0 - dist
            if sim >= min_similarity:
                unwrapped = _unwrap_vectors(d)
                scored.append((copy.deepcopy(unwrapped), sim))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]
