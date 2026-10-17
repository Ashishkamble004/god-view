"""Protocols for God-View state stores and runtime infrastructure.

Defines the DocumentStore, LedgerStore, EventBus, and AgentRuntime protocols
that all storage backends (both in-memory fakes and production GCP implementations)
must satisfy.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DocumentStore(Protocol):
    """Document store interface (matching Firestore and InMemoryFirestore)."""

    def set(
        self,
        collection: str,
        doc_id: str,
        data: dict[str, Any],
        merge: bool = False,
    ) -> dict[str, Any]:
        """Create or update a document in a collection."""
        ...

    def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        """Fetch a single document by ID, or None if not found."""
        ...

    def delete(self, collection: str, doc_id: str) -> bool:
        """Delete a document by ID."""
        ...

    def list_docs(
        self,
        collection: str,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        where: dict[str, Any] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> list[dict[str, Any]]:
        """List documents in a collection with optional filters and pagination."""
        ...

    def set_subdoc(
        self,
        collection: str,
        doc_id: str,
        subcollection: str,
        sub_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Create or update a subcollection document."""
        ...

    def get_subdoc(
        self,
        collection: str,
        doc_id: str,
        subcollection: str,
        sub_id: str,
    ) -> dict[str, Any] | None:
        """Fetch a subcollection document by ID, or None if not found."""
        ...

    def list_subdocs(
        self,
        collection: str,
        doc_id: str,
        subcollection: str,
    ) -> list[dict[str, Any]]:
        """List all documents in a subcollection."""
        ...

    def find_nearest(
        self,
        collection: str,
        vector_field: str,
        query_vector: Sequence[float],
        limit: int = 3,
        min_similarity: float = 0.0,
    ) -> list[tuple[dict[str, Any], float]]:
        """Execute vector KNN search on indexed vector fields."""
        ...


@runtime_checkable
class LedgerStore(Protocol):
    """Ledger store interface (matching BigQuery and InMemoryBigQuery)."""

    def insert_rows(self, table: str, rows: Iterable[dict[str, Any]]) -> int:
        """Insert rows into a ledger table, returning the count of rows inserted."""
        ...

    def get_table(self, table: str) -> list[dict[str, Any]]:
        """Return a copy of all rows in a ledger table."""
        ...

    def query(
        self,
        statement: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a SQL statement against ledger tables."""
        ...


@runtime_checkable
class EventBus(Protocol):
    """Event bus interface (matching Pub/Sub and InMemoryPubSub)."""

    async def publish(
        self,
        topic: str,
        event: Any,
    ) -> str:
        """Publish a CloudEvent or dict payload to a topic."""
        ...

    def subscribe(
        self,
        topic: str,
        handler: Callable[[dict[str, Any]], Any],
    ) -> None:
        """Register a subscriber callback on a topic."""
        ...


@runtime_checkable
class AgentRuntime(Protocol):
    """Agent runtime interface (matching Vertex Agent Engine and InMemoryAgentEngine)."""

    def deploy_agent(
        self,
        agent_id: str,
        agent_type: str,
        min_instances: int = 0,
        max_instances: int = 2,
        cpu: str = "1",
        memory: str = "2Gi",
        card: Any = None,
        subject: str | None = None,
        env_vars: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Deploy an agent to the runtime."""
        ...

    def set_min_instances(self, agent_id: str, min_instances: int) -> dict[str, Any]:
        """Update an agent's minimum instances (e.g. earned warmth 0 <-> 1)."""
        ...

    def retire_agent(self, agent_id: str) -> dict[str, Any]:
        """Retire an agent and cool its instances to 0."""
        ...

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        """Get deployed agent metadata."""
        ...

    def list_agents(self) -> list[dict[str, Any]]:
        """List all deployed agents."""
        ...

    async def send_a2a(self, message: Any) -> dict[str, Any]:
        """Dispatch an A2A message to a target agent."""
        ...
