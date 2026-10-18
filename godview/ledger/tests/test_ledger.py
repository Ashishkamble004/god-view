"""L1 Hermetic Unit & Contract Tests for godview/ledger (TDD §2.2, §4, Decision A8 & A13).

All test data uses environment-neutral service and resource names (TDD I-02).
Every SQL statement (DDL and KPI rollup queries) is parsed with sqlglot (BigQuery dialect).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlglot
from godview_shared.contracts import LEDGER_TABLES
from godview_shared.fakes import InMemoryBigQuery
from godview_shared.otel import get_tracer, set_business_span_attributes
from sqlglot import exp

from godview.ledger.ddl import (
    DDL_STATEMENTS,
    TABLE_SPECS,
    get_all_ddl_statements,
    get_table_ddl,
    load_table_schema,
)
from godview.ledger.exporter import (
    InMemoryStorageWriteStream,
    LedgerSpanExporter,
)
from godview.ledger.kpi import (
    ALL_KPIS,
    KPI_SQL_TEMPLATES,
    KpiEngine,
    build_kpi_rollup_sql,
)
from godview.ledger.process_mining import (
    DirectlyFollowsMiner,
    classify_journey_conformance,
)

STEP_VERIFY = "STEP_VERIFY"
STEP_DECIDE = "STEP_DECIDE"
STEP_POLICY = "STEP_POLICY"
STEP_SETTLE = "STEP_SETTLE"
CANONICAL_ORDER = (STEP_VERIFY, STEP_DECIDE, STEP_POLICY, STEP_SETTLE)


@pytest.mark.unit
@pytest.mark.contract
def test_ledger_ddl_and_json_schemas_reconciled() -> None:
    """TDD §2.2: 4 BigQuery tables with exact schemas, partitioning, clustering, and sqlglot parse."""
    assert tuple(LEDGER_TABLES) == ("events", "state_transitions", "journeys", "kpi_rollups")
    assert set(DDL_STATEMENTS.keys()) == set(LEDGER_TABLES)

    repo_root = Path(__file__).resolve().parents[3]
    ledger_schema_dir = repo_root / "godview" / "ledger" / "schemas"
    tf_schema_dir = (
        repo_root / "infra" / "terraform" / "platform" / "modules" / "03-data" / "schemas"
    )

    expected_partition_cluster = {
        "events": ("event_time", ["journey_id", "service", "event_type"]),
        "state_transitions": ("transition_time", ["entity", "journey_id", "status_to"]),
        "journeys": ("started_at", ["journey_name", "conformance", "final_status"]),
        "kpi_rollups": ("window_start", ["journey_name", "kpi"]),
    }

    for table in LEDGER_TABLES:
        # 1. JSON schema exists and matches infra/terraform/platform/modules/03-data/schemas/*.json
        schema_path = ledger_schema_dir / f"{table}.json"
        assert schema_path.is_file(), f"Missing schema file {schema_path}"
        ledger_fields = load_table_schema(table)
        assert isinstance(ledger_fields, list) and len(ledger_fields) > 0

        tf_path = tf_schema_dir / f"{table}.json"
        if tf_path.is_file():
            tf_fields = json.loads(tf_path.read_text(encoding="utf-8"))
            tf_names = {f["name"] for f in tf_fields}
            if "entity_type" in tf_names or table != "events":
                assert ledger_fields == tf_fields, (
                    f"Schema drift for {table} between ledger and 03-data"
                )

        # 2. DDL parses cleanly in sqlglot BigQuery dialect and has partition/cluster
        ddl = get_table_ddl(table, dataset="godview_ledger")
        parsed = sqlglot.parse_one(ddl, read="bigquery")
        assert isinstance(parsed, exp.Create), f"DDL for {table} did not parse as CREATE"

        part_col, cluster_cols = expected_partition_cluster[table]
        assert TABLE_SPECS[table].partition_field == part_col
        assert list(TABLE_SPECS[table].cluster_fields) == cluster_cols
        assert f"DATE({part_col})" in ddl
        assert ", ".join(cluster_cols) in ddl

    all_ddls = get_all_ddl_statements(dataset="godview_ledger")
    assert len(all_ddls) == 4


@pytest.mark.unit
def test_otel_to_bigquery_exporter_events_transitions_and_dedup() -> None:
    """Decision A13 & TDD §2.2: OTel exporter writes via Storage Write API default stream, dedups by event_id, and stitches journeys."""
    bq = InMemoryBigQuery()
    write_stream = InMemoryStorageWriteStream(bq)
    exporter = LedgerSpanExporter(
        bigquery=bq,
        env_id="env-test-01",
        default_journey_name="primary_business_flow",
        write_stream=write_stream,
        intended_order=CANONICAL_ORDER,
    )

    tracer = get_tracer("svc-step1-node")
    with tracer.start_as_current_span("POST /internal/verify") as span:
        set_business_span_attributes(
            span,
            journey_id="jrn-100",
            customer_id="cust-100",
            event_type="step1.verified",
            entity="item_document",
            entity_id="doc-100",
            status_from="PENDING",
            status_to="VERIFIED",
            synthetic=True,
        )
        readable_span = span  # exercises _readable_span_to_dict via export()
    assert exporter.force_flush() is True
    assert write_stream.table_stream_name("events").endswith("/tables/events/streams/_default")
    if hasattr(readable_span, "get_span_context"):
        exporter.export([readable_span])
    bq.clear_table("events")
    bq.clear_table("state_transitions")
    bq.clear_table("journeys")
    exporter._journeys.clear()
    exporter._last_transition_time.clear()

    base_time = datetime(2026, 10, 16, 9, 0, 0, tzinfo=UTC)
    span_dicts = [
        {
            "event_id": "evt-j100-1",
            "trace_id": "tr-100",
            "span_id": "sp-100-1",
            "service": "svc-step1-node",
            "operation": "POST /internal/verify",
            "start_time": base_time,
            "end_time": base_time + timedelta(milliseconds=120),
            "attributes": {
                "journey.id": "jrn-100",
                "customer.id": "cust-100",
                "event_type": STEP_VERIFY,
                "entity": "item_application",
                "entity_id": "item-100",
                "status_from": "PENDING",
                "status_to": "VERIFIED",
                "http.status_code": 200,
                "synthetic": True,
            },
        },
        {
            "event_id": "evt-j100-2",
            "trace_id": "tr-100",
            "span_id": "sp-100-2",
            "service": "svc-step2-node",
            "operation": "POST /internal/evaluate",
            "start_time": base_time + timedelta(milliseconds=150),
            "end_time": base_time + timedelta(milliseconds=350),
            "attributes": {
                "journey.id": "jrn-100",
                "customer.id": "cust-100",
                "event_type": STEP_DECIDE,
                "entity": "item_application",
                "entity_id": "item-100",
                "decision": "APPROVED",
                "status_from": "VERIFIED",
                "status_to": "APPROVED",
                "http.status_code": 200,
                "synthetic": True,
                "rules_version": "9f1c2ab",
            },
        },
        {
            "event_id": "evt-j100-3",
            "trace_id": "tr-100",
            "span_id": "sp-100-3",
            "service": "svc-step3-node",
            "operation": "POST /internal/issue",
            "start_time": base_time + timedelta(milliseconds=380),
            "end_time": base_time + timedelta(milliseconds=500),
            "attributes": {
                "journey.id": "jrn-100",
                "customer.id": "cust-100",
                "event_type": STEP_POLICY,
                "entity": "item_application",
                "entity_id": "item-100",
                "status_from": "APPROVED",
                "status_to": "ISSUED",
                "http.status_code": 200,
                "synthetic": True,
            },
        },
        {
            "event_id": "evt-j100-4",
            "trace_id": "tr-100",
            "span_id": "sp-100-4",
            "service": "svc-step4-node",
            "operation": "POST /internal/settle",
            "start_time": base_time + timedelta(milliseconds=520),
            "end_time": base_time + timedelta(milliseconds=750),
            "attributes": {
                "journey.id": "jrn-100",
                "customer.id": "cust-100",
                "event_type": STEP_SETTLE,
                "entity": "item_application",
                "entity_id": "item-100",
                "status_from": "ISSUED",
                "status_to": "SETTLED",
                "http.status_code": 200,
                "synthetic": True,
            },
        },
    ]

    res = exporter.export_span_dicts(span_dicts)
    assert res.events_written == 4
    assert res.transitions_written == 4
    assert res.journeys_updated == 1

    events_table = bq.get_table("events")
    transitions_table = bq.get_table("state_transitions")
    journeys_table = bq.get_table("journeys")

    assert len(events_table) == 4
    assert len(transitions_table) == 4
    assert len(journeys_table) == 1

    jrn = journeys_table[0]
    assert jrn["journey_id"] == "jrn-100"
    assert jrn["final_status"] == "SETTLED"
    assert jrn["conformance"] == "CONFORMANT"
    assert jrn["total_duration_ms"] == 670

    # Deduplication test: re-exporting identical events must not duplicate rows
    res_dup = exporter.export_span_dicts(span_dicts)
    assert res_dup.events_written == 0
    assert len(bq.get_table("events")) == 4


@pytest.mark.unit
def test_journey_stitching_and_process_mining_conformance_classes() -> None:
    """TDD §2.2 & §4: Process mining DFG and all 5 conformance classifications."""
    now = datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC)

    # 1. CONFORMANT: canonical 4-step order
    c_conformant = classify_journey_conformance(
        [STEP_VERIFY, STEP_DECIDE, STEP_POLICY, STEP_SETTLE],
        final_status="SETTLED",
        started_at=now,
        last_step_at=now + timedelta(seconds=2),
        now=now + timedelta(seconds=5),
        intended_order=CANONICAL_ORDER,
    )
    assert c_conformant == "CONFORMANT"

    # 2. SKIPPED: step skipped before settlement
    c_skipped = classify_journey_conformance(
        [STEP_VERIFY, STEP_DECIDE, STEP_SETTLE],
        final_status="SETTLED",
        started_at=now,
        last_step_at=now + timedelta(seconds=2),
        now=now + timedelta(seconds=5),
        intended_order=CANONICAL_ORDER,
    )
    assert c_skipped == "SKIPPED"

    # 3. REORDERED: step before verification
    c_reordered = classify_journey_conformance(
        [STEP_POLICY, STEP_VERIFY, STEP_DECIDE, STEP_SETTLE],
        final_status="SETTLED",
        started_at=now,
        last_step_at=now + timedelta(seconds=2),
        now=now + timedelta(seconds=5),
        intended_order=CANONICAL_ORDER,
    )
    assert c_reordered == "REORDERED"

    # 4. LOOPED: repeated step in sequence
    c_looped = classify_journey_conformance(
        [STEP_VERIFY, STEP_DECIDE, STEP_VERIFY, STEP_DECIDE, STEP_POLICY, STEP_SETTLE],
        final_status="SETTLED",
        started_at=now,
        last_step_at=now + timedelta(seconds=3),
        now=now + timedelta(seconds=5),
        intended_order=CANONICAL_ORDER,
    )
    assert c_looped == "LOOPED"

    # 5. STALLED: stuck past stall_threshold_ms
    c_stalled = classify_journey_conformance(
        [STEP_VERIFY, STEP_DECIDE, STEP_POLICY],
        final_status="PROCESSING_PENDING",
        started_at=now,
        last_step_at=now + timedelta(seconds=1),
        now=now + timedelta(seconds=120),
        stall_threshold_ms=30_000,
        intended_order=CANONICAL_ORDER,
    )
    assert c_stalled == "STALLED"

    # In-flight within threshold is NOT marked STALLED
    c_in_flight = classify_journey_conformance(
        [STEP_VERIFY, STEP_DECIDE, STEP_POLICY],
        final_status="PROCESSING_PENDING",
        started_at=now,
        last_step_at=now + timedelta(seconds=1),
        now=now + timedelta(seconds=5),
        stall_threshold_ms=30_000,
        intended_order=CANONICAL_ORDER,
    )
    assert c_in_flight == "CONFORMANT"

    # Mine Directly-Follows Graph across multiple journeys
    miner = DirectlyFollowsMiner(intended_order=CANONICAL_ORDER)
    dfg = miner.mine_from_sequences(
        [
            ("j-1", [STEP_VERIFY, STEP_DECIDE, STEP_POLICY, STEP_SETTLE], "SETTLED", 800),
            ("j-2", [STEP_VERIFY, STEP_DECIDE, STEP_POLICY, STEP_SETTLE], "SETTLED", 950),
            ("j-3", [STEP_VERIFY, STEP_DECIDE, STEP_SETTLE], "SETTLED", 600),
            ("j-4", [STEP_POLICY, STEP_VERIFY, STEP_DECIDE, STEP_SETTLE], "SETTLED", 900),
        ]
    )
    assert dfg.edge_counts[(STEP_VERIFY, STEP_DECIDE)] == 4
    assert dfg.edge_counts[(STEP_DECIDE, STEP_POLICY)] == 2
    assert dfg.edge_counts[(STEP_DECIDE, STEP_SETTLE)] == 2
    assert dfg.edge_counts[(STEP_POLICY, STEP_VERIFY)] == 1
    assert dfg.conformance_counts["CONFORMANT"] == 2
    assert dfg.conformance_counts["SKIPPED"] == 1
    assert dfg.conformance_counts["REORDERED"] == 1


@pytest.mark.unit
def test_kpi_sql_and_changepoint_cusum_zscore_all_kpis() -> None:
    """TDD §2.2 & Decision A8: KPI SQLs parse with sqlglot; CUSUM & z-score detect breaches against 7d same-hour baseline."""
    assert set(KPI_SQL_TEMPLATES.keys()) == set(ALL_KPIS)

    for kpi_name in ALL_KPIS:
        sql = build_kpi_rollup_sql(kpi_name, dataset="godview_ledger", baseline_days=7)
        parsed = sqlglot.parse_one(sql, read="bigquery")
        assert isinstance(parsed, exp.Expression), f"KPI SQL for {kpi_name} failed sqlglot parse"
        assert "INTERVAL 7 DAY" in sql
        assert "probe" in sql

    bq = InMemoryBigQuery()
    engine = KpiEngine(bigquery=bq, env_id="env-test-01", journey_name="primary_flow")

    eval_window_start = datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC)
    eval_window_end = eval_window_start + timedelta(minutes=5)

    # Seed historical events across the full hour (12 * 5-minute blocks)
    historical_rows = []
    for day_offset in range(1, 8):
        base_d = eval_window_start - timedelta(days=day_offset)
        for m in range(0, 60, 5):
            for i in range(20):
                j_id = f"hist-d{day_offset}-m{m}-j{i}"
                historical_rows.append(
                    {
                        "event_id": f"evt-hist-{day_offset}-{m}-{i}",
                        "event_time": base_d + timedelta(minutes=m, seconds=i * 5),
                        "env_id": "env-test-01",
                        "journey_id": j_id,
                        "service": "svc-step1",
                        "operation": "execute",
                        "event_type": "step.executed",
                        "state_to": "COMPLETED",
                        "duration_ms": 150 + (i % 3) * 10,
                        "source": "otel",
                    }
                )
    bq.insert_rows("events", historical_rows)

    # Normal window -> no breaches
    normal_rows = []
    for i in range(20):
        normal_rows.append(
            {
                "event_id": f"evt-norm-{i}",
                "event_time": eval_window_start + timedelta(seconds=10 + i),
                "env_id": "env-test-01",
                "journey_id": f"curr-norm-{i}",
                "service": "svc-step1",
                "operation": "execute",
                "event_type": "step.executed",
                "state_to": "COMPLETED",
                "duration_ms": 155,
                "source": "otel",
            }
        )
    bq.insert_rows("events", normal_rows)

    normal_rollups = {
        r.kpi: r
        for r in engine.evaluate_window(
            window_start=eval_window_start,
            window_end=eval_window_end,
        )
    }
    assert set(normal_rollups.keys()) == set(ALL_KPIS)
    for kpi_name, row in normal_rollups.items():
        assert row.breach is False, f"Expected no breach for {kpi_name}"

    # Anomaly window: high latency spike -> latency_mean_ms breach
    anom_start = eval_window_start + timedelta(minutes=10)
    anom_end = anom_start + timedelta(minutes=5)
    anom_rows = []
    for i in range(20):
        anom_rows.append(
            {
                "event_id": f"evt-anom-{i}",
                "event_time": anom_start + timedelta(seconds=10 + i),
                "env_id": "env-test-01",
                "journey_id": f"curr-anom-{i}",
                "service": "svc-step1",
                "operation": "execute",
                "event_type": "step.executed",
                "state_to": "FAILED",
                "duration_ms": 8500,  # massive latency spike
                "source": "otel",
            }
        )
    bq.insert_rows("events", anom_rows)

    anom_rollups = {
        r.kpi: r
        for r in engine.evaluate_window(
            window_start=anom_start,
            window_end=anom_end,
        )
    }
    assert anom_rollups["latency_mean_ms"].breach is True
    assert anom_rollups["completion_rate"].breach is True


@pytest.mark.unit
@pytest.mark.contract
def test_ledger_exporter_row_keys_subset_of_bigquery_schemas() -> None:
    """Ensure every row emitted by LedgerSpanExporter is a subset of the BigQuery table schemas (no streaming 500 drift)."""
    repo_root = Path(__file__).resolve().parents[3]
    ledger_schema_dir = repo_root / "godview" / "ledger" / "schemas"

    schema_columns: dict[str, set[str]] = {}
    for table in ("events", "state_transitions", "journeys"):
        schema_file = ledger_schema_dir / f"{table}.json"
        assert schema_file.is_file(), f"Missing schema file {schema_file}"
        fields = json.loads(schema_file.read_text(encoding="utf-8"))
        schema_columns[table] = {f["name"] for f in fields}

    # Verify parent_span_id is in events schema
    assert "parent_span_id" in schema_columns["events"]

    bq = InMemoryBigQuery()
    write_stream = InMemoryStorageWriteStream(bq)
    exporter = LedgerSpanExporter(
        bigquery=bq,
        env_id="env-drift-test",
        default_journey_name="drift_check_flow",
        write_stream=write_stream,
    )

    base_time = datetime(2026, 10, 18, 8, 0, 0, tzinfo=UTC)
    test_spans = [
        {
            "event_id": "evt-drift-1",
            "trace_id": "tr-drift-1",
            "span_id": "sp-drift-1",
            "parent_span_id": "sp-parent-0",
            "service": "svc-drift-node",
            "operation": "POST /items",
            "start_time": base_time,
            "end_time": base_time + timedelta(milliseconds=50),
            "attributes": {
                "journey.id": "jrn-drift-1",
                "customer.id": "cust-drift-1",
                "event_type": "item.created",
                "entity": "item",
                "entity_id": "it-001",
                "status_from": "INIT",
                "status_to": "ACTIVE",
                "http.status_code": 201,
                "synthetic": False,
                "custom_key": "custom_val",
            },
        }
    ]

    res = exporter.export_span_dicts(test_spans)
    assert res.events_written >= 1
    assert res.transitions_written >= 1
    assert res.journeys_updated >= 1

    # Assert row keys in events ⊆ events.json columns
    for row in bq.get_table("events"):
        extra_keys = set(row.keys()) - schema_columns["events"]
        assert not extra_keys, f"events table row has keys not in schema: {extra_keys}"

    # Assert row keys in state_transitions ⊆ state_transitions.json columns
    for row in bq.get_table("state_transitions"):
        extra_keys = set(row.keys()) - schema_columns["state_transitions"]
        assert not extra_keys, f"state_transitions table row has keys not in schema: {extra_keys}"

    # Assert row keys in journeys ⊆ journeys.json columns
    for row in bq.get_table("journeys"):
        extra_keys = set(row.keys()) - schema_columns["journeys"]
        assert not extra_keys, f"journeys table row has keys not in schema: {extra_keys}"

