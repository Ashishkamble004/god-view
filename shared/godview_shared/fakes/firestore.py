"""In-memory Firestore fake with 768-dimensional COSINE KNN search (TDD §2.3 & §7)."""

from __future__ import annotations

import copy
import math
from collections.abc import Callable, Sequence
from typing import Any

from godview_shared.contracts import FIRESTORE_COLLECTIONS, FIRESTORE_VECTOR_INDEXES


def _cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    if len(vec_a) != 768 or len(vec_b) != 768:
        raise ValueError(
            f"Firestore KNN vectors must be 768-dimensional (got {len(vec_a)} and {len(vec_b)})"
        )
    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=True))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class InMemoryFirestore:
    """Hermetic in-memory implementation of Firestore Native DB god-view-memory-db."""

    def __init__(self) -> None:
        self._collections: dict[str, dict[str, dict[str, Any]]] = {
            col: {} for col in FIRESTORE_COLLECTIONS
        }
        self._subcollections: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}

    def _validate_collection(self, collection: str) -> None:
        if collection not in self._collections:
            raise ValueError(
                f"Unknown Firestore collection '{collection}' (allowed: {FIRESTORE_COLLECTIONS})"
            )

    def set(
        self,
        collection: str,
        doc_id: str,
        data: dict[str, Any],
        merge: bool = False,
    ) -> dict[str, Any]:
        """Create or update a document in a valid top-level collection."""
        self._validate_collection(collection)
        bucket = self._collections[collection]
        stored = copy.deepcopy(data)
        stored["_id"] = doc_id
        if merge and doc_id in bucket:
            merged = copy.deepcopy(bucket[doc_id])
            merged.update(stored)
            bucket[doc_id] = merged
        else:
            bucket[doc_id] = stored
        return copy.deepcopy(bucket[doc_id])

    def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        """Fetch a single document by ID, or None if not found."""
        self._validate_collection(collection)
        bucket = self._collections[collection]
        doc = bucket.get(doc_id)
        return copy.deepcopy(doc) if doc is not None else None

    def delete(self, collection: str, doc_id: str) -> bool:
        """Delete a document by ID."""
        self._validate_collection(collection)
        bucket = self._collections[collection]
        if doc_id in bucket:
            del bucket[doc_id]
            return True
        return False

    def list_docs(
        self,
        collection: str,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        where: dict[str, Any] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> list[dict[str, Any]]:
        """List documents in a collection with optional equality filters or predicate."""
        self._validate_collection(collection)
        bucket = self._collections[collection]
        results: list[dict[str, Any]] = []
        for doc in bucket.values():
            if where and any(doc.get(k) != v for k, v in where.items()):
                continue
            if predicate and not predicate(doc):
                continue
            results.append(copy.deepcopy(doc))
        if cursor is not None:
            # Drop elements until after cursor
            idx = -1
            for i, doc in enumerate(results):
                if doc.get("_id") == cursor or doc.get("id") == cursor:
                    idx = i
                    break
            if idx >= 0:
                results = results[idx + 1 :]
        if limit is not None:
            results = results[:limit]
        return results

    def set_subdoc(
        self,
        collection: str,
        doc_id: str,
        subcollection: str,
        sub_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Set a subcollection document (e.g. court_agents/{id}/living_model/{section})."""
        self._validate_collection(collection)
        key = (collection, doc_id, subcollection)
        bucket = self._subcollections.setdefault(key, {})
        stored = copy.deepcopy(data)
        stored["_id"] = sub_id
        bucket[sub_id] = stored
        return copy.deepcopy(stored)

    def get_subdoc(
        self,
        collection: str,
        doc_id: str,
        subcollection: str,
        sub_id: str,
    ) -> dict[str, Any] | None:
        """Get a subcollection document."""
        self._validate_collection(collection)
        bucket = self._subcollections.get((collection, doc_id, subcollection), {})
        doc = bucket.get(sub_id)
        return copy.deepcopy(doc) if doc is not None else None

    def list_subdocs(
        self,
        collection: str,
        doc_id: str,
        subcollection: str,
    ) -> list[dict[str, Any]]:
        """List all documents in a subcollection."""
        self._validate_collection(collection)
        bucket = self._subcollections.get((collection, doc_id, subcollection), {})
        return [copy.deepcopy(doc) for doc in bucket.values()]

    def find_nearest(
        self,
        collection: str,
        vector_field: str,
        query_vector: Sequence[float],
        limit: int = 3,
        min_similarity: float = 0.0,
    ) -> list[tuple[dict[str, Any], float]]:
        """Execute 768-dim COSINE KNN search on one of the 4 indexed vector fields (A17)."""
        self._validate_collection(collection)
        if len(query_vector) != 768:
            raise ValueError(f"Query vector must be 768-dimensional (got {len(query_vector)})")
        if (collection, vector_field) not in FIRESTORE_VECTOR_INDEXES:
            raise ValueError(
                f"No vector index configured for {collection}.{vector_field}; "
                f"configured indexes: {FIRESTORE_VECTOR_INDEXES}"
            )
        bucket = self._collections[collection]
        scored: list[tuple[dict[str, Any], float]] = []
        for doc in bucket.values():
            vec = doc.get(vector_field)
            if vec is None:
                continue
            sim = _cosine_similarity(query_vector, vec)
            if sim >= min_similarity:
                scored.append((copy.deepcopy(doc), sim))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]
