"""Official MCP stdio clients for the 4 MCP servers packaged inside the Sovereign image.

Per Coordinator Decision A3 & TDD §3.6–§3.7 (I-05):
1. `observability-mcp`: `list_log_entries`, `list_timeseries`, `list_traces`, `get_trace`
2. `gke-mcp`: `list_clusters`, `get_cluster`, `get_k8s_resource`, `query_logs`, `get_log_schema`, `list_recommendations`
3. `mcp-toolbox-postgres`: `list_tables`, `execute_sql`, `list_active_queries`, `get_query_plan`
4. `github-mcp`: `list_commits`, `get_commit`, `get_file_contents`, `create_branch`, `push_files`, `create_issue`, `create_pull_request`

Each `StdioMcpClient` maintains a persistent subprocess session per server, executes the
MCP JSON-RPC 2.0 `initialize` + `notifications/initialized` handshake before invoking
`tools/call`, resolves commands from environment/config (`DEFAULT_MCP_SERVER_COMMANDS`),
and raises `McpToolError` on JSON-RPC `"error"` or MCP `result.isError == True`.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import shlex
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from godview_shared.capabilities import (
    OFFICIAL_MCP_TOOLS,
    validate_mcp_tool_binding,
)

McpTransportCallable = Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]

DEFAULT_MCP_SERVER_COMMANDS: dict[str, tuple[str, ...]] = {
    "observability-mcp": ("npx", "-y", "@google-cloud/observability-mcp"),
    "gke-mcp": ("gke-mcp", "--stdio"),
    "mcp-toolbox-postgres": ("toolbox", "--prebuilt", "postgres", "--stdio"),
    "github-mcp": ("github-mcp-server", "stdio"),
}

_MCP_COMMAND_ENV_VARS: dict[str, str] = {
    "observability-mcp": "GODVIEW_MCP_OBSERVABILITY_CMD",
    "gke-mcp": "GODVIEW_MCP_GKE_CMD",
    "mcp-toolbox-postgres": "GODVIEW_MCP_POSTGRES_CMD",
    "github-mcp": "GODVIEW_MCP_GITHUB_CMD",
}


class McpToolError(RuntimeError):
    """Raised when an MCP server returns a JSON-RPC error or `result.isError == True`."""


def resolve_mcp_server_command(
    server_name: str,
    explicit_command: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    """Resolve the stdio command for an official MCP server from explicit args or environment."""
    if explicit_command:
        cmd = [str(part) for part in explicit_command if str(part).strip()]
        if not cmd:
            raise ValueError(f"Empty explicit command for MCP server '{server_name}'")
        return cmd
    env_var = _MCP_COMMAND_ENV_VARS.get(server_name, "")
    if env_var:
        raw_env = os.environ.get(env_var, "").strip()
        if raw_env:
            parsed = shlex.split(raw_env)
            if parsed:
                return parsed
    default_cmd = DEFAULT_MCP_SERVER_COMMANDS.get(server_name)
    if not default_cmd:
        raise ValueError(f"No default command configured for MCP server '{server_name}'")
    return list(default_cmd)


@runtime_checkable
class McpToolClientProtocol(Protocol):
    """Protocol shared across `godview/adapters/gcp/` and `godview/adapters/{kubernetes,sql,git}/`."""

    server_name: str

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Validate `tool_name` against `OFFICIAL_MCP_TOOLS[server_name]` and invoke over stdio."""


