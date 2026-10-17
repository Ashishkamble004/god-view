"""PostgreSQL Sense Adapter (`godview/adapters/sql/adapter.py`).

Implements SDD §2.2, §2.3, §5.1, §10, Coordinator Decision A12 (`godview_readonly` role with
`default_transaction_read_only=on` and `statement_timeout='5s'`), TDD §1 (I-02, I-03, I-04,
I-05, I-06), §3.7, and Agent Spec §4.2, §5.1, §6, §8:
- Enforces strict read-only SQL during sensing (`assert_readonly_sql`) and refuses any query
  against `FORBIDDEN_TELEMETRY_TABLES` (`service_metric_rollups`, `otel_trace_spans`).
- Discovers tables, columns, and correlation keys from `information_schema`.
- Queries `pg_stat_statements` slow queries and `pg_locks` + `pg_stat_activity` + `pg_blocking_pids()`
  blocking trees (`sql.blocking_tree`).
- Evaluates logical-decoding readiness (`wal_level = 'logical'`, replication slots, wal senders,
  `pg_stat_statements`) for Phase 2 CDC and `health()` reporting (`available`/`partial`/`unavailable`).
- Executes `sql.terminate_backend` (L3, `TrustDecision` required) via the MCP Toolbox
  (`mcp-toolbox-postgres` -> `execute_sql`) when a Toolbox client is discovered/injected,
  or via direct `psycopg` 3 driver when no Toolbox is present.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable
from urllib.parse import urlparse, urlunparse

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None  # type: ignore[assignment]
    dict_row = None  # type: ignore[assignment]
from godview_shared.adapter_contract import (
    AdapterHealth,
    DiscoveryResult,
    SenseAdapter,
    SenseCategory,
)
from godview_shared.capabilities import (
    CapabilitySpec,
    get_capability,
    validate_mcp_tool_binding,
)
from godview_shared.config import MANAGED_LABEL_KEY, MANAGED_LABEL_VALUE, is_excluded_resource
from godview_shared.contracts import (
    FORBIDDEN_TELEMETRY_TABLES,
    DiscoveredEntityDoc,
    DiscoveredServiceDoc,
    TrustDecision,
    TrustViolation,
)

_PsycopgError: type[Exception] = getattr(psycopg, "Error", Exception) if psycopg is not None else Exception


class ReadOnlyViolationError(ValueError):
    """Raised when a sensing query attempts a mutation or touches a forbidden telemetry table (I-04, A12)."""


_FORBIDDEN_SQL_VERBS_RE = re.compile(
    r"\b("
    r"insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|call|do|"
    r"vacuum|reindex|cluster|lock|comment|security|reassign|refresh|"
    r"set_config|setval|lo_[a-z0-9_]+|dblink[a-z0-9_]*|pg_(?:try_)?advisory_[a-z0-9_]+|"
    r"pg_terminate_backend|pg_cancel_backend|pg_reload_conf|pg_rotate_logfile|"
    r"pg_promote|pg_sleep|pg_read_server_files|pg_write_file|pg_execute_server_program"
    r")\b",
    re.IGNORECASE,
)
_FORBIDDEN_LOCK_CLAUSE_RE = re.compile(
    r"\bfor\s+(?:update|share|no\s+key\s+update|key\s+share)\b",
    re.IGNORECASE,
)
_SAFE_ROLE_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

_COMMENT_LINE_RE = re.compile(r"--[^\r\n]*")
_COMMENT_BLOCK_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING_LITERAL_RE = re.compile(r"'(?:''|[^'])*'")


def _strip_comments_and_literals(sql: str) -> str:
    without_block = _COMMENT_BLOCK_RE.sub(" ", sql)
    without_line = _COMMENT_LINE_RE.sub(" ", without_block)
    return _STRING_LITERAL_RE.sub("''", without_line)


def assert_readonly_sql(sql: str) -> str:
    """Validate that a SQL query is strictly read-only and does not touch forbidden telemetry tables (I-04, A12)."""
    if not sql or not sql.strip():
        raise ReadOnlyViolationError("Empty SQL statement is not permitted")

    cleaned = _strip_comments_and_literals(sql).strip()
    statements = [stmt.strip() for stmt in cleaned.split(";") if stmt.strip()]
    if len(statements) != 1:
        raise ReadOnlyViolationError(
            "Multiple SQL statements per sensing call are forbidden (expected exactly 1 read-only statement)"
        )

    stmt = statements[0]
    first_token = stmt.split()[0].lower() if stmt.split() else ""
    if first_token not in ("select", "with", "show", "explain"):
        raise ReadOnlyViolationError(
            f"Sensing SQL must begin with SELECT, WITH, SHOW, or EXPLAIN (got '{first_token}')"
        )

    if first_token == "explain" and re.search(r"\banalyze\b", stmt, re.IGNORECASE):
        raise ReadOnlyViolationError("EXPLAIN ANALYZE is forbidden on sensing connections")

    verb_match = _FORBIDDEN_SQL_VERBS_RE.search(stmt)
    if verb_match:
        raise ReadOnlyViolationError(
            f"Forbidden mutating or administrative token '{verb_match.group(1)}' in read-only sensing query"
        )

    lock_match = _FORBIDDEN_LOCK_CLAUSE_RE.search(stmt)
    if lock_match:
        raise ReadOnlyViolationError(
            f"Forbidden row-locking clause '{lock_match.group(0)}' in read-only sensing query"
        )

    lowered_raw = cleaned.lower()
    for forbidden_table in FORBIDDEN_TELEMETRY_TABLES:
        if re.search(rf"\b{re.escape(forbidden_table.lower())}\b", lowered_raw):
            raise ReadOnlyViolationError(
                f"Query references forbidden patient telemetry table '{forbidden_table}' (TDD I-04)"
            )

    return sql.strip()


def redact_dsn_password(dsn: str) -> str:
    """Return a PostgreSQL DSN with any embedded password removed for safe locators/evidence."""
    if not dsn:
        return ""
    try:
        parsed = urlparse(dsn)
        if not parsed.scheme:
            return dsn
        netloc = parsed.hostname or "localhost"
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        if parsed.username:
            netloc = f"{parsed.username}@{netloc}"
        return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))
    except ValueError:
        return "postgresql://redacted"


@runtime_checkable
class McpToolClientProtocol(Protocol):
    """Protocol agreed with @adapters-gcp (#72/#73) for MCP server clients."""

    server_name: str

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any: ...


@runtime_checkable
class SqlDriverProtocol(Protocol):
    """Protocol for direct PostgreSQL query execution (psycopg 3 or hermetic test double)."""

    async def execute_query(
        self,
        sql: str,
        params: tuple[Any, ...] | None = None,
        *,
        role: str = "godview_readonly",
        readonly: bool = True,
    ) -> list[dict[str, Any]]: ...


class Psycopg3Driver:
    """Direct psycopg 3 async driver enforcing Decision A12 read isolation and §10 identity split."""

    def __init__(
        self,
        dsn: str,
        *,
        act_dsn: str | None = None,
        statement_timeout: str = "5s",
    ) -> None:
        self.dsn = dsn.strip()
        self.act_dsn = (act_dsn or "").strip()
        self.statement_timeout = statement_timeout

    async def execute_query(
        self,
        sql: str,
        params: tuple[Any, ...] | None = None,
        *,
        role: str = "godview_readonly",
        readonly: bool = True,
    ) -> list[dict[str, Any]]:
        if not _SAFE_ROLE_RE.match(role):
            raise ValueError(f"Unsafe SQL role identifier: {role!r}")
        if readonly:
            assert_readonly_sql(sql)
            target_dsn = self.dsn
        else:
            if (
                not self.act_dsn
                or self.act_dsn == self.dsn
                or "godview_readonly" in self.act_dsn
                or role == "godview_readonly"
            ):
                raise PermissionError(
                    "Mutating SQL requires a separate actuation DSN (act_dsn / GODVIEW_SQL_ACT_DSN) "
                    "and actuation role distinct from godview_readonly (Agent Spec §10, Decision A12)"
                )
            target_dsn = self.act_dsn

        conn = await psycopg.AsyncConnection.connect(
            target_dsn,
            autocommit=True,
            row_factory=dict_row,
        )
        try:
            async with conn.cursor() as cur:
                await cur.execute(f'SET ROLE "{role}"')
                if readonly:
                    await cur.execute("SET default_transaction_read_only = on")
                    await cur.execute(f"SET statement_timeout = '{self.statement_timeout}'")
                await cur.execute(sql, params)
                if cur.description is None:
                    return []
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
        finally:
            await conn.close()


def _parse_terminate_backend_result(rows: Any) -> bool:
    """Strictly parse a boolean result from pg_terminate_backend; raise on errors or unexpected shapes."""
    if isinstance(rows, list):
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise RuntimeError(
                f"Expected single-row dict list from pg_terminate_backend, got: {rows!r}"
            )
        row = rows[0]
    elif isinstance(rows, dict):
        row = rows
    else:
        raise RuntimeError(f"Unexpected response type from pg_terminate_backend: {rows!r}")

    if "error" in row:
        raise RuntimeError(f"pg_terminate_backend returned error: {row['error']!r}")

    for key in ("pg_terminate_backend", "terminated"):
        if key in row:
            val = row[key]
            if isinstance(val, bool):
                return val
            raise RuntimeError(
                f"Column '{key}' from pg_terminate_backend is not a boolean: {val!r}"
            )

    raise RuntimeError(
        f"pg_terminate_backend response missing boolean 'pg_terminate_backend' column: {row!r}"
    )


BLOCKING_TREE_SQL: str = (
    "SELECT a.pid, a.usename, a.state, a.wait_event_type, a.wait_event, a.query, "
    "pg_blocking_pids(a.pid) AS blocked_by "
    "FROM pg_stat_activity a "
    "JOIN pg_locks l ON l.pid = a.pid "
    "WHERE NOT l.granted"
)


def _validate_sql_mutating_trust(
    capability_id: str,
    spec: CapabilitySpec,
    trust_decision: TrustDecision | None,
) -> TrustDecision:
    if trust_decision is None or not trust_decision.authorized:
        raise TrustViolation(
            f"Mutating capability '{capability_id}' requires an authorized TrustDecision (I-06)"
        )
    if trust_decision.capability_id != capability_id or trust_decision.action_type != capability_id:
        raise TrustViolation(f"TrustDecision capability/action_type mismatch for '{capability_id}'")
    if trust_decision.required_level != spec.min_level:
        raise TrustViolation(
            f"TrustDecision required_level ({trust_decision.required_level}) != spec.min_level ({spec.min_level})"
        )
    if trust_decision.blast_radius_resources != 1:
        raise TrustViolation(
            f"Blast radius must be 1 backend PID (got {trust_decision.blast_radius_resources})"
        )
    if trust_decision.sa_to_impersonate != "godview-act-sa":
        raise TrustViolation(
            f"Mutating capability '{capability_id}' must impersonate 'godview-act-sa'"
        )
    if trust_decision.effective_level < 2:
        raise TrustViolation(
            f"Mutating capability '{capability_id}' requires effective_level >= 2 "
            f"(got {trust_decision.effective_level})"
        )
    if trust_decision.effective_level < spec.min_level and not trust_decision.approval_id:
        raise TrustViolation(
            f"Capability '{capability_id}' at effective_level={trust_decision.effective_level} "
            f"(< min_level={spec.min_level}) requires an approval_id"
        )
    return trust_decision


class PostgresSenseAdapter(SenseAdapter):
    """PostgreSQL sense and capability adapter (`godview/adapters/sql`)."""

    family: Literal["gcp", "kubernetes", "sql", "git", "channels", "local"] = "sql"
    name: str = "postgres-sense-adapter"

    readonly_role: str = "godview_readonly"
    statement_timeout: str = "5s"
    default_transaction_read_only: bool = True

    def __init__(
        self,
        *,
        dsn: str | None = None,
        act_dsn: str | None = None,
        act_source: str | None = None,
        instance_name: str = "postgres",
        instance_labels: Mapping[str, str] | None = None,
        env_id: str = "sql-cluster",
        toolbox_client: McpToolClientProtocol | None = None,
        act_toolbox_client: McpToolClientProtocol | None = None,
        driver: SqlDriverProtocol | None = None,
    ) -> None:
        resolved_dsn = (
            dsn
            if dsn is not None
            else os.environ.get("GODVIEW_SQL_READONLY_DSN") or os.environ.get("DATABASE_URL") or ""
        ).strip()
        self.dsn = resolved_dsn
        self.act_dsn = (
            act_dsn if act_dsn is not None else os.environ.get("GODVIEW_SQL_ACT_DSN", "")
        ).strip()
        self.act_source = (
            act_source if act_source is not None else os.environ.get("GODVIEW_SQL_ACT_SOURCE", "")
        ).strip()
        self.instance_name = instance_name
        self.instance_labels: dict[str, str] = (
            {str(k): str(v) for k, v in instance_labels.items()}
            if instance_labels is not None
            else {}
        )
        self.env_id = env_id
        self.toolbox_client = toolbox_client
        self.act_toolbox_client = act_toolbox_client
        self._driver: SqlDriverProtocol | None = driver or (
            Psycopg3Driver(self.dsn, act_dsn=self.act_dsn, statement_timeout=self.statement_timeout)
            if self.dsn
            else None
        )

    def _assert_managed_instance(self, args: dict[str, Any]) -> None:
        """Enforce TDD I-07 and Decision A11 before mutating a SQL instance."""
        target_instance = str(args.get("instance") or self.instance_name).strip()
        labels: dict[str, str] = dict(self.instance_labels)
        arg_labels = args.get("labels") or args.get("instance_labels")
        if isinstance(arg_labels, Mapping):
            labels.update({str(k): str(v) for k, v in arg_labels.items()})
        if is_excluded_resource(target_instance, labels):
            raise TrustViolation(
                f"SQL instance '{target_instance}' is excluded by GODVIEW_HINT_EXCLUDE or observer role (A11)"
            )
        if labels.get(MANAGED_LABEL_KEY) != MANAGED_LABEL_VALUE:
            raise TrustViolation(
                f"SQL instance '{target_instance}' does not carry label "
                f"{MANAGED_LABEL_KEY}={MANAGED_LABEL_VALUE!r}; refusing mutation (TDD I-07)"
            )

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.DATA, SenseCategory.SCOPED_OBSERVABILITY]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {
            "sql.list_tables": get_capability("sql.list_tables"),
            "sql.list_active_queries": get_capability("sql.list_active_queries"),
            "sql.get_query_plan": get_capability("sql.get_query_plan"),
            "sql.blocking_tree": get_capability("sql.blocking_tree"),
            "sql.integrity_check": get_capability("sql.integrity_check"),
            "sql.terminate_backend": get_capability("sql.terminate_backend"),
        }

    async def _run_readonly(
        self, sql: str, params: tuple[Any, ...] | None = None
    ) -> list[dict[str, Any]]:
        assert_readonly_sql(sql)
        if self.toolbox_client is not None:
            validate_mcp_tool_binding("mcp-toolbox-postgres", "execute_sql")
            res = await self.toolbox_client.call_tool("execute_sql", {"sql": sql})
            return list(res) if isinstance(res, list) else [res]
        if self._driver is None:
            raise ConnectionError("Neither MCP Toolbox nor psycopg 3 driver is configured")
        return await self._driver.execute_query(
            sql,
            params,
            role=self.readonly_role,
            readonly=True,
        )

    async def check_logical_decoding_readiness(self) -> dict[str, Any]:
        """Inspect wal_level, replication slots, wal senders, and pg_stat_statements."""
        if self._driver is None and self.toolbox_client is None:
            return {
                "reachable": False,
                "wal_level": None,
                "logical_decoding_ready": False,
                "pg_stat_statements_installed": False,
            }

        query_runner = (
            self._driver.execute_query
            if self._driver is not None
            else lambda q, p=None, **kw: self._run_readonly(q, p)
        )
        wal_rows = await query_runner(
            "SHOW wal_level", None, role=self.readonly_role, readonly=True
        )
        slots_rows = await query_runner(
            "SHOW max_replication_slots", None, role=self.readonly_role, readonly=True
        )
        senders_rows = await query_runner(
            "SHOW max_wal_senders", None, role=self.readonly_role, readonly=True
        )
        ext_rows = await query_runner(
            "SELECT extname FROM pg_extension WHERE extname = 'pg_stat_statements'",
            None,
            role=self.readonly_role,
            readonly=True,
        )

        wal_level = str(wal_rows[0].get("wal_level", "")).lower() if wal_rows else ""
        max_slots = int(slots_rows[0].get("max_replication_slots", 0)) if slots_rows else 0
        max_senders = int(senders_rows[0].get("max_wal_senders", 0)) if senders_rows else 0
        has_pgss = bool(ext_rows)

        logical_ready = wal_level == "logical" and max_slots >= 1 and max_senders >= 1
        return {
            "reachable": True,
            "wal_level": wal_level,
            "max_replication_slots": max_slots,
            "max_wal_senders": max_senders,
            "logical_decoding_ready": logical_ready,
            "pg_stat_statements_installed": has_pgss,
        }

    async def health(self) -> AdapterHealth:
        now = datetime.now(tz=UTC)
        if not self.dsn and self.toolbox_client is None and self._driver is None:
            return AdapterHealth(
                adapter_name=self.name,
                family=self.family,
                state="unavailable",
                evidence={
                    "reachable": False,
                    "reason": "No PostgreSQL DSN or MCP Toolbox configured",
                },
                checked_at=now,
            )

        try:
            readiness = await self.check_logical_decoding_readiness()
        except (ConnectionError, OSError, _PsycopgError, RuntimeError, ValueError) as exc:
            return AdapterHealth(
                adapter_name=self.name,
                family=self.family,
                state="unavailable",
                evidence={
                    "reachable": False,
                    "error": str(exc),
                    "readonly_role": self.readonly_role,
                },
                checked_at=now,
            )

        if not readiness.get("reachable"):
            state: Literal["available", "partial", "unavailable"] = "unavailable"
        elif readiness.get("logical_decoding_ready") and readiness.get(
            "pg_stat_statements_installed"
        ):
            state = "available"
        else:
            state = "partial"

        return AdapterHealth(
            adapter_name=self.name,
            family=self.family,
            state=state,
            evidence={
                **readiness,
                "readonly_role": self.readonly_role,
                "default_transaction_read_only": "on",
                "statement_timeout": self.statement_timeout,
                "toolbox_connected": self.toolbox_client is not None,
            },
            checked_at=now,
        )

    async def discover(self) -> DiscoveryResult:
        """Discover PostgreSQL datastore service and public schema entities via information_schema."""
        now_iso = datetime.now(tz=UTC).isoformat()
        service_doc_id = f"{self.env_id}__cloud_sql.{self.instance_name}"

        tables_sql = (
            "SELECT table_name, table_type FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        )
        cols_sql = (
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'public' ORDER BY table_name, ordinal_position"
        )

        if self._driver is not None:
            table_rows = await self._driver.execute_query(tables_sql, readonly=True)
            col_rows = await self._driver.execute_query(cols_sql, readonly=True)
        else:
            table_rows = await self._run_readonly(tables_sql)
            col_rows = await self._run_readonly(cols_sql)

        cols_by_table: dict[str, list[str]] = {}
        for row in col_rows:
            t_name = str(row.get("table_name", ""))
            c_name = str(row.get("column_name", ""))
            if t_name and t_name not in FORBIDDEN_TELEMETRY_TABLES and c_name:
                cols_by_table.setdefault(t_name, []).append(c_name)

        entities: list[DiscoveredEntityDoc] = []
        discovered_table_names: list[str] = []
        for row in table_rows:
            t_name = str(row.get("table_name", ""))
            if not t_name or t_name in FORBIDDEN_TELEMETRY_TABLES:
                continue
            discovered_table_names.append(t_name)
            columns = cols_by_table.get(t_name, [])
            corr_keys = [
                col
                for col in columns
                if col in ("journey_id", "customer_id", "trace_id") or col.endswith("_id")
            ]
            entities.append(
                DiscoveredEntityDoc(
                    doc_id=f"{self.env_id}__{t_name}",
                    name=t_name,
                    correlation_keys=corr_keys,
                    tables=[t_name],
                    owning_service_id=service_doc_id,
                    states=[],
                    provenance="information_schema",
                )
            )

        svc_labels = (
            dict(self.instance_labels)
            if self.instance_labels
            else {MANAGED_LABEL_KEY: MANAGED_LABEL_VALUE}
        )
        svc_doc = DiscoveredServiceDoc(
            doc_id=service_doc_id,
            env_id=self.env_id,
            name=self.instance_name,
            kind="cloud_sql",
            adapter_family="sql",
            locator=redact_dsn_password(self.dsn) or f"postgresql://{self.instance_name}:5432/db",
            labels=svc_labels,
            first_seen=now_iso,
            last_seen=now_iso,
            status="active",
        )

        return DiscoveryResult(
            services=[svc_doc],
            edges=[],
            entities=entities,
            evidence={
                "tables_discovered": discovered_table_names,
                "readonly_role": self.readonly_role,
                "statement_timeout": self.statement_timeout,
            },
        )

    async def get_slow_queries(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Query pg_stat_statements for top slow queries."""
        sql = (
            "SELECT queryid, query, calls, total_exec_time, mean_exec_time, rows "
            f"FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT {int(limit)}"
        )
        if self._driver is not None and self.toolbox_client is None:
            return await self._driver.execute_query(sql, readonly=True)
        return await self._run_readonly(sql)

    async def _build_blocking_tree(self) -> dict[str, Any]:
        via = "mcp-toolbox-postgres" if self.toolbox_client is not None else "psycopg3"
        if self.toolbox_client is not None:
            validate_mcp_tool_binding("mcp-toolbox-postgres", "execute_sql")
            raw_rows = await self.toolbox_client.call_tool(
                "execute_sql",
                {"sql": BLOCKING_TREE_SQL},
            )
            rows = list(raw_rows) if isinstance(raw_rows, list) else [raw_rows]
        elif self._driver is not None:
            rows = await self._driver.execute_query(BLOCKING_TREE_SQL, readonly=True)
        else:
            raise ConnectionError("No SQL client available for sql.blocking_tree")

        blocked_pids = {int(r["pid"]) for r in rows if "pid" in r}
        root_blocker_pids: set[int] = set()
        edges: list[dict[str, int]] = []

        for row in rows:
            waiter_pid = int(row["pid"])
            blockers = row.get("blocked_by") or []
            if isinstance(blockers, (list, tuple)):
                for b_pid in blockers:
                    b_int = int(b_pid)
                    edges.append({"blocked_pid": waiter_pid, "blocker_pid": b_int})
                    if b_int not in blocked_pids:
                        root_blocker_pids.add(b_int)
            if "root_blocker_pid" in row and row["root_blocker_pid"] is not None:
                root_blocker_pids.add(int(row["root_blocker_pid"]))

        return {
            "via": via,
            "blocked_queries": rows,
            "root_blocker_pids": sorted(root_blocker_pids),
            "edges": edges,
        }

    async def _execute_capability_impl(
        self,
        capability_id: str,
        args: dict[str, Any],
        trust_decision: TrustDecision | None = None,
    ) -> Any:
        spec = get_capability(capability_id)

        if capability_id == "sql.list_tables":
            if self.toolbox_client is not None:
                validate_mcp_tool_binding("mcp-toolbox-postgres", "list_tables")
                return await self.toolbox_client.call_tool("list_tables", {})
            sql = (
                "SELECT table_name, table_type FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            )
            return await self._run_readonly(sql)

        if capability_id == "sql.list_active_queries":
            if self.toolbox_client is not None:
                validate_mcp_tool_binding("mcp-toolbox-postgres", "list_active_queries")
                return await self.toolbox_client.call_tool("list_active_queries", {})
            sql = (
                "SELECT pid, usename, state, wait_event_type, wait_event, query "
                "FROM pg_stat_activity WHERE state != 'idle'"
            )
            return await self._run_readonly(sql)

        if capability_id == "sql.get_query_plan":
            raw_sql = str(args.get("sql", "")).strip()
            assert_readonly_sql(raw_sql)
            if self.toolbox_client is not None:
                validate_mcp_tool_binding("mcp-toolbox-postgres", "get_query_plan")
                return await self.toolbox_client.call_tool("get_query_plan", {"sql": raw_sql})
            if self._driver is None:
                raise ConnectionError("No SQL driver configured for sql.get_query_plan")
            return await self._driver.execute_query(
                f"EXPLAIN (FORMAT JSON) {raw_sql}", readonly=True
            )

        if capability_id == "sql.blocking_tree":
            return await self._build_blocking_tree()

        if capability_id == "sql.integrity_check":
            raw_sql = str(args.get("sql", "")).strip()
            assert_readonly_sql(raw_sql)
            return await self._run_readonly(raw_sql)

        if capability_id == "sql.terminate_backend":
            decision = _validate_sql_mutating_trust(capability_id, spec, trust_decision)
            self._assert_managed_instance(args)
            if "pid" not in args:
                raise ValueError("sql.terminate_backend requires integer 'pid' in args")
            pid = int(args["pid"])
            if pid <= 0:
                raise ValueError(f"Invalid backend PID {pid}; must be a positive integer")

            term_sql = f"SELECT pg_terminate_backend({pid});"
            if self.act_toolbox_client is not None or self.toolbox_client is not None:
                has_separate_act_source = (
                    self.act_toolbox_client is not None
                    or (bool(self.act_source) and self.act_source != self.readonly_role)
                    or (
                        bool(self.act_dsn)
                        and self.act_dsn != self.dsn
                        and self.readonly_role not in self.act_dsn
                    )
                )
                if not has_separate_act_source:
                    raise PermissionError(
                        "sql.terminate_backend via MCP Toolbox requires a separate actuation identity "
                        "(act_toolbox_client, act_source / GODVIEW_SQL_ACT_SOURCE, or act_dsn / GODVIEW_SQL_ACT_DSN) "
                        "distinct from the read-only sensing source (Agent Spec §10, Decision A12)"
                    )
                target_toolbox = self.act_toolbox_client or self.toolbox_client
                assert target_toolbox is not None
                validate_mcp_tool_binding("mcp-toolbox-postgres", "execute_sql")
                rows = await target_toolbox.call_tool(
                    "execute_sql",
                    {
                        "sql": term_sql,
                        "source": self.act_source or decision.sa_to_impersonate,
                        "role": decision.sa_to_impersonate,
                    },
                )
                via = "mcp-toolbox-postgres"
            elif self._driver is not None:
                if (
                    not self.act_dsn
                    or self.act_dsn == self.dsn
                    or self.readonly_role in self.act_dsn
                ):
                    raise PermissionError(
                        "sql.terminate_backend requires a separate actuation DSN "
                        "(act_dsn / GODVIEW_SQL_ACT_DSN) distinct from the read-only sensing DSN "
                        "(Agent Spec §10, Decision A12)"
                    )
                rows = await self._driver.execute_query(
                    "SELECT pg_terminate_backend(%s);",
                    (pid,),
                    role=decision.sa_to_impersonate,
                    readonly=False,
                )
                via = "psycopg3"
            else:
                raise ConnectionError("Neither MCP Toolbox nor psycopg 3 driver is available")

            terminated = _parse_terminate_backend_result(rows)
            return {
                "pid": pid,
                "terminated": terminated,
                "via": via,
                "target_resource": f"postgres://{self.instance_name}/pid/{pid}",
                "reversal_recipe": {
                    "note": "none required — terminated statement is retried by the caller saga",
                },
            }

        raise NotImplementedError(
            f"Adapter '{self.name}' does not implement capability '{capability_id}'"
        )
