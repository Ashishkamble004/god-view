"""Unit tests for Resource Scope Rule (Plan §3.4 item 5).

Mutations allowed on:
(a) resources labelled godview_managed=true, OR
(b) any observed resource for which the acting agent has reached the required ladder level
    for that action type (with proving-ground pre-promotion to L2 for platform actions).
"""

from __future__ import annotations

from typing import Any

import pytest

from godview.sovereign.remediation.scope import validate_resource_scope
from godview.sovereign.remediation.types import ScopeViolationError


class MockContext:
    def __init__(self, docs: dict[str, dict[str, Any]] | None = None) -> None:
        self._data = docs or {}

    @property
    def docs(self) -> Any:
        return self

    def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        return self._data.get(collection, {}).get(doc_id)

    def list_docs(self, collection: str, where: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return list(self._data.get(collection, {}).values())


@pytest.mark.unit
def test_read_only_capability_always_allowed() -> None:
    ctx = MockContext()
    # Read-only capability is always in scope
    assert (
        validate_resource_scope(
            ctx=ctx,
            agent_id="application__svc-01",
            capability_id="logging.query",
            target_resource="random-target",
            effective_level=0,
        )
        is True
    )


@pytest.mark.unit
def test_sovereign_root_operator_always_allowed() -> None:
    ctx = MockContext()
    ctx.observed_resources = {"edge-security-policy"}
    assert (
        validate_resource_scope(
            ctx=ctx,
            agent_id="operator",
            capability_id="armor.set_rule_preview",
            target_resource="edge-security-policy",
            effective_level=0,
        )
        is True
    )

    # But unobserved resource is rejected even for operator
    with pytest.raises(ScopeViolationError, match="neither godview_managed=true nor an observed resource"):
        validate_resource_scope(
            ctx=ctx,
            agent_id="operator",
            capability_id="armor.set_rule_preview",
            target_resource="completely-unobserved-random-resource",
            effective_level=0,
        )


@pytest.mark.unit
def test_godview_managed_resource_allowed() -> None:
    # Target resource marked godview_managed=true in docs
    ctx = MockContext(
        docs={
            "discovered_services": {
                "sovereign-svc": {
                    "doc_id": "sovereign-svc",
                    "labels": {"godview_managed": "true"},
                }
            }
        }
    )
    assert (
        validate_resource_scope(
            ctx=ctx,
            agent_id="application__svc-01",
            capability_id="run.set_ingress",
            target_resource="sovereign-svc",
            effective_level=1,
        )
        is True
    )


@pytest.mark.unit
def test_observed_resource_platform_action_pre_promoted_with_approval() -> None:
    # Target resource in discovered_services (observed, not godview_managed)
    ctx = MockContext(
        docs={
            "discovered_services": {
                "tenant-edge-policy": {
                    "doc_id": "tenant-edge-policy",
                    "name": "tenant-edge-policy",
                    "labels": {"godview_managed": "false"},
                }
            }
        }
    )

    # Platform action without approval should raise ScopeViolationError
    with pytest.raises(ScopeViolationError, match="requires human approval"):
        validate_resource_scope(
            ctx=ctx,
            agent_id="platform__env-01",
            capability_id="armor.set_waf_sensitivity",
            target_resource="tenant-edge-policy",
            effective_level=1,
            approval_id=None,
        )

    # With approval_id, platform action succeeds via L2 pre-promotion
    assert (
        validate_resource_scope(
            ctx=ctx,
            agent_id="platform__env-01",
            capability_id="armor.set_waf_sensitivity",
            target_resource="tenant-edge-policy",
            effective_level=1,
            approval_id="apr-20261017-0001",
        )
        is True
    )


@pytest.mark.unit
def test_observed_resource_insufficient_level_raises_scope_violation() -> None:
    ctx = MockContext(
        docs={
            "discovered_services": {
                "custom-target": {
                    "doc_id": "custom-target",
                    "labels": {"godview_managed": "false"},
                }
            }
        }
    )
    # k8s.rollout_restart has min_level=3
    with pytest.raises(ScopeViolationError, match="has effective level L1, but capability 'k8s.rollout_restart' requires L3"):
        validate_resource_scope(
            ctx=ctx,
            agent_id="application__svc-01",
            capability_id="k8s.rollout_restart",
            target_resource="custom-target",
            effective_level=1,
        )


@pytest.mark.unit
def test_scope_bypass_query_param_rejected() -> None:
    ctx = MockContext()
    with pytest.raises(ScopeViolationError, match="invalid query parameter scope bypass"):
        validate_resource_scope(
            ctx=ctx,
            agent_id="application__x",
            capability_id="run.set_ingress",
            target_resource="anything?godview_managed=true",
            effective_level=1,
        )
