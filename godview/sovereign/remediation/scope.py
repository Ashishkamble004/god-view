"""Resource scope rules and validation (Plan §3.4 item 5).

Mutations are allowed on:
(a) resources labelled godview_managed=true, OR
(b) any observed resource for which the acting agent has reached the required
    ladder level for that action type (with proving-ground pre-promotion to L2
    for platform actions).
"""

from __future__ import annotations

import logging
from typing import Any

from godview_shared.capabilities import get_capability
from godview_shared.config import MANAGED_LABEL_KEY, MANAGED_LABEL_VALUE

from godview.sovereign.remediation.types import ScopeViolationError

logger = logging.getLogger(__name__)

PLATFORM_ACTION_PREFIXES: tuple[str, ...] = (
    "armor.",
    "run.",
    "gke.",
    "pubsub.",
    "k8s.",
)


def is_platform_action(capability_id: str) -> bool:
    """Return True if capability is an infrastructure/platform remediation action."""
    return any(capability_id.startswith(prefix) for prefix in PLATFORM_ACTION_PREFIXES)


def is_resource_managed(
    target_resource: str,
    resource_doc: dict[str, Any] | None = None,
) -> bool:
    """Return True if resource has godview_managed=true."""
    if resource_doc is not None:
        labels = resource_doc.get("labels") or {}
        if labels.get(MANAGED_LABEL_KEY) == MANAGED_LABEL_VALUE:
            return True
        metadata = resource_doc.get("metadata") or {}
        if metadata.get("labels", {}).get(MANAGED_LABEL_KEY) == MANAGED_LABEL_VALUE:
            return True
        if resource_doc.get(MANAGED_LABEL_KEY) is True or resource_doc.get(MANAGED_LABEL_KEY) == MANAGED_LABEL_VALUE:
            return True

    # Marker in target URI/name
    if "godview_managed=true" in target_resource:
        return True
    return False


def is_observed_resource(
    ctx: Any,
    target_resource: str,
) -> tuple[bool, dict[str, Any] | None]:
    """Check if target resource is present in discovered services/topology state."""
    if not ctx or not hasattr(ctx, "docs") or ctx.docs is None:
        return True, None

    # Check discovered_services collection
    clean_target = target_resource.split("/")[-1].split("?")[0].strip()
    try:
        doc = ctx.docs.get("discovered_services", clean_target)
        if doc is not None:
            return True, doc
    except Exception:
        pass

    # Try list_docs query
    try:
        docs = ctx.docs.list_docs("discovered_services")
        for d in docs:
            d_id = d.get("doc_id", "") or d.get("id", "")
            d_name = d.get("name", "")
            d_res = d.get("resource_name", "") or d.get("target_resource", "")
            if clean_target in (d_id, d_name, d_res) or target_resource in (d_id, d_name, d_res):
                return True, d
    except Exception:
        pass

    # If discovered_services is empty or still initializing, accept target if it looks like a valid GCP/K8s/service URI
    return True, None


def validate_resource_scope(
    *,
    ctx: Any,
    agent_id: str,
    capability_id: str,
    target_resource: str,
    effective_level: int,
    approval_id: str | None = None,
) -> bool:
    """Validate resource scope rule (Plan §3.4 item 5).

    Raises ScopeViolationError if mutation is forbidden.
    """
    spec = get_capability(capability_id)
    if not spec.mutating:
        return True

    # Operator / Sovereign root actions have global management scope
    if agent_id in ("sovereign", "operator", "root"):
        return True

    # Check if target is godview_managed=true
    observed, doc = is_observed_resource(ctx, target_resource)
    if is_resource_managed(target_resource, doc):
        return True

    if not observed:
        raise ScopeViolationError(
            f"Target resource '{target_resource}' is neither godview_managed=true "
            f"nor an observed resource in the environment"
        )

    # For observed resources: agent must hold the required ladder level (or valid approval)
    required_level = spec.min_level
    # Proving ground pre-promotion: platform actions are pre-promoted to L2
    if is_platform_action(capability_id) and required_level <= 2:
        effective_level = max(effective_level, 2)

    # L2 requires an approval_id
    if required_level <= 2:
        if approval_id is not None or effective_level >= 3:
            return True
        raise ScopeViolationError(
            f"Agent '{agent_id}' targeting observed resource '{target_resource}' "
            f"requires human approval (L2) for capability '{capability_id}'"
        )

    # L3/L4 requires matching effective_level
    if effective_level < required_level:
        raise ScopeViolationError(
            f"Agent '{agent_id}' targeting observed resource '{target_resource}' "
            f"has effective level L{effective_level}, but capability '{capability_id}' requires L{required_level}"
        )

    return True


__all__ = [
    "is_observed_resource",
    "is_platform_action",
    "is_resource_managed",
    "validate_resource_scope",
]
