"""L1 Hermetic Unit & Contract Tests for godview/invariants and procedural skills
(TDD §2.4, §4, Agent Spec §7 & §8, Design §6.2, Decision A8).

All test data in this file uses environment-neutral names (TDD I-02).
Every SQL invariant statement is parsed with sqlglot (BigQuery / Postgres dialect).
"""

from __future__ import annotations

import io
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlglot
from godview_shared.contracts import InvariantDoc, ProceduralSkillDoc, SentinelDefinition
from godview_shared.fakes import InMemoryBigQuery
from sqlglot import exp

from godview.invariants.compiler import (
    InvariantCompiler,
    InvariantLifecycleError,
    InvariantLifecycleManager,
)
from godview.invariants.loader import (
    InvariantValidationError,
    load_invariant_file,
    parse_invariant_markdown,
    validate_check_statement_syntax,
)
from godview.invariants.skills import (
    GcsSkillsStorageClient,
    InMemorySkillsStorageClient,
    SkillLoader,
    SkillValidationError,
    default_skills_bucket_uri,
)


@pytest.mark.unit
@pytest.mark.contract
def test_invariant_load_validate_and_parse_with_sqlglot(tmp_path: Path) -> None:
    """Design §6.2 & Agent Spec §7: Invariants validate, parse with sqlglot, and compile to SentinelDefinition."""
    raw_md = """---
name: step-latency-threshold
scope: {service: svc-processor, entity: task_item, journey: processing_pipeline}
provenance: policy
provenance_ref: "POL-001"
check:
  dialect: bigquery
  window_s: 300
  statement: "SELECT COUNT(*) AS violations FROM `godview_ledger.events` WHERE duration_ms > 5000"
severity: sev1
wake_condition: {operator: ">=", threshold: 1, consecutive: 2, target_agent_id: application__svc-processor}
confidence: 0.95
created_by: human_curator
version: "1.0.0"
status: enforced
---
# Processing Latency Invariant
Enforces that task processing duration remains within acceptable limits.
"""
    inv_file = tmp_path / "INVARIANT.md"
    inv_file.write_text(raw_md, encoding="utf-8")

    doc = load_invariant_file(inv_file)
    assert isinstance(doc, InvariantDoc)
    assert doc.name == "step-latency-threshold"
    assert doc.doc_id == f"inv-{doc.name}"
    assert doc.scope.journey == "processing_pipeline"
    assert doc.body_md.strip().startswith("#")

    # Validate SQL syntax with sqlglot (BigQuery dialect)
    parsed = sqlglot.parse_one(doc.check.statement, read="bigquery")
    assert isinstance(parsed, exp.Select), f"Invariant {doc.name} did not parse as SELECT"

    # Compile to SentinelDefinition
    compiler = InvariantCompiler()
    sentinel = compiler.compile(doc)
    assert isinstance(sentinel, SentinelDefinition)
    assert sentinel.sentinel_id == f"snt-invariant_sql-{doc.name}"
    assert sentinel.sentinel_class == "invariant_sql"
    assert sentinel.source_ref == f"inv-{doc.name}"
    assert sentinel.cadence_s == 10
    assert sentinel.baseline.model == "same_hour"
    assert sentinel.baseline.window_days == 7
    assert sentinel.state == "enforced"


