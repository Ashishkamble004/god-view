"""Single authoritative capability registry and official MCP tool allowlist (TDD I-05, §3.7, Agent Spec §6).

Only official MCP tools listed in OFFICIAL_MCP_TOOLS may be bound as MCP tools.
Everything else God-View performs is an explicit capability implemented by a sense adapter.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

OFFICIAL_MCP_TOOLS: dict[str, frozenset[str]] = {
    "observability-mcp": frozenset(
        {
            "list_log_entries",
            "list_timeseries",
            "list_traces",
            "get_trace",
        }
    ),
    "gke-mcp": frozenset(
        {
            "list_clusters",
            "get_cluster",
            "get_k8s_resource",
            "query_logs",
            "get_log_schema",
            "list_recommendations",
        }
    ),
    "mcp-toolbox-postgres": frozenset(
        {
            "list_tables",
            "execute_sql",
            "list_active_queries",
            "get_query_plan",
        }
    ),
    "github-mcp": frozenset(
        {
            "list_commits",
            "get_commit",
            "get_file_contents",
            "create_branch",
            "push_files",
            "create_issue",
            "create_pull_request",
        }
    ),
}

FORBIDDEN_V3_MCP_TOOLS: frozenset[str] = frozenset(
    {
        "fetch_single_exemplar_trace",
        "get_critical_path_span",
        "query_promql_timeseries",
        "gke_get_pod_events",
        "gke_inspect_oom_and_restarts",
        "gke_rollout_restart_deployment",
        "inspect_pg_locks_blocking_tree",
        "terminate_blocking_pid",
        "tf_detect_live_drift_diff",
        "tf_reconcile_drifted_resource",
        "tf_map_service_to_module",
        "cdn_inspect_cache_hit_ratio",
        "github_create_rollback_pr",
    }
)


def validate_mcp_tool_binding(mcp_server: str, tool_name: str) -> None:
    """Validate that an MCP server and tool name are in the official allowlist (TDD I-05).

    Raises ValueError if terraform-mcp is claimed for drift/apply or if the server/tool
    is not in OFFICIAL_MCP_TOOLS.
    """
    if mcp_server == "terraform-mcp":
        raise ValueError(
            "HashiCorp 'terraform-mcp' is registry-docs/HCP-only and cannot be bound for drift detection or apply (TDD I-05)"
        )
    if mcp_server not in OFFICIAL_MCP_TOOLS:
        raise ValueError(
            f"MCP server '{mcp_server}' is not in OFFICIAL_MCP_TOOLS: {sorted(OFFICIAL_MCP_TOOLS)}"
        )
    allowed = OFFICIAL_MCP_TOOLS[mcp_server]
    if tool_name in FORBIDDEN_V3_MCP_TOOLS or tool_name not in allowed:
        raise ValueError(
            f"Tool '{tool_name}' on server '{mcp_server}' is not in the official allowlist: {sorted(allowed)}"
        )


class CapabilitySpec(BaseModel):
    """Canonical specification for a God-View capability."""

    model_config = ConfigDict(frozen=True)

    capability_id: str
    family: Literal["gcp", "kubernetes", "sql", "git", "channels", "local", "http"]
    kind: Literal["official_mcp", "composite_mcp", "godview_adapter"]
    mcp_server: str | None = None
    mcp_tools: tuple[str, ...] = ()
    mutating: bool = False
    min_level: int = 0
    sovereign_only: bool = False
    money_movement: bool = False
    customer_comms: bool = False
    description: str = ""


def _build_registry(custom_specs: list[CapabilitySpec] | None = None) -> dict[str, CapabilitySpec]:
    specs: list[CapabilitySpec] = (
        custom_specs
        if custom_specs is not None
        else [
            # Scoped observability (observability-mcp)
            CapabilitySpec(
                capability_id="obs.list_timeseries",
                family="gcp",
                kind="official_mcp",
                mcp_server="observability-mcp",
                mcp_tools=("list_timeseries",),
                mutating=False,
                min_level=0,
                description="Query Cloud Monitoring time-series for affected resource and window",
            ),
            CapabilitySpec(
                capability_id="obs.get_trace",
                family="gcp",
                kind="official_mcp",
                mcp_server="observability-mcp",
                mcp_tools=("get_trace",),
                mutating=False,
                min_level=0,
                description="Fetch single exemplar trace by ID",
            ),
            CapabilitySpec(
                capability_id="obs.list_traces",
                family="gcp",
                kind="official_mcp",
                mcp_server="observability-mcp",
                mcp_tools=("list_traces",),
                mutating=False,
                min_level=0,
                description="List bounded traces for an affected service",
            ),
            CapabilitySpec(
                capability_id="obs.query_logs_scoped",
                family="gcp",
                kind="official_mcp",
                mcp_server="observability-mcp",
                mcp_tools=("list_log_entries",),
                mutating=False,
                min_level=0,
                description="Scoped log query (pageSize <= 50) for affected resource",
            ),
            # Kubernetes / GKE inspection & actions
            CapabilitySpec(
                capability_id="k8s.list_clusters",
                family="gcp",
                kind="official_mcp",
                mcp_server="gke-mcp",
                mcp_tools=("list_clusters",),
                mutating=False,
                min_level=0,
            ),
            CapabilitySpec(
                capability_id="k8s.get_cluster",
                family="gcp",
                kind="official_mcp",
                mcp_server="gke-mcp",
                mcp_tools=("get_cluster",),
                mutating=False,
                min_level=0,
            ),
            CapabilitySpec(
                capability_id="k8s.get_resource",
                family="kubernetes",
                kind="official_mcp",
                mcp_server="gke-mcp",
                mcp_tools=("get_k8s_resource",),
                mutating=False,
                min_level=0,
                description="Inspect Kubernetes resource via gke-mcp on GKE or Kubernetes API on kind",
            ),
            CapabilitySpec(
                capability_id="k8s.query_logs",
                family="gcp",
                kind="official_mcp",
                mcp_server="gke-mcp",
                mcp_tools=("query_logs",),
                mutating=False,
                min_level=0,
            ),
            CapabilitySpec(
                capability_id="k8s.get_log_schema",
                family="gcp",
                kind="official_mcp",
                mcp_server="gke-mcp",
                mcp_tools=("get_log_schema",),
                mutating=False,
                min_level=0,
            ),
            CapabilitySpec(
                capability_id="k8s.list_recommendations",
                family="gcp",
                kind="official_mcp",
                mcp_server="gke-mcp",
                mcp_tools=("list_recommendations",),
                mutating=False,
                min_level=0,
            ),
            CapabilitySpec(
                capability_id="k8s.rollout_restart",
                family="kubernetes",
                kind="godview_adapter",
                mutating=True,
                min_level=3,
                description="Restart a Kubernetes Deployment via the Kubernetes API",
            ),
            CapabilitySpec(
                capability_id="k8s.scale",
                family="kubernetes",
                kind="godview_adapter",
                mutating=True,
                min_level=3,
                description="Scale a Kubernetes workload replica count via the Kubernetes API",
            ),
            # SQL / MCP Toolbox for Databases (postgres)
            CapabilitySpec(
                capability_id="sql.list_tables",
                family="sql",
                kind="official_mcp",
                mcp_server="mcp-toolbox-postgres",
                mcp_tools=("list_tables",),
                mutating=False,
                min_level=0,
            ),
            CapabilitySpec(
                capability_id="sql.list_active_queries",
                family="sql",
                kind="official_mcp",
                mcp_server="mcp-toolbox-postgres",
                mcp_tools=("list_active_queries",),
                mutating=False,
                min_level=0,
            ),
            CapabilitySpec(
                capability_id="sql.get_query_plan",
                family="sql",
                kind="official_mcp",
                mcp_server="mcp-toolbox-postgres",
                mcp_tools=("get_query_plan",),
                mutating=False,
                min_level=0,
            ),
            CapabilitySpec(
                capability_id="sql.blocking_tree",
                family="sql",
                kind="composite_mcp",
                mcp_server="mcp-toolbox-postgres",
                mcp_tools=("execute_sql",),
                mutating=False,
                min_level=0,
                description="Inspect pg_locks joined to pg_stat_activity with pg_blocking_pids()",
            ),
            CapabilitySpec(
                capability_id="sql.integrity_check",
                family="sql",
                kind="official_mcp",
                mcp_server="mcp-toolbox-postgres",
                mcp_tools=("execute_sql",),
                mutating=False,
                min_level=0,
                description="Execute read-only SELECT integrity query with statement_timeout",
            ),
            CapabilitySpec(
                capability_id="sql.terminate_backend",
                family="sql",
                kind="official_mcp",
                mcp_server="mcp-toolbox-postgres",
                mcp_tools=("execute_sql",),
                mutating=True,
                min_level=3,
                description="Terminate a single blocking backend PID via pg_terminate_backend(pid)",
            ),
            # Git / GitHub MCP & REST adapter
            CapabilitySpec(
                capability_id="git.list_commits",
                family="git",
                kind="official_mcp",
                mcp_server="github-mcp",
                mcp_tools=("list_commits",),
                mutating=False,
                min_level=0,
            ),
            CapabilitySpec(
                capability_id="git.get_commit",
                family="git",
                kind="official_mcp",
                mcp_server="github-mcp",
                mcp_tools=("get_commit",),
                mutating=False,
                min_level=0,
            ),
            CapabilitySpec(
                capability_id="git.get_file_contents",
                family="git",
                kind="official_mcp",
                mcp_server="github-mcp",
                mcp_tools=("get_file_contents",),
                mutating=False,
                min_level=0,
            ),
            CapabilitySpec(
                capability_id="git.create_issue",
                family="git",
                kind="official_mcp",
                mcp_server="github-mcp",
                mcp_tools=("create_issue",),
                mutating=True,
                min_level=1,
            ),
            CapabilitySpec(
                capability_id="git.propose_files",
                family="git",
                kind="composite_mcp",
                mcp_server="github-mcp",
                mcp_tools=("create_branch", "push_files", "create_pull_request"),
                mutating=True,
                min_level=1,
                description="Propose INVARIANT.md or SKILL.md files via a branch and pull request",
            ),
            CapabilitySpec(
                capability_id="git.create_rollback_pr",
                family="git",
                kind="composite_mcp",
                mcp_server="github-mcp",
                mcp_tools=(
                    "get_commit",
                    "get_file_contents",
                    "create_branch",
                    "push_files",
                    "create_pull_request",
                    "create_issue",
                ),
                mutating=True,
                min_level=3,
                description="5-call rollback PR recipe (+ postmortem issue) via github-mcp",
            ),
            CapabilitySpec(
                capability_id="git.pr_comment",
                family="git",
                kind="godview_adapter",
                mutating=True,
                min_level=1,
                description="Post business-language PR risk review comment via GitHub REST API",
            ),
            CapabilitySpec(
                capability_id="git.merge_pr",
                family="git",
                kind="godview_adapter",
                mutating=True,
                min_level=3,
                description="Merge an approved rollback PR via GitHub REST API",
            ),
            # Platform / SDK capabilities
            CapabilitySpec(
                capability_id="metrics.scrape",
                family="kubernetes",
                kind="godview_adapter",
                mutating=False,
                min_level=0,
            ),
            CapabilitySpec(
                capability_id="asset.list",
                family="gcp",
                kind="godview_adapter",
                mutating=False,
                min_level=0,
            ),
            CapabilitySpec(
                capability_id="tfstate.read",
                family="gcp",
                kind="godview_adapter",
                mutating=False,
                min_level=0,
            ),
            CapabilitySpec(
                capability_id="cost.query",
                family="gcp",
                kind="godview_adapter",
                mutating=False,
                min_level=0,
            ),
            CapabilitySpec(
                capability_id="ledger.query",
                family="gcp",
                kind="godview_adapter",
                mutating=False,
                min_level=0,
            ),
            CapabilitySpec(
                capability_id="tf.drift_diff",
                family="gcp",
                kind="godview_adapter",
                mutating=False,
                min_level=0,
                description="Custom tfstate-vs-live curated attribute diff engine",
            ),
            CapabilitySpec(
                capability_id="tf.restore_attribute",
                family="gcp",
                kind="godview_adapter",
                mutating=True,
                min_level=3,
                description="Restore a single drifted attribute to its tfstate value via live API",
            ),
            CapabilitySpec(
                capability_id="tf.apply_dispatch",
                family="git",
                kind="godview_adapter",
                mutating=True,
                min_level=2,
                description="Dispatch GitHub Actions workflow_dispatch for terraform apply",
            ),
            CapabilitySpec(
                capability_id="probe.execute",
                family="http",
                kind="godview_adapter",
                mutating=False,
                min_level=0,
                description="Execute synthetic probe against x-godview-probe-safe endpoint",
            ),
            # v5 Observability & Sense Capabilities (Plan §1.5)
            CapabilitySpec(
                capability_id="logging.query_lb_requests",
                family="gcp",
                kind="godview_adapter",
                mutating=False,
                min_level=0,
                description="Query Cloud Logging for load balancer HTTP request entries",
            ),
            CapabilitySpec(
                capability_id="logging.query",
                family="gcp",
                kind="godview_adapter",
                mutating=False,
                min_level=0,
                description="Query Cloud Logging entries with filter and window",
            ),
            CapabilitySpec(
                capability_id="monitoring.query_timeseries",
                family="gcp",
                kind="godview_adapter",
                mutating=False,
                min_level=0,
                description="Query Cloud Monitoring time-series metrics",
            ),
            CapabilitySpec(
                capability_id="edge.describe_backends",
                family="gcp",
                kind="godview_adapter",
                mutating=False,
                min_level=0,
                description="Describe Compute Engine backend services and security policies",
            ),
            CapabilitySpec(
                capability_id="edge.describe_security_policies",
                family="gcp",
                kind="godview_adapter",
                mutating=False,
                min_level=0,
                description="Describe Cloud Armor security policies and rules",
            ),
            CapabilitySpec(
                capability_id="apigateway.get_openapi",
                family="gcp",
                kind="godview_adapter",
                mutating=False,
                min_level=0,
                description="Fetch and parse API Gateway OpenAPI configuration specs",
            ),
            CapabilitySpec(
                capability_id="run.get_posture",
                family="gcp",
                kind="godview_adapter",
                mutating=False,
                min_level=0,
                description="Get Cloud Run service security and ingress posture",
            ),
            CapabilitySpec(
                capability_id="pubsub.describe_subscriptions",
                family="gcp",
                kind="godview_adapter",
                mutating=False,
                min_level=0,
                description="Describe Pub/Sub subscriptions and dead letter policies",
            ),
            CapabilitySpec(
                capability_id="pubsub.dlq_depth",
                family="gcp",
                kind="godview_adapter",
                mutating=False,
                min_level=0,
                description="Get dead letter queue backlog depth and unacknowledged messages",
            ),
            CapabilitySpec(
                capability_id="http.probe",
                family="http",
                kind="godview_adapter",
                mutating=False,
                min_level=0,
                description="Execute GET-only synthetic HTTP probe against discovered safe route",
            ),
            CapabilitySpec(
                capability_id="gemini.infer_business_context",
                family="gcp",
                kind="godview_adapter",
                mutating=False,
                min_level=0,
                description="Infer business context and role from environment evidence",
            ),
            # v5 Mutating Capabilities (Plan §1.5)
            CapabilitySpec(
                capability_id="armor.set_waf_sensitivity",
                family="gcp",
                kind="godview_adapter",
                mutating=True,
                min_level=2,
                description="Update Cloud Armor preconfigured WAF rule sensitivity (preview first, verify, then enforce)",
            ),
            CapabilitySpec(
                capability_id="armor.set_rule_preview",
                family="gcp",
                kind="godview_adapter",
                mutating=True,
                min_level=2,
                description="Toggle preview mode on a Cloud Armor security policy rule (kill switch)",
            ),
            CapabilitySpec(
                capability_id="run.set_ingress",
                family="gcp",
                kind="godview_adapter",
                mutating=True,
                min_level=2,
                description="Update Cloud Run ingress traffic settings",
            ),
            CapabilitySpec(
                capability_id="run.set_invoker_iam",
                family="gcp",
                kind="godview_adapter",
                mutating=True,
                min_level=2,
                description="Update Cloud Run invoker IAM policy binding",
            ),
            CapabilitySpec(
                capability_id="pubsub.replay_dlq",
                family="gcp",
                kind="godview_adapter",
                mutating=True,
                min_level=2,
                description="Replay messages from dead letter subscription back to target topic",
            ),
            CapabilitySpec(
                capability_id="gke.patch_probe",
                family="kubernetes",
                kind="godview_adapter",
                mutating=True,
                min_level=2,
                description="Patch container probes (e.g. startupProbe) on a Kubernetes deployment",
            ),
            CapabilitySpec(
                capability_id="tenant.admin_action",
                family="http",
                kind="godview_adapter",
                mutating=True,
                min_level=2,
                description="Invoke an OpenAPI operation tagged with x-godview-admin-action using minted act SA OIDC token",
            ),
            CapabilitySpec(
                capability_id="run.restore_revision_config",
                family="gcp",
                kind="godview_adapter",
                mutating=True,
                min_level=3,
                description="Restore Cloud Run v2 service revision configuration",
            ),
            CapabilitySpec(
                capability_id="agent.set_min_instances",
                family="gcp",
                kind="godview_adapter",
                mutating=True,
                min_level=0,
                sovereign_only=True,
                description="Sovereign-only PATCH of Agent Engine deploymentSpec.minInstances (0 <-> 1)",
            ),
            # Business workflow remediation capabilities (discovered from x-godview-admin-action)
            CapabilitySpec(
                capability_id="biz.replay_disbursement",
                family="http",
                kind="godview_adapter",
                mutating=True,
                min_level=4,
                money_movement=True,
                description="Replay stuck disbursement via discovered tenant admin API",
            ),
            CapabilitySpec(
                capability_id="biz.reevaluate_application",
                family="http",
                kind="godview_adapter",
                mutating=True,
                min_level=4,
                description="Re-evaluate application under current rules via discovered tenant admin API",
            ),
            CapabilitySpec(
                capability_id="biz.compensate_journey",
                family="http",
                kind="godview_adapter",
                mutating=True,
                min_level=4,
                money_movement=True,
                description="Compensate / reverse journey state via discovered tenant admin API",
            ),
            # Human communication channels
            CapabilitySpec(
                capability_id="comms.chat_card",
                family="channels",
                kind="godview_adapter",
                mutating=True,
                min_level=1,
                description="Post alert or approval card to Chat space inside 08:00-22:00 IST window",
            ),
            CapabilitySpec(
                capability_id="comms.customer_notify",
                family="channels",
                kind="godview_adapter",
                mutating=True,
                min_level=2,
                customer_comms=True,
                description="Customer notification capped at L2 (never whitelisted in v1)",
            ),
            # Sovereign operator capabilities
            CapabilitySpec(
                capability_id="tenant.set_paused",
                family="http",
                kind="godview_adapter",
                mutating=True,
                min_level=0,
                sovereign_only=True,
            ),
            CapabilitySpec(
                capability_id="chaos.activate",
                family="http",
                kind="godview_adapter",
                mutating=True,
                min_level=0,
                sovereign_only=True,
            ),
            CapabilitySpec(
                capability_id="chaos.clear",
                family="http",
                kind="godview_adapter",
                mutating=True,
                min_level=0,
                sovereign_only=True,
            ),
        ]
    )
    registry: dict[str, CapabilitySpec] = {}
    for spec in specs:
        if spec.capability_id in registry:
            raise ValueError(f"Duplicate capability_id in registry: '{spec.capability_id}'")
        if spec.mcp_server is not None:
            for tool in spec.mcp_tools:
                validate_mcp_tool_binding(spec.mcp_server, tool)
        registry[spec.capability_id] = spec
    return registry


CAPABILITY_REGISTRY: dict[str, CapabilitySpec] = _build_registry()


def get_capability(capability_id: str) -> CapabilitySpec:
    """Return the CapabilitySpec for capability_id or raise KeyError."""
    if capability_id not in CAPABILITY_REGISTRY:
        raise KeyError(f"Unknown capability_id: '{capability_id}'")
    return CAPABILITY_REGISTRY[capability_id]
