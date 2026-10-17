"""Unit tests for the PostgreSQL Sense Adapter (`godview/adapters/sql/`).

Covers TDD §1 (I-02 randomised fixtures, I-04 never read telemetry from the patient & read-only
sensing, I-05 official MCP tools only, I-06 TrustDecision before sql.terminate_backend),
Coordinator Decision A12 (`godview_readonly` role with `default_transaction_read_only=on` and
`statement_timeout='5s'`), TDD §3.7, §6.2 #3, and Agent Spec §4.2, §5.1, §6, §8:
- `information_schema` table/column/constraint discovery into `DiscoveredServiceDoc` & `DiscoveredEntityDoc`
- `pg_stat_statements` slow-query baseline inspection
- `pg_locks` + `pg_stat_activity` + `pg_blocking_pids()` blocking tree (`sql.blocking_tree`)
- Logical-decoding readiness (`wal_level=logical`, `max_replication_slots`, `max_wal_senders`, `pg_stat_statements`)
- Read-only enforcement while sensing (rejects mutating SQL and `FORBIDDEN_TELEMETRY_TABLES`)
- `sql.terminate_backend` (L3) via MCP Toolbox `execute_sql` when discovered else direct psycopg 3 driver
- Availability states: `available`, `partial`, `unavailable`
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from godview_shared.adapter_contract import SenseCategory
from godview_shared.capabilities import validate_mcp_tool_binding
from godview_shared.contracts import FORBIDDEN_TELEMETRY_TABLES, TrustDecision, TrustViolation

from godview.adapters.sql import (
    PostgresSenseAdapter,
    ReadOnlyViolationError,
    assert_readonly_sql,
)


def _rand_name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _make_trust_decision(
    capability_id: str,
    *,
    authorized: bool = True,
    effective_level: int = 3,
    blast_radius_resources: int = 1,
    sa_to_impersonate: str = "godview-act-sa",
) -> TrustDecision:
    return TrustDecision(
        authorized=authorized,
        agent_id=f"data__{_rand_name('db')}",
        action_type=capability_id,
        capability_id=capability_id,
        effective_level=effective_level,
        required_level=3,
        sa_to_impersonate=sa_to_impersonate,
        blast_radius_resources=blast_radius_resources,
        reason="unit test sql trust decision",
        decided_at=datetime.now(tz=UTC).isoformat(),
    )


class _FakeToolboxMcpClient:
    """Fake MCP Toolbox for Databases (postgres) client implementing McpToolClientProtocol (#72/#73)."""

    server_name = "mcp-toolbox-postgres"

    def __init__(self, rows_by_pattern: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._rows_by_pattern = rows_by_pattern or {}

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        validate_mcp_tool_binding(self.server_name, tool_name)
        self.calls.append((tool_name, dict(arguments)))
        sql = arguments.get("sql", "")
        for pattern, rows in self._rows_by_pattern.items():
            if pattern in sql or pattern == tool_name:
                return rows
        return []


class _FakePsycopgDriver:
    """In-memory fake for psycopg 3 driver capturing session settings and executed SQL."""

    def __init__(
        self,
        *,
        wal_level: str = "logical",
        has_pg_stat_statements: bool = True,
        reachable: bool = True,
        table_name: str = "orders_table",
    ) -> None:
        self.wal_level = wal_level
        self.has_pg_stat_statements = has_pg_stat_statements
        self.reachable = reachable
        self.table_name = table_name
        self.session_commands: list[str] = []
        self.queries_executed: list[tuple[str, tuple[Any, ...] | None, str]] = []

    async def execute_query(
        self,
        sql: str,
        params: tuple[Any, ...] | None = None,
        *,
        role: str = "godview_readonly",
        readonly: bool = True,
    ) -> list[dict[str, Any]]:
        if not self.reachable:
            raise ConnectionError("PostgreSQL server unreachable")
        if readonly:
            self.session_commands.append("SET default_transaction_read_only = on")
            self.session_commands.append("SET statement_timeout = '5s'")
        self.queries_executed.append((sql, params, role))

        sql_lower = sql.lower()
        if "show wal_level" in sql_lower:
            return [{"wal_level": self.wal_level}]
        if "show max_replication_slots" in sql_lower:
            return [{"max_replication_slots": "5"}]
        if "show max_wal_senders" in sql_lower:
            return [{"max_wal_senders": "5"}]
        if "show default_transaction_read_only" in sql_lower:
            return [{"default_transaction_read_only": "on"}]
        if "show statement_timeout" in sql_lower:
            return [{"statement_timeout": "5s"}]
        if "pg_extension" in sql_lower:
            return [{"extname": "pg_stat_statements"}] if self.has_pg_stat_statements else []
        if "information_schema.columns" in sql_lower:
            return [
                {"table_name": self.table_name, "column_name": "order_id", "data_type": "uuid"},
                {
                    "table_name": self.table_name,
                    "column_name": "journey_id",
                    "data_type": "character varying",
                },
                {"table_name": self.table_name, "column_name": "customer_id", "data_type": "uuid"},
                {
                    "table_name": self.table_name,
                    "column_name": "status",
                    "data_type": "character varying",
                },
            ]
        if "information_schema.tables" in sql_lower:
            return [{"table_name": self.table_name, "table_type": "BASE TABLE"}]
        if "pg_stat_statements" in sql_lower:
            return [
                {
                    "queryid": 99123,
                    "query": f"SELECT * FROM {self.table_name} WHERE status = $1",
                    "calls": 150,
                    "total_exec_time": 450.0,
                    "mean_exec_time": 3.0,
                    "rows": 150,
                }
            ]
        if "pg_blocking_pids" in sql_lower:
            return [
                {
                    "pid": 4102,
                    "usename": "app_user",
                    "state": "active",
                    "wait_event_type": "Lock",
                    "wait_event": "relation",
                    "query": f"UPDATE {self.table_name} SET status = 'APPROVED'",
                    "blocked_by": [4001],
                    "root_blocker_pid": 4001,
                    "root_blocker_query": f"LOCK TABLE {self.table_name} IN ACCESS EXCLUSIVE MODE",
                    "root_blocker_age_s": 42.5,
                }
            ]
        if "pg_terminate_backend" in sql_lower:
            pid = params[0] if params else 4001
            return [{"pid": pid, "pg_terminate_backend": True}]
        if sql_lower.startswith("explain"):
            return [
                {
                    "QUERY PLAN": [
                        {"Plan": {"Node Type": "Seq Scan", "Relation Name": self.table_name}}
                    ]
                }
            ]
        if "pg_stat_activity" in sql_lower:
            return [
                {
                    "pid": 4001,
                    "state": "idle in transaction",
                    "query": f"LOCK TABLE {self.table_name}",
                }
            ]
        return [{"count": 0}]


@pytest.mark.unit
async def test_sql_readonly_enforcement_and_forbidden_telemetry_tables() -> None:
    """Enforce read-only SQL while sensing (Decision A12) and reject forbidden telemetry tables (I-04)."""
    tbl_name = _rand_name("tbl_contracts")

    # Allowed SELECT and WITH ... SELECT statements pass
    assert_readonly_sql(f"SELECT COUNT(*) FROM {tbl_name} WHERE status = 'APPROVED'")
    assert_readonly_sql(f"WITH recent AS (SELECT * FROM {tbl_name}) SELECT COUNT(*) FROM recent")

    # Mutating statements, session escape functions, large objects, dblink, advisory locks, and row locks must fail loudly
    for bad_sql in [
        f"DELETE FROM {tbl_name}",
        f"UPDATE {tbl_name} SET status = 'REJECTED'",
        f"INSERT INTO {tbl_name} VALUES (1)",
        f"DROP TABLE {tbl_name}",
        f"TRUNCATE {tbl_name}",
        "SELECT pg_terminate_backend(1234)",
        "SELECT set_config('default_transaction_read_only','off',false)",
        "SELECT pg_advisory_lock(42)",
        "SELECT pg_try_advisory_xact_lock(42)",
        "SELECT lo_unlink(1)",
        "SELECT lo_import('/etc/passwd')",
        f"SELECT dblink_exec('dbname=x','DELETE FROM {tbl_name}')",
        f"SELECT 1 FROM {tbl_name} FOR SHARE",
        f"SELECT 1 FROM {tbl_name} FOR KEY SHARE",
        f"SELECT 1 FROM {tbl_name} FOR NO KEY UPDATE",
        f"SELECT 1 FROM {tbl_name} FOR UPDATE",
        f"WITH d AS (DELETE FROM {tbl_name} RETURNING *) SELECT * FROM d",
        f"SELECT * FROM {tbl_name}; DROP TABLE {tbl_name}",
    ]:
        with pytest.raises(ReadOnlyViolationError):
            assert_readonly_sql(bad_sql)

    # Querying forbidden v3 telemetry tables must fail loudly (TDD I-04)
    for forbidden_tbl in FORBIDDEN_TELEMETRY_TABLES:
        with pytest.raises(ReadOnlyViolationError, match=forbidden_tbl):
            assert_readonly_sql(f"SELECT * FROM {forbidden_tbl} LIMIT 10")


@pytest.mark.unit
async def test_sql_discovery_pg_stat_statements_and_logical_decoding_health() -> None:
    """Discover schema/entities, inspect slow queries, and report available/partial/unavailable health."""
    db_name = _rand_name("db-instance")
    tbl_name = _rand_name("tx_applications")
    env_id = _rand_name("env-sql")

    driver = _FakePsycopgDriver(
        wal_level="logical", has_pg_stat_statements=True, table_name=tbl_name
    )
    adapter = PostgresSenseAdapter(
        dsn=f"postgresql://godview_readonly:secret@{db_name}:5432/appdb",
        instance_name=db_name,
        env_id=env_id,
        driver=driver,
    )

    assert SenseCategory.DATA in adapter.senses()
    assert adapter.readonly_role == "godview_readonly"
    assert adapter.statement_timeout == "5s"
    assert adapter.default_transaction_read_only is True

    health = await adapter.health()
    assert health.state == "available"
    assert health.evidence["wal_level"] == "logical"
    assert health.evidence["logical_decoding_ready"] is True
    assert health.evidence["pg_stat_statements_installed"] is True
    assert health.evidence["readonly_role"] == "godview_readonly"
    assert health.evidence["statement_timeout"] == "5s"

    discovery = await adapter.discover()
    assert len(discovery.services) == 1
    assert discovery.services[0].name == db_name
    assert discovery.services[0].kind == "cloud_sql"
    # Password must never appear in locator
    assert "secret" not in discovery.services[0].locator

    assert len(discovery.entities) == 1
    ent = discovery.entities[0]
    assert ent.name == tbl_name
    assert "journey_id" in ent.correlation_keys
    assert "customer_id" in ent.correlation_keys

    slow_queries = await adapter.get_slow_queries(limit=5)
    assert len(slow_queries) == 1
    assert slow_queries[0]["mean_exec_time"] == 3.0

    # Test read-only capabilities on direct driver
    tables_res = await adapter.execute_capability("sql.list_tables", {})
    assert tables_res[0]["table_name"] == tbl_name
    active_res = await adapter.execute_capability("sql.list_active_queries", {})
    assert len(active_res) == 1
    plan_res = await adapter.execute_capability(
        "sql.get_query_plan",
        {"sql": f"SELECT * FROM {tbl_name} WHERE status = 'APPROVED'"},
    )
    assert "QUERY PLAN" in plan_res[0]
    integrity_res = await adapter.execute_capability(
        "sql.integrity_check",
        {"sql": f"SELECT COUNT(*) FROM {tbl_name}"},
    )
    assert integrity_res == [{"count": 0}]

    # Mutating SQL inside sql.integrity_check or sql.get_query_plan must fail loudly
    with pytest.raises(ReadOnlyViolationError):
        await adapter.execute_capability(
            "sql.integrity_check",
            {"sql": f"DELETE FROM {tbl_name}"},
        )

    # Partial health when wal_level != 'logical'
    partial_driver = _FakePsycopgDriver(
        wal_level="replica", has_pg_stat_statements=True, table_name=tbl_name
    )
    partial_adapter = PostgresSenseAdapter(
        dsn=f"postgresql://godview_readonly:secret@{db_name}:5432/appdb",
        instance_name=db_name,
        env_id=env_id,
        driver=partial_driver,
    )
    h_partial = await partial_adapter.health()
    assert h_partial.state == "partial"
    assert h_partial.evidence["logical_decoding_ready"] is False

    # Unavailable health when DB is unreachable
    unavail_driver = _FakePsycopgDriver(reachable=False)
    unavail_adapter = PostgresSenseAdapter(
        dsn=f"postgresql://godview_readonly:secret@{db_name}:5432/appdb",
        instance_name=db_name,
        env_id=env_id,
        driver=unavail_driver,
    )
    h_unavail = await unavail_adapter.health()
    assert h_unavail.state == "unavailable"


@pytest.mark.unit
async def test_sql_blocking_tree_and_terminate_backend_via_toolbox_and_psycopg() -> None:
    """Test sql.blocking_tree and sql.terminate_backend via MCP Toolbox when discovered vs direct psycopg3."""
    tbl_name = _rand_name("ledger_rows")
    toolbox = _FakeToolboxMcpClient(
        rows_by_pattern={
            "pg_blocking_pids": [
                {
                    "pid": 5201,
                    "usename": "app_user",
                    "wait_event_type": "Lock",
                    "query": f"UPDATE {tbl_name} SET status = 'DONE'",
                    "blocked_by": [5100],
                }
            ],
            "pg_terminate_backend": [{"pg_terminate_backend": True, "pid": 5100}],
            "list_tables": [{"table_name": tbl_name}],
        }
    )
    driver = _FakePsycopgDriver(table_name=tbl_name)

    # 1. Identity split & I-07 managed check (Findings 4 & 6):
    # Without GODVIEW_SQL_ACT_DSN, act_dsn does not fall back to the read-only DSN
    adapter_no_act = PostgresSenseAdapter(
        dsn="postgresql://godview_readonly:pw@localhost:5432/db",
        instance_labels={"godview_managed": "true"},
        toolbox_client=toolbox,
        driver=driver,
    )
    assert adapter_no_act.act_dsn == ""

    td = _make_trust_decision("sql.terminate_backend", effective_level=3)

    # Missing actuation DSN/source refuses with PermissionError
    with pytest.raises(PermissionError, match="separate actuation"):
        await adapter_no_act.execute_capability(
            "sql.terminate_backend",
            {"pid": 5100},
            trust_decision=td,
        )

    # Unmanaged SQL instance refuses with TrustViolation (TDD I-07)
    adapter_unmanaged = PostgresSenseAdapter(
        dsn="postgresql://godview_readonly:pw@localhost:5432/db",
        act_dsn="postgresql://godview_act:pw@localhost:5432/db",
        instance_labels={"godview_managed": "false"},
        toolbox_client=toolbox,
        driver=driver,
    )
    with pytest.raises(TrustViolation, match="godview_managed"):
        await adapter_unmanaged.execute_capability(
            "sql.terminate_backend",
            {"pid": 5100},
            trust_decision=td,
        )

    # 2. With MCP Toolbox discovered and actuation identity + managed label configured:
    adapter_with_toolbox = PostgresSenseAdapter(
        dsn="postgresql://godview_readonly:pw@localhost:5432/db",
        act_dsn="postgresql://godview_act:pw@localhost:5432/db",
        instance_labels={"godview_managed": "true"},
        toolbox_client=toolbox,
        driver=driver,
    )
    tree_res = await adapter_with_toolbox.execute_capability("sql.blocking_tree", {})
    assert tree_res["via"] == "mcp-toolbox-postgres"
    assert 5100 in tree_res["root_blocker_pids"]
    assert len(tree_res["blocked_queries"]) == 1

    # Mutating sql.terminate_backend without TrustDecision fails (TDD I-06)
    with pytest.raises(TrustViolation):
        await adapter_with_toolbox.execute_capability("sql.terminate_backend", {"pid": 5100})

    # Mutating sql.terminate_backend with valid L3 TrustDecision executes via Toolbox
    term_toolbox = await adapter_with_toolbox.execute_capability(
        "sql.terminate_backend",
        {"pid": 5100},
        trust_decision=td,
    )
    assert term_toolbox["terminated"] is True
    assert term_toolbox["pid"] == 5100
    assert term_toolbox["via"] == "mcp-toolbox-postgres"
    assert any(
        call[0] == "execute_sql" and "pg_terminate_backend(5100)" in call[1]["sql"]
        for call in toolbox.calls
    )

    # Error / empty / non-boolean Toolbox responses raise RuntimeError; False returns terminated=False (Finding 2)
    class _RawResultToolbox:
        server_name = "mcp-toolbox-postgres"

        def __init__(self, raw_result: Any) -> None:
            self.raw_result = raw_result

        async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
            return self.raw_result

    for bad_toolbox_res in (
        [{"result": "error: permission denied"}],
        "ERROR",
        [],
        [{"pg_terminate_backend": "not-a-bool"}],
    ):
        bad_tb_adapter = PostgresSenseAdapter(
            dsn="postgresql://godview_readonly:pw@localhost:5432/db",
            act_dsn="postgresql://godview_act:pw@localhost:5432/db",
            instance_labels={"godview_managed": "true"},
            toolbox_client=_RawResultToolbox(bad_toolbox_res),
        )
        with pytest.raises(RuntimeError):
            await bad_tb_adapter.execute_capability(
                "sql.terminate_backend",
                {"pid": 5100},
                trust_decision=td,
            )

    false_tb_adapter = PostgresSenseAdapter(
        dsn="postgresql://godview_readonly:pw@localhost:5432/db",
        act_dsn="postgresql://godview_act:pw@localhost:5432/db",
        instance_labels={"godview_managed": "true"},
        toolbox_client=_RawResultToolbox([{"pg_terminate_backend": False}]),
    )
    false_res = await false_tb_adapter.execute_capability(
        "sql.terminate_backend",
        {"pid": 5100},
        trust_decision=td,
    )
    assert false_res["terminated"] is False

    # 3. Without MCP Toolbox: falls back to direct psycopg 3 driver with separate act_dsn
    adapter_direct = PostgresSenseAdapter(
        dsn="postgresql://godview_readonly:pw@localhost:5432/db",
        act_dsn="postgresql://godview_act:pw@localhost:5432/db",
        instance_labels={"godview_managed": "true"},
        toolbox_client=None,
        driver=driver,
    )
    tree_direct = await adapter_direct.execute_capability("sql.blocking_tree", {})
    assert tree_direct["via"] == "psycopg3"
    assert 4001 in tree_direct["root_blocker_pids"]

    term_direct = await adapter_direct.execute_capability(
        "sql.terminate_backend",
        {"pid": 4001},
        trust_decision=td,
    )
    assert term_direct["terminated"] is True
    assert term_direct["pid"] == 4001
    assert term_direct["via"] == "psycopg3"
    assert driver.queries_executed[-1][2] == "godview-act-sa"