@pytest.mark.unit
def test_invariant_compiler_dialects_bigquery_postgres_python() -> None:
    """Test evaluating compiled invariants across bigquery, postgres, and python dialects."""
    bq = InMemoryBigQuery()
    compiler = InvariantCompiler(bigquery=bq)
    now = datetime.now(tz=UTC)

    # 1. BigQuery dialect invariant
    bq_inv = parse_invariant_markdown(
        """---
name: failure-rate-check
scope: {service: svc-worker, entity: job, journey: batch_run}
provenance: code
provenance_ref: "CODE-001"
check:
  dialect: bigquery
  window_s: 300
  statement: "SELECT COUNT(*) AS violations FROM `godview_ledger.events` WHERE http_status >= 500"
severity: sev2
wake_condition: {operator: ">=", threshold: 1, consecutive: 1, target_agent_id: application__svc-worker}
confidence: 0.9
created_by: human_curator
version: "1.0.0"
status: enforced
---
# Check failure rates
"""
    )
    snt_bq = compiler.compile(bq_inv)

    # Clean run
    bq.insert_rows(
        "events",
        [
            {
                "event_id": "evt-ok-1",
                "event_time": now - timedelta(seconds=10),
                "env_id": "env-01",
                "service": "svc-worker",
                "http_status": 200,
                "duration_ms": 100,
                "source": "otel",
            }
        ],
    )
    res_clean = compiler.evaluate_sentinel(snt_bq)
    assert res_clean["violations"] == 0

    # Breach run
    bq.insert_rows(
        "events",
        [
            {
                "event_id": "evt-fail-1",
                "event_time": now - timedelta(seconds=5),
                "env_id": "env-01",
                "service": "svc-worker",
                "http_status": 500,
                "duration_ms": 120,
                "source": "otel",
            }
        ],
    )
    res_breach = compiler.evaluate_sentinel(snt_bq)
    assert res_breach["violations"] == 1

    # 2. Python dialect invariant
    py_inv = parse_invariant_markdown(
        """---
name: duration-python-check
scope: {service: svc-worker, entity: job, journey: batch_run}
provenance: policy
provenance_ref: "POL-002"
check:
  dialect: python
  window_s: 300
  statement: "duration_ms <= 1000"
severity: sev3
wake_condition: {operator: ">=", threshold: 1, consecutive: 1, target_agent_id: application__svc-worker}
confidence: 0.9
created_by: human_curator
version: "1.0.0"
status: enforced
---
# Python duration check
"""
    )
    snt_py = compiler.compile(py_inv)
    res_py = compiler.evaluate_sentinel(
        snt_py,
        python_rows=[
            {"duration_ms": 200},
            {"duration_ms": 1500},
            {"duration_ms": 300},
        ],
    )
    assert res_py["violations"] == 1

    # 3. Mutating/locking Postgres queries and multi-statement SQL must be rejected
    for forbidden_sql in (
        "SELECT 1; DROP TABLE items",
        "SELECT pg_terminate_backend(123) AS violations",
        "SELECT set_config('default_transaction_read_only', 'off', false) AS violations",
        "SELECT lo_unlink(1) AS violations",
        "SELECT * FROM tasks FOR UPDATE",
        "SELECT * INTO temp_tbl FROM tasks",
    ):
        with pytest.raises(InvariantValidationError):
            validate_check_statement_syntax("postgres", forbidden_sql)

    # 4. Python mode='exec' statements and AST sandbox escape attempts must be rejected
    for forbidden_py in (
        "x = 1",
        "import os\nx = 1",
        "len(().__class__.__base__.__subclasses__()) > 0",
        "eval('1 == 1')",
        "[x for x in (1, 2)]",
    ):
        with pytest.raises(InvariantValidationError):
            validate_check_statement_syntax("python", forbidden_py)


