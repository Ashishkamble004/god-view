"""God-View Sovereign Remediation Module (Plan §3.4).

Provides real executors, outcome verification, auto-rollback, capability registry,
and resource scope enforcement.
"""

from __future__ import annotations

from godview.sovereign.remediation.registry import RemediationDispatcher
from godview.sovereign.remediation.scope import (
    is_observed_resource,
    is_platform_action,
    is_resource_managed,
    validate_resource_scope,
)
from godview.sovereign.remediation.types import (
    BaseExecutor,
    ExecutionResult,
    NoExecutorError,
    ScopeViolationError,
    VerificationCheck,
)

__all__ = [
    "BaseExecutor",
    "ExecutionResult",
    "NoExecutorError",
    "RemediationDispatcher",
    "ScopeViolationError",
    "VerificationCheck",
    "is_observed_resource",
    "is_platform_action",
    "is_resource_managed",
    "validate_resource_scope",
]