class StdioMcpClient:
    """Persistent JSON-RPC 2.0 stdio client for an official MCP server subprocess.

    Validates `server_name` on construction and every `tool_name` on `call_tool()`
    against `godview_shared.capabilities.OFFICIAL_MCP_TOOLS` (TDD I-05).
    Performs the MCP `initialize` / `notifications/initialized` handshake once per
    persistent subprocess session and raises `McpToolError` if `result.isError` is True.
    """

    def __init__(
        self,
        server_name: str,
        *,
        command: list[str] | None = None,
        env: dict[str, str] | None = None,
        transport: McpTransportCallable | None = None,
        protocol_version: str = "2024-11-05",
    ) -> None:
        if server_name == "terraform-mcp":
            raise ValueError(
                "HashiCorp 'terraform-mcp' is registry-docs/HCP-only and cannot be bound for drift detection or apply (TDD I-05)"
            )
        if server_name not in OFFICIAL_MCP_TOOLS:
            raise ValueError(
                f"Unknown or unapproved MCP server '{server_name}'. Allowed: {sorted(OFFICIAL_MCP_TOOLS)}"
            )
        self.server_name = server_name
        self.allowed_tools: frozenset[str] = OFFICIAL_MCP_TOOLS[server_name]
        self.command = resolve_mcp_server_command(server_name, command)
        self.env = env
        self._transport = transport
        self.protocol_version = protocol_version
        self._next_id = 1
        self._process: asyncio.subprocess.Process | None = None
        self._initialized: bool = False
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> StdioMcpClient:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    @property
    def is_initialized(self) -> bool:
        """Return True if the MCP session has completed the `initialize` handshake."""
        return self._initialized

    def _check_result(self, tool_name: str, response: Any) -> Any:
        if not isinstance(response, dict):
            return response
        if "error" in response and response["error"]:
            raise McpToolError(
                f"MCP JSON-RPC error from {self.server_name}.{tool_name}: {response['error']}"
            )
        result = response.get("result", response)
        if isinstance(result, dict) and result.get("isError") is True:
            raise McpToolError(
                f"MCP tool '{self.server_name}.{tool_name}' reported isError=true: "
                f"{result.get('content', result)}"
            )
        return result

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Validate `tool_name` against the official allowlist and execute `tools/call`."""
        validate_mcp_tool_binding(self.server_name, tool_name)
        request_id = self._next_id
        self._next_id += 1
        request: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": dict(arguments),
            },
        }
        if self._transport is not None:
            raw_res = self._transport(request)
            response = await raw_res if inspect.isawaitable(raw_res) else raw_res
            return self._check_result(tool_name, response)

        response = await self._send_over_persistent_stdio(request)
        return self._check_result(tool_name, response)

    async def _ensure_process_and_handshake(self) -> asyncio.subprocess.Process:
        """Ensure the persistent MCP server subprocess is running and initialized."""
        if self._process is None or self._process.returncode is not None:
            proc_env = {**os.environ, **(self.env or {})}
            self._process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=proc_env,
            )
            self._initialized = False

        if not self._initialized:
            init_id = self._next_id
            self._next_id += 1
            init_req = {
                "jsonrpc": "2.0",
                "id": init_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {},
                    "clientInfo": {"name": "godview-sovereign", "version": "4.0.0"},
                },
            }
            init_resp = await self._write_and_read_jsonrpc(self._process, init_req)
            if "error" in init_resp and init_resp["error"]:
                raise McpToolError(
                    f"MCP initialize handshake failed on '{self.server_name}': {init_resp['error']}"
                )
            initialized_notif = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
            assert self._process.stdin is not None
            self._process.stdin.write((json.dumps(initialized_notif) + "\n").encode("utf-8"))
            await self._process.stdin.drain()
            self._initialized = True

        return self._process

    async def _write_and_read_jsonrpc(
        self,
        proc: asyncio.subprocess.Process,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        assert proc.stdin is not None and proc.stdout is not None
        payload = (json.dumps(request) + "\n").encode("utf-8")
        proc.stdin.write(payload)
        await proc.stdin.drain()

        expected_id = request.get("id")
        while True:
            raw_line = await proc.stdout.readline()
            if not raw_line:
                stderr_text = ""
                if proc.stderr is not None:
                    stderr_bytes = await proc.stderr.read()
                    stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
                raise McpToolError(
                    f"MCP server '{self.server_name}' closed stdout unexpectedly "
                    f"(returncode={proc.returncode}, stderr={stderr_text!r})"
                )
            line_str = raw_line.decode("utf-8").strip()
            if not line_str:
                continue
            parsed = json.loads(line_str)
            if isinstance(parsed, dict) and (
                expected_id is None or parsed.get("id") == expected_id
            ):
                return parsed
            if isinstance(parsed, dict) and "method" not in parsed:
                return parsed

    async def _send_over_persistent_stdio(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON-RPC request over the persistent MCP server subprocess."""
        async with self._lock:
            proc = await self._ensure_process_and_handshake()
            return await self._write_and_read_jsonrpc(proc, request)

    async def close(self) -> None:
        """Terminate the persistent MCP server subprocess if running."""
        async with self._lock:
            proc = self._process
            self._process = None
            self._initialized = False
            if proc is not None and proc.returncode is None:
                if proc.stdin is not None:
                    proc.stdin.close()
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except TimeoutError:
                    proc.kill()
                    await proc.wait()


