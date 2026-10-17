"""Types, result models, and exceptions for God-View remediation executors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

try:
    from godview_shared.contracts import ExecutionResult, VerificationCheck
except ImportError:

    class VerificationCheck(BaseModel):
        """Single post-execution verification assertion."""

        check: str
        passed: bool
        evidence: Any = None

    class ExecutionResult(BaseModel):
        """Standard outcome returned by every remediation executor."""

        ok: bool
        before: Any = None
        after: Any = None
        verification: list[VerificationCheck] = Field(default_factory=list)
        reverted: bool = False
        revert_error: str | None = None
        error: str | None = None


class NoExecutorError(Exception):
    """Raised when capabilities:invoke is called for an unhandled capability ID (HTTP 501)."""

    def __init__(self, capability_id: str) -> None:
        super().__init__(f"No executor registered for capability '{capability_id}'")
        self.capability_id = capability_id
        self.status_code = 501


class ScopeViolationError(Exception):
    """Raised when an action violates the resource scope rule (plan §3.4 item 5) (HTTP 403)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.status_code = 403


class BaseExecutor(ABC):
    """Abstract base class for all God-View capability executors."""

    @abstractmethod
    async def execute(
        self,
        ctx: Any,
        args: dict[str, Any],
        target_resource: str,
        *,
        agent_id: str | None = None,
        approval_id: str | None = None,
        **kwargs: Any,
    ) -> ExecutionResult:
        """Execute the capability mutation with post-execution verification and auto-rollback."""
        raise NotImplementedError


__all__ = [
    "BaseExecutor",
    "ExecutionResult",
    "NoExecutorError",
    "ScopeViolationError",
    "VerificationCheck",
]
