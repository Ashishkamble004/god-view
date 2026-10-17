"""MCP Subprocess Host and Capability Dispatcher (`godview.sovereign.control_api.mcp`)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from godview_shared.adapter_contract import SenseAdapter
from godview_shared.capabilities import (
    get_capability,
    validate_mcp_tool_binding,
)
from godview_shared.contracts import (
    TrustDecision,
    TrustViolation,
)

MCP_SERVER_COMMANDS: dict[str, list[str]] = {
    "observability-mcp": ["npx", "-y", "@google-cloud/observability-mcp"],
    "gke-mcp": ["gke-mcp", "--stdio"],
    "mcp-toolbox-postgres": [
        "genai-toolbox",
        "--prebuilt",
        "cloud-sql-postgres",
        "--stdio",
    ],
    "github-mcp": ["github-mcp-server", "stdio"],
}

_CHAOS_DRILL_METADATA: dict[str, dict[str, str]] = {
    "gke_oom_503": {
        "fault_class": "infrastructure",
        "fault_type": "pod_oom_503",
        "title": "Pod OOM & HTTP 503 Saturation",
        "target_service": "svc-alpha-01",
        "infra_golden_signals": "red",
        "expected_detection": "golden_signal_gate + probe_plan",
        "mttd_target": "30s",
    },
    "pg_lock_contention": {
        "fault_class": "infrastructure",
        "fault_type": "row_lock_stall",
        "title": "Database Row-Lock Contention",
        "target_service": "db-primary-01",
        "infra_golden_signals": "amber",
        "expected_detection": "journey_conformance (STALLED)",
        "mttd_target": "60s",
    },
    "bad_rollout_pr": {
        "fault_class": "change",
        "fault_type": "calculation_regression",
        "title": "Formula Regression Rollout",
        "target_service": "svc-alpha-01",
        "infra_golden_signals": "green",
        "expected_detection": "invariant_sql",
        "mttd_target": "30s",
    },
    "silent_drop_200": {
        "fault_class": "semantic",
        "fault_type": "silent_rejection_200",
        "title": "Silent HTTP 200 Approval Drop",
        "target_service": "svc-alpha-01",
        "infra_golden_signals": "green",
        "expected_detection": "kpi_changepoint + invariant_sql",
        "mttd_target": "60s",
    },
    "armor_bypass": {
        "fault_class": "security",
        "fault_type": "guardrail_bypass",
        "title": "Guardrail Bypass Injection",
        "target_service": "svc-alpha-01",
        "infra_golden_signals": "green",
        "expected_detection": "invariant_sql",
        "mttd_target": "30s",
    },
    "cbs_timeout": {
        "fault_class": "infrastructure",
        "fault_type": "upstream_timeout",
        "title": "Upstream Dependency Timeout",
        "target_service": "vm-core-01",
        "infra_golden_signals": "amber",
        "expected_detection": "journey_conformance + golden_signal_gate",
        "mttd_target": "45s",
    },
    "migration_skipped": {
        "fault_class": "semantic",
        "fault_type": "step_bypass",
        "title": "Mandatory Step Skipped in Flow",
        "target_service": "svc-alpha-01",
        "infra_golden_signals": "green",
        "expected_detection": "journey_conformance (SKIPPED) + invariant_sql",
        "mttd_target": "30s",
    },
}


class NoExecutorError(HTTPException):
    """Raised when no executor or adapter is registered to handle a capability."""

    def __init__(self, capability_id: str) -> None:
        super().__init__(
            status_code=501,
            detail=f"No executor registered for capability '{capability_id}'",
        )


class McpSubprocessManager:
    """Hosts the official MCP stdio servers and SenseAdapters inside Sovereign."""

    def __init__(
        self,
        *,
        env_mode: str = "hermetic",
        adapters: Sequence[SenseAdapter] | None = None,
        executors: dict[str, Any] | None = None,
    ) -> None:
        self.server_commands = dict(MCP_SERVER_COMMANDS)
        self.env_mode = env_mode
        self._adapters: list[SenseAdapter] = list(adapters) if adapters is not None else []
        self._executors: dict[str, Any] = executors if executors is not None else {}
        self.invocation_log: list[dict[str, Any]] = []

    def register_adapter(self, adapter: SenseAdapter) -> None:
        """Register a SenseAdapter to handle matching capabilities."""
        if adapter not in self._adapters:
            self._adapters.append(adapter)

    def register_adapters(self, adapters: Sequence[SenseAdapter]) -> None:
        """Register multiple SenseAdapters."""
        for adapter in adapters:
            self.register_adapter(adapter)

    def register_executor(self, capability_id: str, executor: Any) -> None:
        """Register a remediation/runtime executor callable for a capability."""
        self._executors[capability_id] = executor

    async def invoke_capability_async(
        self,
        capability_id: str,
        args: dict[str, Any],
        decision: TrustDecision,
    ) -> dict[str, Any]:
        """Dispatch a TrustGate-authorized capability call to a registered executor, SenseAdapter, or MCP."""
        spec = get_capability(capability_id)
        if spec.mutating and not decision.authorized:
            raise TrustViolation(
                f"McpSubprocessManager refused unauthorized mutating capability '{capability_id}'"
            )

        mcp_server: str | None = spec.mcp_server
        mcp_tools: list[str] = list(spec.mcp_tools)
        if mcp_server is not None:
            for tool_name in mcp_tools:
                validate_mcp_tool_binding(mcp_server, tool_name)

        # 1. Check registered executors
        if capability_id in self._executors:
            executor = self._executors[capability_id]
            import inspect

            res = executor(**args)
            if inspect.isawaitable(res):
                res = await res
            record = {
                "capability_id": capability_id,
                "args": args,
                "invoked_at": datetime.now(tz=UTC).isoformat(),
                "executor_result": res,
            }
            self.invocation_log.append(record)
            return {
                "ok": True,
                "capability_id": capability_id,
                "executor_result": res,
            }

        # 2. Check registered SenseAdapters
        for adapter in self._adapters:
            caps = (
                adapter.capabilities()
                if callable(getattr(adapter, "capabilities", None))
                else getattr(adapter, "capabilities", ())
            )
            if capability_id in caps:
                adapter_res = await adapter.execute_capability(
                    capability_id,
                    args,
                    trust_decision=decision,
                )
                record = {
                    "capability_id": capability_id,
                    "adapter_family": adapter.family,
                    "args": args,
                    "invoked_at": datetime.now(tz=UTC).isoformat(),
                    "adapter_result": adapter_res,
                }
                self.invocation_log.append(record)
                return {
                    "ok": True,
                    "capability_id": capability_id,
                    "adapter_family": adapter.family,
                    "adapter_result": adapter_res,
                }

        # In hermetic mode only, if it is a read-only capability (e.g. obs.get_trace), allow mock execution
        if self.env_mode == "hermetic" and not spec.mutating:
            record = {
                "capability_id": capability_id,
                "mcp_server": mcp_server,
                "mcp_tools": mcp_tools,
                "sa_impersonated": decision.sa_to_impersonate,
                "args": args,
                "invoked_at": datetime.now(tz=UTC).isoformat(),
            }
            self.invocation_log.append(record)
            return {
                "ok": True,
                "capability_id": capability_id,
                "hermetic_mock": True,
            }

        # Otherwise raise NoExecutorError (HTTP 501) - never echo!
        raise NoExecutorError(capability_id)
