"""L1 Hermetic Unit & Contract Tests for godview/invariants and godview/agent-skills
(TDD §2.4, §4 UC-04..UC-08, Agent Spec §7 & §8, Design §6.2, Decision A8).

All test data in this file uses environment-neutral names (TDD I-02).
Every SQL invariant statement is parsed with sqlglot (BigQuery / Postgres dialect).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

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
    SEED_INVARIANT_NAMES,
    InvariantValidationError,
    load_invariant_file,
    load_seed_invariants,
    parse_invariant_markdown,
    resolve_invariant_path,
    validate_check_statement_syntax,
)
from godview.invariants.skills import (
    GcsSkillsStorageClient,
    InMemorySkillsStorageClient,
    SkillLoader,
    SkillValidationError,
    default_skills_bucket_uri,
)
from godview.ledger.process_mining import (
    STEP_CREDIT,
    STEP_DECIDE,
    STEP_POLICY,
    STEP_SETTLE,
    STEP_VERIFY,
)


@pytest.mark.unit
@pytest.mark.contract
def test_seed_invariants_load_validate_and_parse_with_sqlglot() -> None:
    """Design §6.2 & Agent Spec §7: All 5 seed invariants exist under godview/invariants/vehicle_loan_origination/<name>/INVARIANT.md, validate, parse with sqlglot, and compile to SentinelDefinition."""
    repo_root = Path(__file__).resolve().parents[3]
    vault_dir = repo_root / "godview" / "invariants" / "vehicle_loan_origination"
    expected_names = {p.parent.name for p in vault_dir.glob("*/INVARIANT.md")}
    assert len(expected_names) == 5
    assert set(SEED_INVARIANT_NAMES) == expected_names

    seed_docs = load_seed_invariants(journey_key="vehicle_loan_origination")
    assert len(seed_docs) == 5
    assert {doc.name for doc in seed_docs} == expected_names

    # Also verify load_invariant_file directly on a resolved path
    single_path = resolve_invariant_path("step-order", journey_key="vehicle_loan_origination")
    single_doc = load_invariant_file(single_path)
    assert single_doc.name == "step-order"

    compiler = InvariantCompiler()
    for doc in seed_docs:
        assert isinstance(doc, InvariantDoc)
        assert doc.doc_id == f"inv-{doc.name}"
        assert doc.scope.journey == "vehicle_loan_origination"
        assert doc.body_md.strip().startswith("#")

        # Validate SQL syntax with sqlglot (BigQuery dialect)
        parsed = sqlglot.parse_one(doc.check.statement, read="bigquery")
        assert isinstance(parsed, exp.Select), f"Seed invariant {doc.name} did not parse as SELECT"

        # Compile to SentinelDefinition
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
def test_invariant_compiler_dialects_bigquery_postgres_python_and_uc_drills() -> None:
    """Test evaluating compiled invariants across bigquery, postgres, and python dialects (both clean and violation directions)."""
    bq = InMemoryBigQuery()
    compiler = InvariantCompiler(bigquery=bq)
    seed_by_name = {
        doc.name: doc for doc in load_seed_invariants(journey_key="vehicle_loan_origination")
    }
    now = datetime.now(tz=UTC)

    # 1. Clean baseline journey in BigQuery fake -> all 5 seed invariants return 0 violations
    # Principal = 900,000, rate = 9.0% p.a. (0.0075/mo), tenure = 48 months -> EMI = 22396.54
    bq.insert_rows(
        "events",
        [
            {
                "event_id": "evt-clean-s1",
                "event_time": now - timedelta(seconds=30),
                "env_id": "env-01",
                "journey_id": "j-clean-1",
                "service": "svc-step1",
                "operation": "verify",
                "event_type": "kyc.verified",
                "loan_id": "ln-clean-1",
                "source": "otel",
            },
            {
                "event_id": "evt-clean-s2",
                "event_time": now - timedelta(seconds=25),
                "env_id": "env-01",
                "journey_id": "j-clean-1",
                "service": "svc-step2",
                "operation": "evaluate",
                "event_type": "loan.decided",
                "loan_id": "ln-clean-1",
                "decision": "APPROVED",
                "foir_pct": 48.0,
                "cibil_score": 745,
                "amount_inr": 900000.0,
                "emi_inr": 22396.54,
                "source": "otel",
                "attributes": {"interest_rate_pct": 9.0, "tenure_months": 48},
            },
            {
                "event_id": "evt-clean-s3",
                "event_time": now - timedelta(seconds=20),
                "env_id": "env-01",
                "journey_id": "j-clean-1",
                "service": "svc-step3",
                "operation": "issue",
                "event_type": "insurance.issued",
                "loan_id": "ln-clean-1",
                "amount_inr": 15000.0,
                "source": "otel",
            },
            {
                "event_id": "evt-clean-s4",
                "event_time": now - timedelta(seconds=15),
                "env_id": "env-01",
                "journey_id": "j-clean-1",
                "service": "svc-step4",
                "operation": "disburse",
                "event_type": "disbursement.settled",
                "loan_id": "ln-clean-1",
                "amount_inr": 900000.0,
                "source": "otel",
                "attributes": {"sanction_letter_no": "SAN-2026-001"},
            },
            # Probe event with bad FOIR must be ignored by invariant checks
            {
                "event_id": "evt-probe-ignored",
                "event_time": now - timedelta(seconds=10),
                "env_id": "env-01",
                "journey_id": "j-probe-1",
                "service": "svc-step2",
                "operation": "evaluate",
                "event_type": "loan.decided",
                "decision": "APPROVED",
                "foir_pct": 85.0,
                "cibil_score": 550,
                "source": "probe",
            },
        ],
    )
    bq.insert_rows(
        "journeys",
        [
            {
                "journey_id": "j-clean-1",
                "journey_name": "vehicle_loan_origination",
                "started_at": now - timedelta(seconds=30),
                "ended_at": now - timedelta(seconds=15),
                "env_id": "env-01",
                "customer_id": "cust-1",
                "steps": [],
                "observed_sequence": f"{STEP_VERIFY}->{STEP_DECIDE}->{STEP_POLICY}->{STEP_SETTLE}",
                "conformance": "CONFORMANT",
                "final_status": "DISBURSED",
                "rupee_value": 900000.0,
                "total_duration_ms": 15000,
                "is_synthetic": True,
                "journey_model_version": "1.0",
            },
            # Also verify a 5-step credit-line journey passes step-order with 0 violations
            {
                "journey_id": "j-clean-credit-1",
                "journey_name": "vehicle_loan_origination",
                "started_at": now - timedelta(seconds=25),
                "ended_at": now - timedelta(seconds=10),
                "env_id": "env-01",
                "customer_id": "cust-credit-1",
                "steps": [],
                "observed_sequence": f"{STEP_VERIFY}->{STEP_DECIDE}->{STEP_CREDIT}->{STEP_POLICY}->{STEP_SETTLE}",
                "conformance": "CONFORMANT",
                "final_status": "DISBURSED",
                "rupee_value": 1200000.0,
                "total_duration_ms": 15000,
                "is_synthetic": True,
                "journey_model_version": "1.0",
            },
        ],
    )

    for name, doc in seed_by_name.items():
        snt = compiler.compile(doc)
        res = compiler.evaluate_sentinel(snt)
        assert res["violations"] == 0, f"Expected 0 violations on clean data for {name}, got {res}"

    # 2. Inject violations for each of the 5 seed invariants -> each detects >0 violations
    bq.insert_rows(
        "events",
        [
            # (a) UC-04 FOIR > 60 approved
            {
                "event_id": "evt-bad-foir",
                "event_time": now - timedelta(seconds=5),
                "env_id": "env-01",
                "journey_id": "j-bad-1",
                "service": "svc-step2",
                "operation": "evaluate",
                "event_type": "loan.decided",
                "loan_id": "ln-bad-1",
                "decision": "APPROVED",
                "foir_pct": 68.5,
                "cibil_score": 710,
                "amount_inr": 900000.0,
                "emi_inr": 22000.0,  # (e) UC-06 EMI rounding error (expected 22396.54, diff > 1)
                "source": "otel",
                "attributes": {"interest_rate_pct": 9.0, "tenure_months": 48},
            },
            # (b) Disbursement without sanction letter or real approved decision (even if a probe APPROVED event exists on j-unapproved)
            {
                "event_id": "evt-probe-unapproved",
                "event_time": now - timedelta(seconds=6),
                "env_id": "env-01",
                "journey_id": "j-unapproved",
                "service": "svc-step2",
                "operation": "evaluate",
                "event_type": "loan.decided",
                "loan_id": "ln-unapproved",
                "decision": "APPROVED",
                "foir_pct": 40.0,
                "cibil_score": 780,
                "source": "probe",
            },
            {
                "event_id": "evt-bad-disb",
                "event_time": now - timedelta(seconds=4),
                "env_id": "env-01",
                "journey_id": "j-unapproved",
                "service": "svc-step4",
                "operation": "disburse",
                "event_type": "disbursement.settled",
                "loan_id": "ln-unapproved",
                "amount_inr": 500000.0,
                "source": "otel",
                "attributes": {},
            },
            # (c) Policy issued for unapproved journey
            {
                "event_id": "evt-bad-ins",
                "event_time": now - timedelta(seconds=3),
                "env_id": "env-01",
                "journey_id": "j-unapproved",
                "service": "svc-step3",
                "operation": "issue",
                "event_type": "insurance.issued",
                "loan_id": "ln-unapproved",
                "amount_inr": 12000.0,
                "source": "otel",
            },
        ],
    )
    bq.insert_rows(
        "journeys",
        [
            # (d) UC-08 Reordered journey
            {
                "journey_id": "j-reordered-1",
                "journey_name": "vehicle_loan_origination",
                "started_at": now - timedelta(seconds=10),
                "ended_at": now - timedelta(seconds=2),
                "env_id": "env-01",
                "customer_id": "cust-2",
                "steps": [],
                "observed_sequence": f"{STEP_POLICY}->{STEP_VERIFY}->{STEP_DECIDE}->{STEP_SETTLE}",
                "conformance": "REORDERED",
                "final_status": "DISBURSED",
                "rupee_value": 750000.0,
                "total_duration_ms": 8000,
                "is_synthetic": True,
                "journey_model_version": "1.0",
            }
        ],
    )

    for name, doc in seed_by_name.items():
        snt = compiler.compile(doc)
        res = compiler.evaluate_sentinel(snt)
        assert res["violations"] >= 1, f"Expected violation for {name}, got {res}"

    # 3. Test postgres and python dialects
    pg_md = """---
