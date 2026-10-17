"""Remediation executor registry and dispatcher (Plan §3.4).

Dispatches capability invocations to registered executors, enforces the scope rule,
manages verification outcomes, and feeds results back into TrustGate.
"""

from __future__ import annotations

import logging
from typing import Any

from godview_shared.capabilities import get_capability
from godview_shared.contracts import TrustViolation

from godview.sovereign.remediation.executors.agent import AgentScalingExecutor
from godview.sovereign.remediation.executors.armor import CloudArmorExecutor
from godview.sovereign.remediation.executors.gke import GkeProbeExecutor
from godview.sovereign.remediation.executors.pubsub import PubSubDlqExecutor
from godview.sovereign.remediation.executors.run_posture import (
    CloudRunPostureExecutor,
)
from godview.sovereign.remediation.executors.tenant_admin import (
    TenantAdminExecutor,
)
from godview.sovereign.remediation.scope import validate_resource_scope
from godview.sovereign.remediation.types import (
    BaseExecutor,
    ExecutionResult,
    NoExecutorError,
)

logger = logging.getLogger(__name__)


class RemediationDispatcher:
    """Registry and invocation router for remediation executors."""

    def __init__(self, custom_executors: dict[str, BaseExecutor] | None = None) -> None:
        self._armor_executor = CloudArmorExecutor()
        self._run_executor = CloudRunPostureExecutor()
        self._pubsub_executor = PubSubDlqExecutor()
        self._gke_executor = GkeProbeExecutor()
        self._tenant_admin_executor = TenantAdminExecutor()
        self._agent_executor = AgentScalingExecutor()

        self._executors: dict[str, BaseExecutor] = {
            "armor.set_waf_sensitivity": self._armor_executor,
            "armor.set_rule_preview": self._armor_executor,
            "run.set_ingress": self._run_executor,
            "run.set_invoker_iam": self._run_executor,
            "run.restore_revision_config": self._run_executor,
            "pubsub.replay_dlq": self._pubsub_executor,
            "gke.patch_probe": self._gke_executor,
            "tenant.admin_action": self._tenant_admin_executor,
            "agent.set_min_instances": self._agent_executor,
        }
        if custom_executors:
            self._executors.update(custom_executors)

    def register_executor(self, capability_id: str, executor: BaseExecutor) -> None:
        """Register or override an executor for capability_id."""
        self._executors[capability_id] = executor

    def get_executor(self, capability_id: str) -> BaseExecutor:
        """Return executor for capability_id or raise NoExecutorError."""
        if capability_id not in self._executors:
            raise NoExecutorError(capability_id)
        return self._executors[capability_id]

    async def invoke(
        self,
        *,
        ctx: Any,
        trust_gate: Any,
        capability_id: str,
        target_resource: str,
        args: dict[str, Any],
        agent_id: str,
        approval_id: str | None = None,
        novel_skill_first_step: bool = False,
        probe_url: str | None = None,
    ) -> ExecutionResult:
        """Authorize, validate scope, dispatch to executor, verify outcome, and feed trust ladder."""
        # 1. Look up executor first; if unhandled, raise NoExecutorError (never echo)
        executor = self.get_executor(capability_id)
        spec = get_capability(capability_id)

        # 2. Check TrustGate authorization
        decision = trust_gate.authorize(
            agent_id=agent_id,
            capability_id=capability_id,
            target_resource=target_resource,
            blast_radius_resources=1,
            approval_id=approval_id,
            novel_skill_first_step=novel_skill_first_step,
        )
        if not decision.authorized:
            raise TrustViolation(
                f"TrustGate refused authorization: {decision.reason} "
                f"(effective=L{decision.effective_level}, required=L{decision.required_level})"
            )

        # 3. If approval_id was used, enforce argument binding against stored approval card
        effective_args = dict(args)
        if approval_id is not None and hasattr(ctx, "docs") and ctx.docs is not None:
            card_raw = ctx.docs.get("approvals", approval_id)
            if card_raw:
                card_args = card_raw.get("args")
                if card_args is not None:
                    # If caller supplied distinct arguments, verify they match approved card
                    if args and args != card_args:
                        raise TrustViolation(
                            f"Approval '{approval_id}' argument mismatch: "
                            f"invocation args {args} do not match approved card args {card_args}"
                        )
                    # Always use the approved card's stored arguments
                    effective_args = dict(card_args)

        # 4. Validate Scope Rule (Plan §3.4 item 5)
        validate_resource_scope(
            ctx=ctx,
            agent_id=agent_id,
            capability_id=capability_id,
            target_resource=target_resource,
            effective_level=decision.effective_level,
            approval_id=approval_id,
        )

        # 5. Dispatch to real executor with learning feedback
        try:
            result = await executor.execute(
                ctx=ctx,
                args={**effective_args, "capability_id": capability_id},
                target_resource=target_resource,
                agent_id=agent_id,
                approval_id=approval_id,
                probe_url=probe_url,
            )
        except Exception as exc:
            if spec.mutating:
                try:
                    trust_gate.record_bad_outcome(agent_id, capability_id, reason=str(exc))
                except Exception as ex:
                    logger.warning("Failed to record bad outcome on exception: %s", ex)
            raise

        # 6. Outcome verification & Trust Ladder feedback
        if result.ok:
            if spec.mutating and approval_id is not None:
                try:
                    trust_gate.record_approved_execution_verified(agent_id, capability_id)
                except Exception as ex:
                    logger.warning("Failed to record approved execution: %s", ex)
        else:
            if spec.mutating:
                try:
                    fail_reason = result.error or "Verification checks failed"
                    trust_gate.record_bad_outcome(agent_id, capability_id, reason=fail_reason)
                except Exception as ex:
                    logger.warning("Failed to record bad outcome: %s", ex)

        return result


__all__ = ["RemediationDispatcher"]
