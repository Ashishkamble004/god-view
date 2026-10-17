"""SenseAdapter abstract base class and discovery/health models (SDD §2.2, TDD I-03 & I-06)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from godview_shared.capabilities import CAPABILITY_REGISTRY, CapabilitySpec
from godview_shared.contracts import (
    DiscoveredEdgeDoc,
    DiscoveredEntityDoc,
    DiscoveredServiceDoc,
    TrustDecision,
    TrustViolation,
)


class SenseCategory(StrEnum):
    """The 7 sense categories defined in SDD §2.2 & Agent Spec §5."""

    CODE = "code"
    CONTRACTS = "contracts"
    SYNTHETIC_INTERROGATION = "synthetic_interrogation"
    CHANGE_PLANE = "change_plane"
    SCOPED_OBSERVABILITY = "scoped_observability"
    DATA = "data"
    HUMAN_DOCS_CHAT = "human_docs_chat"


class AdapterHealth(BaseModel):
    """Availability report returned by SenseAdapter.health()."""

    adapter_name: str
    family: Literal["gcp", "kubernetes", "sql", "git", "channels", "local"]
    state: Literal["available", "partial", "unavailable"]
    evidence: dict[str, Any] = Field(default_factory=dict)
    checked_at: datetime


class DiscoveryResult(BaseModel):
    """Candidate graph nodes and edges returned by SenseAdapter.discover()."""

    services: list[DiscoveredServiceDoc] = Field(default_factory=list)
    edges: list[DiscoveredEdgeDoc] = Field(default_factory=list)
    entities: list[DiscoveredEntityDoc] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class SenseAdapter(ABC):
    """Abstract base class for every environment sense adapter (SDD §2.2).

    Enforces TDD I-06 & Decision A10 in execute_capability(): any mutating
    capability call without a valid, authorized TrustDecision raises
    TrustViolation before the adapter implementation runs.
    """

    family: Literal["gcp", "kubernetes", "sql", "git", "channels", "local"]
    name: str

    @abstractmethod
    async def discover(self) -> DiscoveryResult:
        """Discover candidate graph nodes and edges with evidence (read-only, idempotent)."""

    @abstractmethod
    def senses(self) -> list[SenseCategory]:
        """Return sense categories fed by this adapter."""

    @abstractmethod
    def capabilities(self) -> dict[str, CapabilitySpec]:
        """Return capability specs provided by this adapter."""

    @abstractmethod
    async def health(self) -> AdapterHealth:
        """Evaluate availability state (available / partial / unavailable) with evidence."""

    async def execute_capability(
        self,
        capability_id: str,
        args: dict[str, Any],
        trust_decision: TrustDecision | None = None,
    ) -> Any:
        """Execute a capability after enforcing TrustGate authorization (TDD I-06 & Decision A10)."""
        if capability_id not in CAPABILITY_REGISTRY:
            raise KeyError(f"Unknown capability '{capability_id}'")
        spec = CAPABILITY_REGISTRY[capability_id]
        if spec.mutating:
            if trust_decision is None or not trust_decision.authorized:
                raise TrustViolation(
                    f"Mutating capability '{capability_id}' requires an authorized TrustDecision (I-06)"
                )
            if trust_decision.capability_id != capability_id:
                raise TrustViolation(
                    f"TrustDecision capability mismatch: expected '{capability_id}', got '{trust_decision.capability_id}'"
                )
            if trust_decision.action_type != capability_id:
                raise TrustViolation(
                    f"TrustDecision action_type mismatch: expected '{capability_id}', got '{trust_decision.action_type}'"
                )
            if trust_decision.required_level != spec.min_level:
                raise TrustViolation(
                    f"TrustDecision required_level mismatch for '{capability_id}': expected {spec.min_level}, got {trust_decision.required_level}"
                )
            if trust_decision.blast_radius_resources != 1:
                raise TrustViolation(
                    f"Blast radius must be 1 resource per action (got {trust_decision.blast_radius_resources})"
                )
            if spec.sovereign_only:
                if trust_decision.agent_id != "sovereign":
                    raise TrustViolation(
                        f"Sovereign-only capability '{capability_id}' cannot be invoked by '{trust_decision.agent_id}'"
                    )
            else:
                if trust_decision.sa_to_impersonate != "godview-act-sa":
                    raise TrustViolation(
                        f"Mutating capability '{capability_id}' must impersonate 'godview-act-sa'"
                    )
                if trust_decision.effective_level < 2 and spec.min_level >= 2:
                    raise TrustViolation(
                        f"Mutating capability '{capability_id}' refused: effective_level={trust_decision.effective_level} < 2"
                    )
                if trust_decision.effective_level < 1:
                    raise TrustViolation(
                        f"Mutating capability '{capability_id}' refused: effective_level={trust_decision.effective_level} < 1"
                    )
                if (
                    trust_decision.effective_level == 2
                    or trust_decision.effective_level < spec.min_level
                ) and not trust_decision.approval_id:
                    raise TrustViolation(
                        f"Mutating capability '{capability_id}' at effective_level={trust_decision.effective_level} (min_level={spec.min_level}) requires approval_id"
                    )
                if spec.customer_comms and not trust_decision.approval_id:
                    raise TrustViolation(
                        f"Customer communication capability '{capability_id}' is capped at L2 and always requires approval_id"
                    )
                if (
                    spec.money_movement
                    and trust_decision.effective_level < 4
                    and not trust_decision.approval_id
                ):
                    raise TrustViolation(
                        f"Money-movement capability '{capability_id}' requires L4 whitelist or explicit approval_id (Decision A10)"
                    )
        return await self._execute_capability_impl(capability_id, args, trust_decision)

    async def _execute_capability_impl(
        self,
        capability_id: str,
        args: dict[str, Any],
        trust_decision: TrustDecision | None = None,
    ) -> Any:
        """Subclass hook to execute the capability once TrustGate checks pass."""
        raise NotImplementedError(
            f"Adapter '{self.name}' does not implement capability '{capability_id}'"
        )