name: pg-orphan-disbursements
scope: {entity: disbursement, journey: vehicle_loan_origination}
provenance: code
provenance_ref: "schema.sql@a1b2c3d#L10-L30"
check:
  dialect: postgres
  window_s: 300
  statement: "SELECT COUNT(*) AS violations FROM disbursements d LEFT JOIN loan_applications l ON d.loan_id = l.loan_id WHERE l.loan_id IS NULL"
severity: sev2
wake_condition: {operator: ">=", threshold: 1, consecutive: 1, target_agent_id: data__pg-primary}
confidence: 0.90
created_by: data__pg-primary
version: "1.0.0"
status: enforced
---
# No orphan disbursements
"""
    pg_doc = parse_invariant_markdown(pg_md)
    pg_snt = compiler.compile(pg_doc)
    assert pg_snt.sentinel_id == "snt-invariant_sql-pg-orphan-disbursements"
    pg_res = compiler.evaluate_sentinel(pg_snt, postgres_runner=lambda sql: [{"violations": 2}])
    assert pg_res["violations"] == 2

    py_md = """---
name: py-emi-tolerance
scope: {entity: loan_application, journey: vehicle_loan_origination}
provenance: code
provenance_ref: "rules.py@a1b2c3d#L1-L20"
check:
  dialect: python
  window_s: 300
  statement: "abs(emi_inr - expected_emi(amount_inr, interest_rate_pct, tenure_months)) <= 1.0"
