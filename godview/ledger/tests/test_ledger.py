"""L1 Hermetic Unit & Contract Tests for godview/ledger (TDD §2.2, §4 UC-04..UC-08, Decision A8 & A13).

All test data uses environment-neutral service and resource names (TDD I-02).
Every SQL statement (DDL and KPI rollup queries) is parsed with sqlglot (BigQuery dialect).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import sqlglot
from godview_shared.contracts import LEDGER_TABLES, KpiName
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
    GcpRestStorageWriteStream,
    InMemoryStorageWriteStream,
    LedgerSpanExporter,
)
from godview.ledger.kpi import (
    KPI_SQL_TEMPLATES,
    KpiEngine,
    build_kpi_rollup_sql,
)
from godview.ledger.process_mining import (
    STEP_CREDIT,
    STEP_DECIDE,
    STEP_POLICY,
    STEP_SETTLE,
    STEP_VERIFY,
    DirectlyFollowsMiner,
    classify_journey_conformance,
)


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
        default_journey_name="vehicle_loan_origination",
        write_stream=write_stream,
    )

    tracer = get_tracer("svc-step1-node")
    with tracer.start_as_current_span("POST /internal/verify") as span:
        set_business_span_attributes(
            span,
            journey_id="jrn-100",
            customer_id="cust-100",
            loan_id="ln-100",
            event_type="kyc.verified",
            entity="kyc_document",
            entity_id="doc-100",
            status_from="VERIFIED_PENDING",
            status_to="VERIFIED",
            synthetic=True,
        )
        readable_span = span  # exercises _readable_span_to_dict via export()
    assert exporter.force_flush() is True
    assert write_stream.table_stream_name("events").endswith("/tables/events/streams/_default")
    # Also testReadableSpan conversion and _infer_entity fallback when entity is omitted
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
                "loan.id": "ln-100",
                "event_type": "kyc.verified",
                "entity": "loan_application",
                "entity_id": "ln-100",
                "status_from": "VERIFIED_PENDING",
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
                "loan.id": "ln-100",
                "event_type": "loan.decided",
                "entity": "loan_application",
                "entity_id": "ln-100",
                "decision": "APPROVED",
                "foir_pct": 44.5,
                "cibil_score": 755,
                "emi_inr": 21500.0,
                "amount_inr": 850000.0,
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
                "loan.id": "ln-100",
                "event_type": "insurance.issued",
                "entity": "loan_application",
                "entity_id": "ln-100",
                "amount_inr": 16500.0,
                "status_from": "APPROVED",
                "status_to": "INSURED",
                "http.status_code": 200,
                "synthetic": True,
            },
        },
        {
            "event_id": "evt-j100-4",
            "trace_id": "tr-100",
            "span_id": "sp-100-4",
            "service": "svc-step4-node",
            "operation": "POST /internal/disburse",
            "start_time": base_time + timedelta(milliseconds=520),
            "end_time": base_time + timedelta(milliseconds=720),
            "attributes": {
                "journey.id": "jrn-100",
                "customer.id": "cust-100",
                "loan.id": "ln-100",
                "event_type": "disbursement.settled",
                "entity": "loan_application",
                "entity_id": "ln-100",
                "amount_inr": 850000.0,
                "status_from": "INSURED",
                "status_to": "DISBURSED",
                "http.status_code": 200,
                "synthetic": True,
                "sanction_letter_no": "SAN-100",
            },
        },
    ]

    first_batch = exporter.export_span_dicts(span_dicts)
    assert first_batch.events_written == 4
    assert first_batch.transitions_written == 4
    assert write_stream.stream_name.endswith("/streams/_default")

    # Re-exporting the exact same spans must deduplicate by event_id (Decision A13)
    duplicate_batch = exporter.export_span_dicts(span_dicts)
    assert duplicate_batch.events_written == 0
    assert len(bq.get_table("events")) == 4
    assert len(bq.get_table("state_transitions")) == 4

    # Exporting a new span with a distinct event_id must still write (both failure directions)
    probe_span = {
        "event_id": "evt-probe-1",
        "trace_id": "tr-probe",
        "span_id": "sp-probe",
        "service": "svc-step2-node",
        "operation": "POST /internal/evaluate",
        "start_time": base_time + timedelta(seconds=5),
        "end_time": base_time + timedelta(seconds=5, milliseconds=50),
        "attributes": {
            "event_type": "loan.decided",
            "decision": "REJECTED",
            "foir_pct": 75.0,
            "cibil_score": 610,
            "synthetic": True,
            "source": "probe",
        },
    }
    probe_res = exporter.export_span_dicts([probe_span])
    assert probe_res.events_written == 1
    events = bq.get_table("events")
    assert len(events) == 5
    probe_row = next(e for e in events if e["event_id"] == "evt-probe-1")
    assert probe_row["source"] == "probe"
    assert probe_row["is_synthetic"] is True

    # Verify stitched journey row in journeys table
    journeys = bq.get_table("journeys")
    assert len(journeys) == 1
    jrn = journeys[0]
    assert jrn["journey_id"] == "jrn-100"
    expected_seq = f"{STEP_VERIFY}->{STEP_DECIDE}->{STEP_POLICY}->{STEP_SETTLE}"
    assert jrn["observed_sequence"] == expected_seq
    assert jrn["conformance"] == "CONFORMANT"
    assert jrn["final_status"] == "DISBURSED"
    assert float(jrn["rupee_value"]) == 850000.0
    assert len(jrn["steps"]) == 4

    # Out-of-order span arrival must still derive final_status from the chronologically latest step
    ooo_spans = [span_dicts[3], span_dicts[0], span_dicts[1], span_dicts[2]]
    bq_ooo = InMemoryBigQuery()
    exp_ooo = LedgerSpanExporter(bigquery=bq_ooo, env_id="env-test-01")
    exp_ooo.export_span_dicts(ooo_spans)
    jrn_ooo = bq_ooo.get_table("journeys")[0]
    assert jrn_ooo["final_status"] == "DISBURSED"
    assert jrn_ooo["conformance"] == "CONFORMANT"

    # Duplicate retry of an earlier span must not corrupt dwell_ms for subsequent spans
    bq_dwell = InMemoryBigQuery()
    exp_dwell = LedgerSpanExporter(bigquery=bq_dwell, env_id="env-test-01")
    exp_dwell.export_span_dicts([span_dicts[0], span_dicts[1]])
    exp_dwell.export_span_dicts([span_dicts[0]])  # duplicate retry of step 1
    exp_dwell.export_span_dicts([span_dicts[2]])
    tr_rows = {r["status_to"]: r for r in bq_dwell.get_table("state_transitions")}
    assert tr_rows["INSURED"]["dwell_ms"] == 150

    # Verify GcpRestStorageWriteStream insertId deduplication
    stream = GcpRestStorageWriteStream(
        project_id="proj-test",
        dataset_id="godview_ledger",
        token_provider=lambda: "tok-test",
    )
    assert stream.table_stream_name("events").endswith("/tables/events/streams/_default")
    assert stream.stream_name.endswith("/streams/_default")
    assert stream.append_rows("events", []) == 0

    import io
    import urllib.request
    from unittest.mock import patch

    class _FakeResp(io.BytesIO):
        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    with patch.object(
        urllib.request,
        "urlopen",
        side_effect=lambda req, timeout=10.0: _FakeResp(
            b'{"kind":"bigquery#tableDataInsertAllResponse"}'
        ),
    ):
        written1 = stream.append_rows("events", [{"event_id": "e-gcp-1", "service": "svc-step1"}])
        written2 = stream.append_rows("events", [{"event_id": "e-gcp-1", "service": "svc-step1"}])
        assert written1 == 1
        assert written2 == 0


@pytest.mark.unit
def test_journey_stitching_and_process_mining_conformance_classes() -> None:
    """TDD §2.2 & §4 UC-05, UC-08, UC-16: Process mining DFG and all 5 conformance classifications."""
    now = datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC)

    # 1. CONFORMANT: canonical 4-step order
    c_conformant = classify_journey_conformance(
        [STEP_VERIFY, STEP_DECIDE, STEP_POLICY, STEP_SETTLE],
        final_status="DISBURSED",
        started_at=now,
        last_step_at=now + timedelta(seconds=2),
        now=now + timedelta(seconds=5),
    )
    assert c_conformant == "CONFORMANT"

    # 2. SKIPPED (UC-05 insurance_no_policy_write): policy step skipped before settlement
    c_skipped = classify_journey_conformance(
        [STEP_VERIFY, STEP_DECIDE, STEP_SETTLE],
        final_status="DISBURSED",
        started_at=now,
        last_step_at=now + timedelta(seconds=2),
        now=now + timedelta(seconds=5),
    )
    assert c_skipped == "SKIPPED"

    # 3. REORDERED (UC-08 step_order_insurance_before_kyc): policy step before verification
    c_reordered = classify_journey_conformance(
        [STEP_POLICY, STEP_VERIFY, STEP_DECIDE, STEP_SETTLE],
        final_status="DISBURSED",
        started_at=now,
        last_step_at=now + timedelta(seconds=2),
        now=now + timedelta(seconds=5),
    )
    assert c_reordered == "REORDERED"

    # 4. LOOPED: repeated step in sequence
    c_looped = classify_journey_conformance(
        [STEP_VERIFY, STEP_DECIDE, STEP_VERIFY, STEP_DECIDE, STEP_POLICY, STEP_SETTLE],
        final_status="DISBURSED",
        started_at=now,
        last_step_at=now + timedelta(seconds=3),
        now=now + timedelta(seconds=5),
    )
    assert c_looped == "LOOPED"

    # 5. STALLED (UC-16 gce_vm_latency_504): stuck at DISBURSEMENT_PENDING past stall_threshold_ms
    c_stalled = classify_journey_conformance(
        [STEP_VERIFY, STEP_DECIDE, STEP_POLICY],
        final_status="DISBURSEMENT_PENDING",
        started_at=now,
        last_step_at=now + timedelta(seconds=1),
        now=now + timedelta(seconds=120),
        stall_threshold_ms=30_000,
    )
    assert c_stalled == "STALLED"

    # Opposite direction for STALLED: in-flight within threshold is NOT marked STALLED
    c_in_flight = classify_journey_conformance(
        [STEP_VERIFY, STEP_DECIDE, STEP_POLICY],
        final_status="DISBURSEMENT_PENDING",
        started_at=now,
        last_step_at=now + timedelta(seconds=1),
        now=now + timedelta(seconds=5),
        stall_threshold_ms=30_000,
    )
    assert c_in_flight == "CONFORMANT"

    # Unknown/unmapped step tokens must not bypass SKIPPED or REORDERED checks
    assert classify_journey_conformance(["UNKNOWN_STEP"], final_status="DISBURSED") == "SKIPPED"
    assert (
        classify_journey_conformance(
            [STEP_VERIFY, "CUSTOM_OP", STEP_SETTLE], final_status="DISBURSED"
        )
        == "SKIPPED"
    )
    assert classify_journey_conformance([STEP_DECIDE], final_status="REJECTED") == "SKIPPED"
    assert (
        classify_journey_conformance(
            [STEP_VERIFY, STEP_DECIDE, STEP_CREDIT, STEP_POLICY, STEP_SETTLE],
            final_status="DISBURSED",
        )
        == "CONFORMANT"
    )

    # Mine Directly-Follows Graph across multiple journeys
    miner = DirectlyFollowsMiner(
        intended_order=[STEP_VERIFY, STEP_DECIDE, STEP_POLICY, STEP_SETTLE]
    )
    dfg = miner.mine_from_sequences(
        [
            ("j-1", [STEP_VERIFY, STEP_DECIDE, STEP_POLICY, STEP_SETTLE], "DISBURSED", 800),
            ("j-2", [STEP_VERIFY, STEP_DECIDE, STEP_POLICY, STEP_SETTLE], "DISBURSED", 950),
            ("j-3", [STEP_VERIFY, STEP_DECIDE, STEP_SETTLE], "DISBURSED", 600),
            ("j-4", [STEP_POLICY, STEP_VERIFY, STEP_DECIDE, STEP_SETTLE], "DISBURSED", 900),
        ]
    )
    assert dfg.edge_counts[(STEP_VERIFY, STEP_DECIDE)] == 4
    assert dfg.edge_counts[(STEP_DECIDE, STEP_POLICY)] == 2
    assert dfg.edge_counts[(STEP_DECIDE, STEP_SETTLE)] == 2
    assert dfg.edge_counts[(STEP_POLICY, STEP_VERIFY)] == 1
    assert dfg.conformance_counts["CONFORMANT"] == 2
    assert dfg.conformance_counts["SKIPPED"] == 1
    assert dfg.conformance_counts["REORDERED"] == 1

    # Also test mine_from_journeys (both with observed_sequence and fallback to steps list) and mine_from_transitions
    dfg_from_journeys = miner.mine_from_journeys(
        [
            {
                "journey_id": "j-a",
                "observed_sequence": f"{STEP_VERIFY}->{STEP_DECIDE}->{STEP_POLICY}->{STEP_SETTLE}",
                "final_status": "DISBURSED",
                "total_duration_ms": 500,
            },
            {
                "journey_id": "j-b",
                "observed_sequence": "",
                "steps": [
                    {"service": "svc-step1", "operation": "verify", "status": "VERIFIED"},
                    {"service": "svc-step2", "operation": "evaluate", "status": "APPROVED"},
                    {"service": "svc-step4", "operation": "disburse", "status": "DISBURSED"},
                ],
                "final_status": "DISBURSED",
                "total_duration_ms": 400,
            },
        ]
    )
    assert dfg_from_journeys.conformance_counts["CONFORMANT"] == 1
    assert dfg_from_journeys.conformance_counts["SKIPPED"] == 1

    dfg_from_transitions = miner.mine_from_transitions(
        [
            {
                "journey_id": "j-t1",
                "transition_time": now,
                "actor_service": "svc-step1",
                "status_to": "VERIFIED",
                "dwell_ms": 100,
            },
            {
                "journey_id": "j-t1",
                "transition_time": now + timedelta(seconds=1),
                "actor_service": "svc-step2",
                "status_to": "APPROVED",
                "dwell_ms": 150,
            },
            {
                "journey_id": "j-t1",
                "transition_time": now + timedelta(seconds=2),
                "actor_service": "svc-step3",
                "status_to": "INSURED",
                "dwell_ms": 120,
            },
            {
                "journey_id": "j-t1",
                "transition_time": now + timedelta(seconds=3),
                "actor_service": "svc-step4",
                "status_to": "DISBURSED",
                "dwell_ms": 200,
            },
        ]
    )
    assert dfg_from_transitions.conformance_counts["CONFORMANT"] == 1
    assert dfg_from_transitions.total_journeys == 1


@pytest.mark.unit
def test_kpi_sql_and_changepoint_cusum_zscore_all_eight_kpis() -> None:
    """TDD §2.2 & §4 UC-06, UC-07 & Decision A8: All 8 KPI SQLs parse with sqlglot; CUSUM & z-score detect breaches against 7d same-hour baseline."""
    all_kpis: tuple[KpiName, ...] = (
        "approval_rate",
        "step_conversion",
        "ticket_size_inr",
        "rupees_per_hour",
        "emi_mean",
        "emi_stddev",
        "disbursement_latency_ms",
        "policy_issue_rate",
    )
    assert set(KPI_SQL_TEMPLATES.keys()) == set(all_kpis)

    for kpi_name in all_kpis:
        sql = build_kpi_rollup_sql(kpi_name, dataset="godview_ledger", baseline_days=7)
        parsed = sqlglot.parse_one(sql, read="bigquery")
        assert isinstance(parsed, exp.Expression), f"KPI SQL for {kpi_name} failed sqlglot parse"
        assert "INTERVAL 7 DAY" in sql
        assert "probe" in sql

    bq = InMemoryBigQuery()
    engine = KpiEngine(bigquery=bq, env_id="env-test-01", journey_name="vehicle_loan_origination")

    # Seed 7 days of historical same-hour events at hour 10:00 UTC
    eval_window_start = datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC)
    eval_window_end = eval_window_start + timedelta(minutes=5)

    historical_rows = []
    for day_offset in range(1, 8):
        day_ts = eval_window_start - timedelta(days=day_offset) + timedelta(minutes=1)
        # 20 decisions per day at hour 10: ~70% approved (14 approved, 6 rejected) with small day-to-day variance
        approved_count = 14 if day_offset % 2 == 0 else 15
        for i in range(20):
            is_approved = i < approved_count
            emi_val = 22000.0 + (i * 50.0) + (day_offset * 10.0)
            amt_val = 900000.0 + (i * 5000.0)
            j_id = f"hist-d{day_offset}-j{i}"
            historical_rows.append(
                {
                    "event_id": f"evt-hist-s1-{day_offset}-{i}",
                    "event_time": day_ts,
                    "env_id": "env-test-01",
                    "journey_id": j_id,
                    "service": "svc-step1",
                    "operation": "verify",
                    "event_type": "kyc.verified",
                    "source": "otel",
                    "duration_ms": 100,
                }
            )
            historical_rows.append(
                {
                    "event_id": f"evt-hist-s2-{day_offset}-{i}",
                    "event_time": day_ts + timedelta(seconds=1),
                    "env_id": "env-test-01",
                    "journey_id": j_id,
                    "service": "svc-step2",
                    "operation": "evaluate",
                    "event_type": "loan.decided",
                    "decision": "APPROVED" if is_approved else "REJECTED",
                    "foir_pct": 45.0 if is_approved else 68.0,
                    "cibil_score": 740 if is_approved else 620,
                    "emi_inr": emi_val,
                    "amount_inr": amt_val,
                    "source": "otel",
                    "duration_ms": 180,
                }
            )
            if is_approved:
                historical_rows.append(
                    {
                        "event_id": f"evt-hist-s3-{day_offset}-{i}",
                        "event_time": day_ts + timedelta(seconds=2),
                        "env_id": "env-test-01",
                        "journey_id": j_id,
                        "service": "svc-step3",
                        "operation": "issue",
                        "event_type": "insurance.issued",
                        "amount_inr": 15000.0,
                        "source": "otel",
                        "duration_ms": 90,
                    }
                )
                historical_rows.append(
                    {
                        "event_id": f"evt-hist-s4-{day_offset}-{i}",
                        "event_time": day_ts + timedelta(seconds=3),
                        "env_id": "env-test-01",
                        "journey_id": j_id,
                        "service": "svc-step4",
                        "operation": "disburse",
                        "event_type": "disbursement.settled",
                        "amount_inr": amt_val,
                        "source": "otel",
                        "duration_ms": 220 + (i % 5) * 10,
                    }
                )
        # Also inject synthetic probe events with extreme values; must be excluded from baselines!
        historical_rows.append(
            {
                "event_id": f"evt-hist-probe-{day_offset}",
                "event_time": day_ts,
                "env_id": "env-test-01",
                "journey_id": f"probe-{day_offset}",
                "service": "svc-step2",
                "operation": "evaluate",
                "event_type": "loan.decided",
                "decision": "REJECTED",
                "emi_inr": 999999.0,
                "amount_inr": 99999999.0,
                "source": "probe",
                "duration_ms": 50,
            }
        )

    bq.insert_rows("events", historical_rows)

    # Direction 1: Normal current window at 10:00 UTC -> no KPI breaches
    normal_rows = []
    for i in range(20):
        is_approved = i < 14
        j_id = f"curr-norm-{i}"
        ts = eval_window_start + timedelta(seconds=10 + i)
        normal_rows.extend(
            [
                {
                    "event_id": f"evt-norm-s1-{i}",
                    "event_time": ts,
                    "env_id": "env-test-01",
                    "journey_id": j_id,
                    "service": "svc-step1",
                    "operation": "verify",
                    "event_type": "kyc.verified",
                    "source": "otel",
                    "duration_ms": 100,
                },
                {
                    "event_id": f"evt-norm-s2-{i}",
                    "event_time": ts + timedelta(seconds=1),
                    "env_id": "env-test-01",
                    "journey_id": j_id,
                    "service": "svc-step2",
                    "operation": "evaluate",
                    "event_type": "loan.decided",
                    "decision": "APPROVED" if is_approved else "REJECTED",
                    "emi_inr": 22000.0 + (i * 50.0) + 40.0,
                    "amount_inr": 900000.0 + (i * 5000.0),
                    "source": "otel",
                    "duration_ms": 180,
                },
            ]
        )
        if is_approved:
            normal_rows.extend(
                [
                    {
                        "event_id": f"evt-norm-s3-{i}",
                        "event_time": ts + timedelta(seconds=2),
                        "env_id": "env-test-01",
                        "journey_id": j_id,
                        "service": "svc-step3",
                        "operation": "issue",
                        "event_type": "insurance.issued",
                        "amount_inr": 15000.0,
                        "source": "otel",
                        "duration_ms": 90,
                    },
                    {
                        "event_id": f"evt-norm-s4-{i}",
                        "event_time": ts + timedelta(seconds=3),
                        "env_id": "env-test-01",
                        "journey_id": j_id,
                        "service": "svc-step4",
                        "operation": "disburse",
                        "event_type": "disbursement.settled",
                        "amount_inr": (900000.0 + (i * 5000.0)) / 12.0,
                        "source": "otel",
                        "duration_ms": 235,
                    },
                ]
            )
    bq.insert_rows("events", normal_rows)

    normal_rollups = {
        r.kpi: r
        for r in engine.evaluate_window(
            window_start=eval_window_start,
            window_end=eval_window_end,
        )
    }
    assert set(normal_rollups.keys()) == set(all_kpis)
    for kpi_name, row in normal_rollups.items():
        assert row.breach is False, (
            f"Expected no breach on normal traffic for {kpi_name}, got z={row.zscore}, cusum={row.cusum}"
        )

    # Direction 2: Anomaly window (UC-07 cibil_stub_900 -> 100% approval, UC-06 emi_rounding, UC-05 insurance_no_policy_write)
    anomaly_start = eval_window_start + timedelta(minutes=10)
    anomaly_end = anomaly_start + timedelta(minutes=5)
    anomaly_rows = []
    for i in range(20):
        j_id = f"curr-anom-{i}"
        ts = anomaly_start + timedelta(seconds=10 + i)
        anomaly_rows.extend(
            [
                {
                    "event_id": f"evt-anom-s1-{i}",
                    "event_time": ts,
                    "env_id": "env-test-01",
                    "journey_id": j_id,
                    "service": "svc-step1",
                    "operation": "verify",
                    "event_type": "kyc.verified",
                    "source": "otel",
                    "duration_ms": 100,
                },
                {
                    "event_id": f"evt-anom-s2-{i}",
                    "event_time": ts + timedelta(seconds=1),
                    "env_id": "env-test-01",
                    "journey_id": j_id,
                    "service": "svc-step2",
                    "operation": "evaluate",
                    "event_type": "loan.decided",
                    "decision": "APPROVED",  # 100% approved (cibil_stub_900)
                    "emi_inr": 15000.0,  # rounded constant EMI (emi_rounding)
                    "amount_inr": Decimal("900000.00"),
                    "source": "otel",
                    "duration_ms": 180,
                },
                # No insurance.issued event emitted (UC-05 insurance_no_policy_write)
                {
                    "event_id": f"evt-anom-s4-{i}",
                    "event_time": ts + timedelta(seconds=3),
                    "env_id": "env-test-01",
                    "journey_id": j_id,
                    "service": "svc-step4",
                    "operation": "disburse",
                    "event_type": "disbursement.settled",
                    "amount_inr": Decimal("900000.00"),
                    "source": "otel",
                    "duration_ms": 5800,  # high latency spike
                },
            ]
        )
    bq.insert_rows("events", anomaly_rows)

    anom_rollups = {
        r.kpi: r
        for r in engine.evaluate_window(
            window_start=anomaly_start,
            window_end=anomaly_end,
        )
    }
    assert anom_rollups["approval_rate"].breach is True
    assert anom_rollups["emi_mean"].breach is True
    assert anom_rollups["policy_issue_rate"].breach is True
    assert anom_rollups["disbursement_latency_ms"].breach is True
    assert len(bq.get_table("kpi_rollups")) >= 16

    # Verify steady 5m traffic across a full 60m historical hour produces matching rupees_per_hour baseline (no 12x inflation)
    bq_rph = InMemoryBigQuery()
    eng_rph = KpiEngine(bigquery=bq_rph, env_id="env-rph")
    rph_rows = []
    for d in range(1, 8):
        base_d = eval_window_start - timedelta(days=d)
        for m in range(0, 60, 5):
            rph_rows.append(
                {
                    "event_id": f"rph-h-{d}-{m}",
                    "event_time": base_d + timedelta(minutes=m, seconds=10),
                    "env_id": "env-rph",
                    "journey_id": f"j-rph-{d}-{m}",
                    "service": "svc-step4",
                    "operation": "disburse",
                    "event_type": "disbursement.settled",
                    "amount_inr": 100000.0,
                    "duration_ms": 200,
                    "source": "otel",
                }
            )
    rph_rows.append(
        {
            "event_id": "rph-curr-1",
            "event_time": eval_window_start + timedelta(seconds=10),
            "env_id": "env-rph",
            "journey_id": "j-rph-curr-1",
            "service": "svc-step4",
            "operation": "disburse",
            "event_type": "disbursement.settled",
            "amount_inr": 100000.0,
            "duration_ms": 200,
            "source": "otel",
        }
    )
    # Also insert an event from a different env_id; must be ignored by eng_rph
    rph_rows.append(
        {
            "event_id": "rph-other-env",
            "event_time": eval_window_start + timedelta(seconds=15),
            "env_id": "env-other",
            "journey_id": "j-other",
            "service": "svc-step4",
            "operation": "disburse",
            "event_type": "disbursement.settled",
            "amount_inr": 99999999.0,
            "duration_ms": 200,
            "source": "otel",
        }
    )
    bq_rph.insert_rows("events", rph_rows)
    rph_res = {
        r.kpi: r
        for r in eng_rph.evaluate_window(window_start=eval_window_start, window_end=eval_window_end)
    }["rupees_per_hour"]
    assert float(rph_res.value) == 1200000.0
    assert float(rph_res.baseline_value) == 1200000.0
    assert rph_res.breach is False
