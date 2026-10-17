"""Agent Engine runtime instance scaling remediation executor (Plan §3.4).

Executes:
- set_min_instances: scales Agent Engine deployment min_instances via ctx.agents.
"""

from __future__ import annotations

import logging
from typing import Any

from godview.sovereign.remediation.types import (
    BaseExecutor,
    ExecutionResult,
    VerificationCheck,
)

logger = logging.getLogger(__name__)


class AgentScalingExecutor(BaseExecutor):
    """Executor for Vertex AI Agent Engine min_instances scaling."""

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
        """Set min_instances on an Agent Engine deployment."""
        target_agent = args.get("agent_id") or target_resource.split("/")[-1]
        target_min = int(args.get("min_instances", 0))

        if not ctx or not hasattr(ctx, "agents") or ctx.agents is None:
            return ExecutionResult(
                ok=False,
                before=None,
                after=None,
                verification=[
                    VerificationCheck(
                        check="agent_runtime_available",
                        passed=False,
                        evidence={"error": "ctx.agents runtime is not configured"},
                    )
                ],
                reverted=False,
                error="ctx.agents is unavailable",
            )

        before: dict[str, Any] = {}
        checks: list[VerificationCheck] = []

        try:
            # 1. Fetch current agent config
            orig_agent = None
            if hasattr(ctx.agents, "get_agent"):
                orig_agent = ctx.agents.get_agent(target_agent)
                if hasattr(orig_agent, "__await__"):
                    orig_agent = await orig_agent

            orig_min = 0
            if isinstance(orig_agent, dict):
                spec = orig_agent.get("deployment_spec", {})
                orig_min = spec.get("min_instances", 0)
            elif orig_agent and hasattr(orig_agent, "min_instances"):
                orig_min = getattr(orig_agent, "min_instances", 0)

            before = {"min_instances": orig_min}

            # 2. Update min_instances
            update_fn = getattr(ctx.agents, "set_min_instances", None)
            if not callable(update_fn):
                raise RuntimeError("ctx.agents does not implement set_min_instances")

            res = update_fn(target_agent, target_min)
            if hasattr(res, "__await__"):
                await res

            # 3. Verify
            live_agent = None
            if hasattr(ctx.agents, "get_agent"):
                live_agent = ctx.agents.get_agent(target_agent)
                if hasattr(live_agent, "__await__"):
                    live_agent = await live_agent

            live_min = target_min
            if isinstance(live_agent, dict):
                spec = live_agent.get("deployment_spec", {})
                live_min = spec.get("min_instances", target_min)
            elif live_agent and hasattr(live_agent, "min_instances"):
                live_min = getattr(live_agent, "min_instances", target_min)

            verified = live_min == target_min
            checks.append(
                VerificationCheck(
                    check="agent_engine_min_instances_scaled",
                    passed=verified,
                    evidence={"expected_min": target_min, "live_min": live_min},
                )
            )

            after = {"min_instances": live_min}
            if not verified:
                # Rollback
                rb_res = update_fn(target_agent, orig_min)
                if hasattr(rb_res, "__await__"):
                    await rb_res
                return ExecutionResult(
                    ok=False,
                    before=before,
                    after=after,
                    verification=checks,
                    reverted=True,
                    error="Verification failed: agent min_instances was not updated",
                )

            return ExecutionResult(ok=True, before=before, after=after, verification=checks)
        except Exception as e:
            logger.exception("Error executing set_min_instances on %s", target_agent)
            return ExecutionResult(
                ok=False,
                before=before or None,
                after=None,
                verification=checks,
                reverted=False,
                error=str(e),
            )


__all__ = ["AgentScalingExecutor"]