@pytest.mark.unit
def test_invariant_lifecycle_proposed_shadow_enforced_retired_and_shadow_stats() -> None:
    """Agent Spec §7.3 & Decision A8: proposed -> shadow (24h) -> enforced -> retired (30d + owner confirmation)."""
    bq = InMemoryBigQuery()
    compiler = InvariantCompiler(bigquery=bq)
    lifecycle = InvariantLifecycleManager(compiler=compiler)

    raw_md = """---
name: latency-spike-rule
scope: {service: svc-step2, entity: record, journey: processing_flow}
provenance: observed
provenance_ref: "inc-20261016-0001"
check:
  dialect: bigquery
  window_s: 300
  statement: "SELECT COUNT(*) AS violations FROM `godview_ledger.events` WHERE duration_ms > 2000"
severity: sev2
wake_condition: {operator: ">=", threshold: 1, consecutive: 2, target_agent_id: application__svc-step2}
confidence: 0.85
created_by: application__svc-step2
version: "1.0.0"
status: proposed
---
# Observed latency cap
"""
    doc = parse_invariant_markdown(raw_md)
    assert doc.status == "proposed"

    # Negative direction: if baseline has violations, proposed -> shadow self-consistency fails
    bq.insert_rows(
        "events",
        [
            {
                "event_id": "evt-dirty-1",
                "event_time": datetime.now(tz=UTC),
                "env_id": "env-01",
                "service": "svc-step2",
                "operation": "process",
                "event_type": "step.executed",
                "duration_ms": 3500,
                "source": "otel",
            }
        ],
    )
    with pytest.raises(InvariantLifecycleError):
        lifecycle.promote_to_shadow(doc)

    # Clean baseline -> proposed -> shadow succeeds
    bq.clear_table("events")
    doc, sentinel = lifecycle.promote_to_shadow(doc)
    assert doc.status == "shadow"
    assert sentinel.state == "shadow"

    # Shadow runs update shadow_stats and NEVER wake an agent even on consecutive breaches
    for _ in range(19):
        doc, sentinel, wake = lifecycle.record_evaluation(doc, sentinel, violations=0)
        assert wake is False
    doc, sentinel, wake = lifecycle.record_evaluation(
        doc, sentinel, violations=3, is_false_positive=False
    )
    assert wake is False
    assert doc.shadow_stats.runs == 20
    assert doc.shadow_stats.breaches == 1
    assert doc.shadow_stats.false_positive_rate == 0.0

    # Negative direction: shadow -> enforced before 24h shadow window elapsed fails (Decision A8)
    with pytest.raises(InvariantLifecycleError):
        lifecycle.promote_to_enforced(doc, sentinel, shadow_elapsed_hours=12.0)

    # Positive direction: shadow -> enforced after 24h with fp_rate < 5% and confidence >= 0.8 succeeds
    doc, sentinel = lifecycle.promote_to_enforced(doc, sentinel, shadow_elapsed_hours=24.5)
    assert doc.status == "enforced"
    assert sentinel.state == "enforced"

    # Enforced sentinel wakes only after consecutive >= 2 breaches
    doc, sentinel, wake1 = lifecycle.record_evaluation(doc, sentinel, violations=2)
    assert wake1 is False
    doc, sentinel, wake2 = lifecycle.record_evaluation(doc, sentinel, violations=2)
    assert wake2 is True

    # Operator marks false positive -> enforced demotes back to shadow with fp_rate=1.0
    doc, sentinel = lifecycle.demote_on_false_positive(doc, sentinel)
    assert doc.status == "shadow"
    assert sentinel.state == "shadow"
    assert doc.shadow_stats.false_positive_rate == 1.0

    # Immediate re-promotion before clean shadow runs decay fp_rate below 5% must fail
    with pytest.raises(InvariantLifecycleError):
        lifecycle.promote_to_enforced(doc, sentinel, shadow_elapsed_hours=25.0)

    # After 25 clean shadow runs, fp_rate decays to 1/26 (~3.85% < 5%), allowing promotion without manual mutation
    for _ in range(25):
        doc, sentinel, _ = lifecycle.record_evaluation(doc, sentinel, violations=0)
    assert doc.shadow_stats.false_positive_rate < 0.05
    doc, sentinel = lifecycle.promote_to_enforced(doc, sentinel, shadow_elapsed_hours=25.0)

    # Both negative directions for auto-retirement:
    with pytest.raises(InvariantLifecycleError):
        lifecycle.retire(doc, sentinel, days_without_breach=29.0, owner_confirmed=True)
    with pytest.raises(InvariantLifecycleError):
        lifecycle.retire(doc, sentinel, days_without_breach=35.0, owner_confirmed=False)

    # Positive direction: >= 30d without breach AND owner_confirmed=True -> retired
    doc, sentinel = lifecycle.retire(doc, sentinel, days_without_breach=30.0, owner_confirmed=True)
    assert doc.status == "retired"
    assert sentinel.state == "retired"