class ObservabilityMcpClient(StdioMcpClient):
    """Typed client for Google `observability-mcp` (`@google-cloud/observability-mcp`)."""

    def __init__(
        self,
        *,
        command: list[str] | None = None,
        env: dict[str, str] | None = None,
        transport: McpTransportCallable | None = None,
    ) -> None:
        super().__init__(
            "observability-mcp",
            command=command,
            env=env,
            transport=transport,
        )

    async def list_log_entries(
        self,
        *,
        filter_str: str,
        page_size: int = 50,
        project_id: str | None = None,
    ) -> Any:
        if not filter_str or not filter_str.strip():
            raise ValueError("observability-mcp list_log_entries requires a non-empty filter")
        if page_size < 1 or page_size > 50:
            raise ValueError(
                f"observability-mcp list_log_entries enforces pageSize <= 50 (got {page_size}) per TDD §3.7"
            )
        args: dict[str, Any] = {"filter": filter_str, "pageSize": page_size}
        if project_id:
            args["projectId"] = project_id
        return await self.call_tool("list_log_entries", args)

    async def list_timeseries(
        self,
        *,
        filter_str: str,
        interval_seconds: int = 300,
        project_id: str | None = None,
    ) -> Any:
        args: dict[str, Any] = {"filter": filter_str, "intervalSeconds": interval_seconds}
        if project_id:
            args["projectId"] = project_id
        return await self.call_tool("list_timeseries", args)

    async def list_traces(
        self,
        *,
        filter_str: str = "",
        page_size: int = 20,
        project_id: str | None = None,
    ) -> Any:
        args: dict[str, Any] = {"filter": filter_str, "pageSize": min(page_size, 50)}
        if project_id:
            args["projectId"] = project_id
        return await self.call_tool("list_traces", args)

    async def get_trace(self, *, trace_id: str, project_id: str | None = None) -> Any:
        if not trace_id or not trace_id.strip():
            raise ValueError("observability-mcp get_trace requires a non-empty trace_id")
        args: dict[str, Any] = {"traceId": trace_id}
        if project_id:
            args["projectId"] = project_id
        return await self.call_tool("get_trace", args)


class GkeMcpClient(StdioMcpClient):
    """Typed client for Google `gke-mcp` (`GoogleCloudPlatform/gke-mcp`)."""

    def __init__(
        self,
        *,
        command: list[str] | None = None,
        env: dict[str, str] | None = None,
        transport: McpTransportCallable | None = None,
    ) -> None:
        super().__init__("gke-mcp", command=command, env=env, transport=transport)

    async def list_clusters(
        self, *, project_id: str | None = None, location: str | None = None
    ) -> Any:
        args: dict[str, Any] = {}
        if project_id:
            args["projectId"] = project_id
        if location:
            args["location"] = location
        return await self.call_tool("list_clusters", args)

    async def get_cluster(
        self, *, name: str, project_id: str | None = None, location: str | None = None
    ) -> Any:
        if not name or not name.strip():
            raise ValueError("gke-mcp get_cluster requires a non-empty cluster name")
        args: dict[str, Any] = {"name": name}
        if project_id:
            args["projectId"] = project_id
        if location:
            args["location"] = location
        return await self.call_tool("get_cluster", args)

    async def get_k8s_resource(
        self,
        *,
        kind: str,
        name: str,
        namespace: str | None = None,
        cluster_name: str | None = None,
    ) -> Any:
        if not kind or not name:
            raise ValueError("gke-mcp get_k8s_resource requires non-empty kind and name")
        args: dict[str, Any] = {"kind": kind, "name": name}
        if namespace:
            args["namespace"] = namespace
        if cluster_name:
            args["clusterName"] = cluster_name
        return await self.call_tool("get_k8s_resource", args)

    async def query_logs(self, *, query: str, limit: int = 50) -> Any:
        if not query or not query.strip():
            raise ValueError("gke-mcp query_logs requires a non-empty query")
        if limit < 1 or limit > 50:
            raise ValueError(f"gke-mcp query_logs enforces limit <= 50 (got {limit})")
        return await self.call_tool("query_logs", {"query": query, "limit": limit})

    async def get_log_schema(self) -> Any:
        return await self.call_tool("get_log_schema", {})

    async def list_recommendations(
        self, *, project_id: str | None = None, location: str | None = None
    ) -> Any:
        args: dict[str, Any] = {}
        if project_id:
            args["projectId"] = project_id
        if location:
            args["location"] = location
        return await self.call_tool("list_recommendations", args)


