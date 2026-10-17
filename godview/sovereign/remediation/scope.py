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
    ctx: Any = None,
) -> bool:
    """Return True if resource has godview_managed=true in labels/metadata."""
    if resource_doc is not None:
        labels = resource_doc.get("labels") or {}
        if labels.get(MANAGED_LABEL_KEY) == MANAGED_LABEL_VALUE:
            return True
        metadata = resource_doc.get("metadata") or {}
        if metadata.get("labels", {}).get(MANAGED_LABEL_KEY) == MANAGED_LABEL_VALUE:
            return True
        if (
            resource_doc.get(MANAGED_LABEL_KEY) is True
            or resource_doc.get(MANAGED_LABEL_KEY) == MANAGED_LABEL_VALUE
        ):
            return True

    if ctx is not None:
        managed_set = getattr(ctx, "managed_resources", None)
        if managed_set and (target_resource in managed_set):
            return True

    return False


def is_observed_resource(
    ctx: Any,
    target_resource: str,
) -> tuple[bool, dict[str, Any] | None]:
    """Check if target resource is present in discovered services or topology state."""
    if not ctx:
        return False, None

    clean_target = target_resource.split("/")[-1].split("?")[0].strip()

    # 1. Check ctx.observed_resources set if configured
    observed_set = getattr(ctx, "observed_resources", None)
    if observed_set and (target_resource in observed_set or clean_target in observed_set):
        return True, {"resource_name": target_resource}

    # 2. Check ctx.topology
    topology = getattr(ctx, "topology", None)
    if topology is not None:
        nodes = getattr(topology, "nodes", {})
        if isinstance(nodes, dict):
            for node_id, node_data in nodes.items():
                node_name = str(node_data.get("name", "")) if isinstance(node_data, dict) else ""
                if target_resource in (node_id, node_name) or clean_target in (node_id, node_name):
                    return True, node_data if isinstance(node_data, dict) else {"id": node_id}
        elif isinstance(nodes, list):
            for n in nodes:
                n_id = n.get("id") or n.get("name") or n.get("resource_name")
                if target_resource in (n_id, str(n.get("resource_name", ""))) or clean_target in (
                    n_id,
                    str(n.get("resource_name", "")),
                ):
                    return True, n

    # 3. Check ctx.discovered_services attribute
    disc_services = getattr(ctx, "discovered_services", None)
    if disc_services:
        if isinstance(disc_services, dict):
            for s_id, s_data in disc_services.items():
                s_name = str(s_data.get("name", "")) if isinstance(s_data, dict) else ""
                if target_resource in (s_id, s_name) or clean_target in (s_id, s_name):
                    return True, s_data if isinstance(s_data, dict) else {"id": s_id}
        elif isinstance(disc_services, list):
            for s in disc_services:
                s_id = s.get("id") or s.get("name") or s.get("resource_name")
                if target_resource in (s_id, str(s.get("resource_name", ""))) or clean_target in (
                    s_id,
                    str(s.get("resource_name", "")),
                ):
                    return True, s

    # 4. Check ctx.docs discovered_services collection
    docs_store = getattr(ctx, "docs", None)
    if docs_store is not None:
        try:
            doc = docs_store.get("discovered_services", clean_target)
            if doc is not None:
                return True, doc
        except Exception:
            pass
        try:
            docs = docs_store.list_docs("discovered_services")
            for d in docs:
                d_id = d.get("doc_id", "") or d.get("id", "")
                d_name = d.get("name", "")
                d_res = d.get("resource_name", "") or d.get("target_resource", "")
                if clean_target in (d_id, d_name, d_res) or target_resource in (d_id, d_name, d_res):
                    return True, d
        except Exception:
            pass

    return False, None


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

    # Reject attempts to spoof managed status via URI query parameters
    if "?" in target_resource and "godview_managed" in target_resource:
        raise ScopeViolationError(
            f"Resource URI '{target_resource}' contains invalid query parameter scope bypass"
        )

    # Check if target is godview_managed=true or an observed resource
    observed, doc = is_observed_resource(ctx, target_resource)
    if is_resource_managed(target_resource, doc, ctx=ctx):
        return True

    if not observed:
        raise ScopeViolationError(
            f"Target resource '{target_resource}' is neither godview_managed=true "
            f"nor an observed resource in the environment"
        )

    # For observed resources:
    # Verified operator actions have management scope across observed resources
    if agent_id == "operator":
        return True

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