@pytest.mark.unit
def test_skill_loader_frontmatter_validation_and_gcs_sync(tmp_path: Path) -> None:
    """Agent Spec §8: SKILL.md frontmatter validation against CAPABILITY_REGISTRY and GCS sync."""
    storage = InMemorySkillsStorageClient()
    bucket_uri = default_skills_bucket_uri()
    assert bucket_uri.startswith("gs://") and bucket_uri.endswith("-god-view-agent-skills")

    loader = SkillLoader(storage_client=storage, bucket_uri=bucket_uri)

    skill_md = """---
name: diagnose-and-heal-lock-contention
description: Isolates and resolves PostgreSQL lock contention.
version: 1.0.0
created_by: data__db-node
owner_agent_id: data__db-node
provenance: human_runbook
confidence_score: 1.0
executions_count: 0
avg_mttr_seconds: 0.0
trigger:
  sentinel_classes: [golden_signal_gate]
  symptom: "latency above threshold"
required_capabilities: [obs.get_trace, sql.blocking_tree, sql.terminate_backend]
steps:
  - {capability: obs.get_trace, min_trust_level: 0}
  - {capability: sql.blocking_tree, min_trust_level: 0}
  - {capability: sql.terminate_backend, min_trust_level: 3}
blast_radius: one process
reversal: none required
verification: latency normal
---
# Skill: Diagnose and Heal Lock Contention
Investigates blocking lock graphs and cancels offending PID.
"""
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text(skill_md, encoding="utf-8")

    skill_doc = loader.load_file(skill_file)
    assert isinstance(skill_doc, ProceduralSkillDoc)
    assert skill_doc.name == "diagnose-and-heal-lock-contention"
    assert skill_doc.owner_agent_id == "data__db-node"
    assert len(skill_doc.steps) == 3

    synced_doc = loader.sync_to_gcs(skill_doc)
    assert synced_doc.gcs_uri == f"{bucket_uri}/skills/{skill_doc.name}/SKILL.md"

    # Round-trip fetch from GCS fake
    fetched = loader.load_from_gcs(skill_doc.name)
    assert fetched.name == skill_doc.name
    assert fetched.version == skill_doc.version
    assert fetched.body_md.strip().startswith("# Skill:")

    # Negative test: unknown capability in SKILL.md must raise SkillValidationError
    bad_skill_md = skill_md.replace("sql.blocking_tree", "invented.nonexistent_capability")
    with pytest.raises(SkillValidationError):
        loader.parse_markdown(bad_skill_md)

    # Verify GcsSkillsStorageClient instantiation and HTTP upload/download with mocked urlopen
    stored_blobs: dict[str, bytes] = {}

    class _FakeGcsResp(io.BytesIO):
        def __enter__(self) -> _FakeGcsResp:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    def _fake_urlopen(req: urllib.request.Request, timeout: float = 10.0) -> _FakeGcsResp:
        if req.get_method() == "POST":
            stored_blobs["last"] = req.data or b""  # type: ignore[assignment]
            return _FakeGcsResp(b'{"name":"skills/test/SKILL.md"}')
        return _FakeGcsResp(stored_blobs.get("last", b""))

    gcs_client = GcsSkillsStorageClient(token_provider=lambda: "tok-test")
    with patch.object(urllib.request, "urlopen", side_effect=_fake_urlopen):
        gcs_loader = SkillLoader(
            storage_client=gcs_client, bucket_uri="gs://proj-test-god-view-agent-skills"
        )
        synced_gcs = gcs_loader.sync_to_gcs(skill_doc)
        assert (
            synced_gcs.gcs_uri
            == f"gs://proj-test-god-view-agent-skills/skills/{skill_doc.name}/SKILL.md"
        )
        loaded_gcs = gcs_loader.load_from_gcs(skill_doc.name)
        assert loaded_gcs.name == skill_doc.name