severity: sev2
wake_condition: {operator: ">=", threshold: 1, consecutive: 1, target_agent_id: application__svc-step2}
confidence: 0.95
created_by: application__svc-step2
version: "1.0.0"
status: enforced
---
# Python EMI formula check within ±1 INR
"""
    py_doc = parse_invariant_markdown(py_md)
    py_snt = compiler.compile(py_doc)
    py_clean = compiler.evaluate_sentinel(
        py_snt,
        python_rows=[
            {
                "amount_inr": 900000.0,
                "interest_rate_pct": 9.0,
                "tenure_months": 48,
                "emi_inr": 22396.54,
            }
        ],
    )
    assert py_clean["violations"] == 0
    py_breach = compiler.evaluate_sentinel(
        py_snt,
        python_rows=[
            {
                "amount_inr": 900000.0,
                "interest_rate_pct": 9.0,
                "tenure_months": 48,
                "emi_inr": 22000.00,
            }
        ],
    )
    assert py_breach["violations"] == 1

    # 4. Invalid SQL, non-SELECT SQL, or missing frontmatter must fail loudly with InvariantValidationError
    bad_sql_md = pg_md.replace("SELECT COUNT(*)", "SELEKT BROKEN SYNTAX FROM (")
    with pytest.raises(InvariantValidationError):
        parse_invariant_markdown(bad_sql_md)

    non_select_md = pg_md.replace(
        "SELECT COUNT(*) AS violations FROM disbursements d LEFT JOIN loan_applications l ON d.loan_id = l.loan_id WHERE l.loan_id IS NULL",
        "DROP TABLE disbursements",
    )
    with pytest.raises(InvariantValidationError):
        parse_invariant_markdown(non_select_md)

    with pytest.raises(InvariantValidationError):
        parse_invariant_markdown("no frontmatter here")

    assert resolve_invariant_path("step-order-alias").name == "INVARIANT.md"
    assert resolve_invariant_path("emi-formula-pm-1").name == "INVARIANT.md"

    # 5. Mutating/locking Postgres queries and multi-statement SQL must be rejected
    for forbidden_sql in (
        "SELECT 1; DROP TABLE disbursements",
        "SELECT pg_terminate_backend(123) AS violations",
        "SELECT set_config('default_transaction_read_only', 'off', false) AS violations",
        "SELECT lo_unlink(1) AS violations",
        "SELECT * FROM loan_applications FOR UPDATE",
        "SELECT * INTO temp_tbl FROM loan_applications",
    ):
        with pytest.raises(InvariantValidationError):
            validate_check_statement_syntax("postgres", forbidden_sql)

    # 6. Python mode='exec' statements and AST sandbox escape attempts must be rejected
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
name: custom-foir-rule
scope: {service: svc-step2, entity: loan_application, journey: vehicle_loan_origination}
provenance: observed
provenance_ref: "inc-20261016-0001"
check:
  dialect: bigquery
  window_s: 300
  statement: "SELECT COUNT(*) AS violations FROM `godview_ledger.events` WHERE decision = 'APPROVED' AND foir_pct > 60"
severity: sev2
wake_condition: {operator: ">=", threshold: 1, consecutive: 2, target_agent_id: application__svc-step2}
confidence: 0.85
created_by: application__svc-step2
version: "1.0.0"
status: proposed
---
# Observed FOIR cap
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
                "operation": "eval",
                "event_type": "loan.decided",
                "decision": "APPROVED",
                "foir_pct": 72.0,
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
    repo_root = Path(__file__).resolve().parents[3]
    seed_skill_path = (
        repo_root
        / "godview"
        / "agent-skills"
        / "diagnose-and-heal-postgres-lock-contention"
        / "SKILL.md"
    )
    assert seed_skill_path.is_file()

    skill_doc = loader.load_file(seed_skill_path)
    assert isinstance(skill_doc, ProceduralSkillDoc)
    assert skill_doc.name == "diagnose-and-heal-postgres-lock-contention"
    assert skill_doc.owner_agent_id == "data__god-view-pg"
    assert len(skill_doc.steps) == 4

    synced_doc = loader.sync_to_gcs(skill_doc)
    assert synced_doc.gcs_uri == f"{bucket_uri}/skills/{skill_doc.name}/SKILL.md"

    # Round-trip fetch from GCS fake
    fetched = loader.load_from_gcs(skill_doc.name)
    assert fetched.name == skill_doc.name
    assert fetched.version == skill_doc.version
    assert fetched.body_md.strip().startswith("# Skill:")

    # Negative test: unknown capability in SKILL.md must raise SkillValidationError
    bad_skill_md = seed_skill_path.read_text(encoding="utf-8").replace(
        "sql.blocking_tree", "invented.nonexistent_capability"
    )
    with pytest.raises(SkillValidationError):
        loader.parse_markdown(bad_skill_md)

    # Verify GcsSkillsStorageClient instantiation and HTTP upload/download with mocked urlopen
    import io
    import urllib.request
    from unittest.mock import patch

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