class PostgresToolboxMcpClient(StdioMcpClient):
    """Typed client for Google MCP Toolbox for Databases (`mcp-toolbox-postgres`)."""

    def __init__(
        self,
        *,
        command: list[str] | None = None,
        env: dict[str, str] | None = None,
        transport: McpTransportCallable | None = None,
    ) -> None:
        super().__init__(
            "mcp-toolbox-postgres",
            command=command,
            env=env,
            transport=transport,
        )

    async def list_tables(self, *, schema: str = "public") -> Any:
        return await self.call_tool("list_tables", {"schema": schema})

    async def execute_sql(self, *, sql: str, params: list[Any] | None = None) -> Any:
        args: dict[str, Any] = {"sql": sql}
        if params is not None:
            args["params"] = params
        return await self.call_tool("execute_sql", args)

    async def list_active_queries(self) -> Any:
        return await self.call_tool("list_active_queries", {})

    async def get_query_plan(self, *, sql: str) -> Any:
        return await self.call_tool("get_query_plan", {"sql": sql})


class GitHubMcpClient(StdioMcpClient):
    """Typed client for official `github-mcp` (`github/github-mcp-server`)."""

    def __init__(
        self,
        *,
        command: list[str] | None = None,
        env: dict[str, str] | None = None,
        transport: McpTransportCallable | None = None,
    ) -> None:
        super().__init__("github-mcp", command=command, env=env, transport=transport)

    async def list_commits(
        self, *, owner: str, repo: str, sha: str | None = None, per_page: int = 20
    ) -> Any:
        args: dict[str, Any] = {"owner": owner, "repo": repo, "perPage": per_page}
        if sha:
            args["sha"] = sha
        return await self.call_tool("list_commits", args)

    async def get_commit(self, *, owner: str, repo: str, sha: str) -> Any:
        return await self.call_tool("get_commit", {"owner": owner, "repo": repo, "sha": sha})

    async def get_file_contents(
        self, *, owner: str, repo: str, path: str, ref: str | None = None
    ) -> Any:
        args: dict[str, Any] = {"owner": owner, "repo": repo, "path": path}
        if ref:
            args["branch"] = ref
        return await self.call_tool("get_file_contents", args)

    async def create_branch(
        self, *, owner: str, repo: str, branch: str, from_branch: str = "main"
    ) -> Any:
        return await self.call_tool(
            "create_branch",
            {"owner": owner, "repo": repo, "branch": branch, "from_branch": from_branch},
        )

    async def push_files(
        self,
        *,
        owner: str,
        repo: str,
        branch: str,
        files: list[dict[str, str]],
        message: str,
    ) -> Any:
        return await self.call_tool(
            "push_files",
            {
                "owner": owner,
                "repo": repo,
                "branch": branch,
                "files": files,
                "message": message,
            },
        )

    async def create_issue(
        self,
        *,
        owner: str,
        repo: str,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> Any:
        args: dict[str, Any] = {"owner": owner, "repo": repo, "title": title, "body": body}
        if labels:
            args["labels"] = labels
        return await self.call_tool("create_issue", args)

    async def create_pull_request(
        self,
        *,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str = "main",
    ) -> Any:
        return await self.call_tool(
            "create_pull_request",
            {
                "owner": owner,
                "repo": repo,
                "title": title,
                "body": body,
                "head": head,
                "base": base,
            },
        )
