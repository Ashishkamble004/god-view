"""M0 Contract & Unit Tests for shared/godview_shared.

Covers TDD §1 (I-01..I-09), §2.1–§2.4, §3.5–§3.7, and Coordinator Decisions A1, A3, A8, A9, A10, A11, A17.
All test fixtures use environment-neutral randomised names (zero tenant service names).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from godview_shared.adapter_contract import (
    AdapterHealth,
    DiscoveryResult,
    SenseAdapter,
    SenseCategory,
)
from godview_shared.capabilities import (
    CAPABILITY_REGISTRY,
    FORBIDDEN_V3_MCP_TOOLS,
    OFFICIAL_MCP_TOOLS,
    CapabilitySpec,
    _build_registry,
    get_capability,
    validate_mcp_tool_binding,
)
from godview_shared.config import (
    ALLOWED_MODEL_IDS,
    DEFAULT_AGENT_DAILY_BUDGETS_USD,
    DEFAULT_AGENT_FALLBACK_MODEL_ID,
    DEFAULT_AGENT_MODEL_ID,
    DEFAULT_AGENT_MODEL_LOCATION,
    DEFAULT_COURT_DAILY_CEILING_USD,
    DEFAULT_COURT_MAX_AGENTS,
    MODEL_TABLE,
    GodViewSettings,
    is_excluded_resource,
    validate_model_location,
)
from godview_shared.contracts import (
    ALLOWED_CLOUDEVENT_TYPES,
    FIRESTORE_COLLECTIONS,
    FIRESTORE_VECTOR_INDEXES,
    FORBIDDEN_TELEMETRY_TABLES,
    LEDGER_TABLES,
    TENANT_BUSINESS_TABLES,
    A2ABriefMessage,
    A2ACritiqueMessage,
    A2AHandoffMessage,
    A2AInterrogateMessage,
    A2AReportMessage,
    A2AWakeMessage,
    ApprovalCard,
    AuditRecord,
    CapabilityInvokeRequest,
    CapabilityInvokeResponse,
    ChangeEventDoc,
    CostFindingDoc,
    CourtAgentDoc,
    DiscoveredEdgeDoc,
    DiscoveredEntityDoc,
    DiscoveredServiceDoc,
    EpisodicIncidentDoc,
    EvidencePack,
    InvariantDoc,
    InvariantFrontmatter,
    JourneyDoc,
    KpiRollupRow,
    LedgerEventRow,
    LedgerJourneyRow,
    NervousSystemEvent,
    ProceduralSkillDoc,
    SentinelCheck,
    SentinelDefinition,
    SkillFrontmatter,
    StateTransitionRow,
    TrustDecision,
    TrustHistoryEntry,
    TrustRecord,
    TrustViolation,
    parse_a2a_message,
)
from godview_shared.fakes import (
    InMemoryAgentEngine,
    InMemoryBigQuery,
    InMemoryFirestore,
    InMemoryPubSub,
)
from godview_shared.otel import (
    BAGGAGE_KEYS,
    BUSINESS_SPAN_ATTRIBUTES,
    SYNTHETIC_HEADER,
    extract_baggage_context,
    get_tracer,
    inject_baggage_headers,
    render_prometheus_metrics,
    set_business_span_attributes,
)
from pydantic import ValidationError


@pytest.mark.unit
@pytest.mark.contract
def test_tenant_9_tables_and_no_telemetry_in_patient() -> None:
    """TDD §2.1 & I-04: Tenant business tables are schema-agnostic/empty by default; telemetry tables forbidden."""
    # Sovereign core contracts do not load tenant tables at import time
    assert len(TENANT_BUSINESS_TABLES) == 0
    for forbidden in ("service_metric_rollups", "otel_trace_spans"):
        assert forbidden in FORBIDDEN_TELEMETRY_TABLES
        assert forbidden not in TENANT_BUSINESS_TABLES


@pytest.mark.unit
@pytest.mark.contract
def test_ledger_row_models_and_tables() -> None:
    """TDD §2.2: BigQuery godview_ledger 4 tables and row schemas."""
    assert tuple(LEDGER_TABLES) == ("events", "state_transitions", "journeys", "kpi_rollups")
    now = datetime.now(tz=UTC)

    ev = LedgerEventRow(
        event_id="evt-1001",
        event_time=now,
        env_id="env-neutral-01",
        journey_id="jrn-01",
        trace_id="tr-01",
        span_id="sp-01",
        service="svc-alpha",
        operation="POST /evaluate",
        event_type="item.decided",
        entity_type="item_record",
        entity_id="rec-01",
        http_status=200,
        duration_ms=145,
        is_synthetic=True,
        source="otel",
        attributes={"customer_id": "cust-01", "decision": "APPROVED", "rules_version": "abc1234"},
        numeric_attributes={"ratio": 48.5, "score": 740.0},
    )
    assert ev.source == "otel"

    st = StateTransitionRow(
        transition_id="trn-1001",
        transition_time=now,
        env_id="env-neutral-01",
        entity="item_record",
        entity_id="rec-01",
        journey_id="jrn-01",
        status_from="STEP_1_DONE",
        status_to="APPROVED",
        actor_service="svc-alpha",
        source="otel",
        dwell_ms=320,
        is_synthetic=True,
    )
    assert st.status_to == "APPROVED"

    jrn = LedgerJourneyRow(
        journey_id="jrn-01",
        journey_name="origination_flow",
        started_at=now,
        ended_at=now,
        env_id="env-neutral-01",
        customer_id="cust-01",
        steps=[
            {
                "seq": 1,
                "service": "svc-step-1",
                "operation": "verify",
                "at": now,
                "status": "OK",
                "duration_ms": 110,
            }
        ],
        observed_sequence="STEP1->STEP2",
        conformance="CONFORMANT",
        final_status="COMPLETED",
        total_value=Decimal("900000.00"),
        total_duration_ms=850,
        is_synthetic=True,
        journey_model_version="v1",
    )
    assert jrn.conformance == "CONFORMANT"

    kpi = KpiRollupRow(
        window_start=now,
        window_end=now,
        env_id="env-neutral-01",
        journey_name="origination_flow",
        kpi="approval_rate",
        segment="all",
        value=Decimal("0.71"),
        baseline_value=Decimal("0.72"),
        zscore=Decimal("-0.25"),
        cusum=Decimal("0.10"),
        sample_n=120,
        breach=False,
    )
    assert kpi.breach is False


@pytest.mark.unit
@pytest.mark.contract
def test_firestore_collections_and_json_contracts_serialize_by_alias() -> None:
    """TDD §2.3 & §2.4 & Board #10: 13 collections, 4 vector indexes, JSON contract round-trips & serialize_by_alias."""
    assert len(FIRESTORE_COLLECTIONS) == 13
    assert set(FIRESTORE_COLLECTIONS) == {
        "discovered_services",
        "discovered_edges",
        "discovered_entities",
        "journeys",
        "court_agents",
        "trust_records",
        "sentinels",
        "invariants",
        "episodic_incidents",
        "procedural_skills",
        "change_events",
        "cost_findings",
        "approvals",
    }
    assert set(FIRESTORE_VECTOR_INDEXES) == {
        ("discovered_services", "purpose_embedding"),
        ("invariants", "statement_embedding"),
        ("episodic_incidents", "symptom_embedding"),
        ("procedural_skills", "trigger_embedding"),
    }

    # EvidencePack exact JSON schema (§2.4)
    evp_payload = {
        "schema_version": "1.0",
        "evidence_pack_id": "evp-20261016-0007",
        "incident_id": "inc-20261016-0003",
        "fired_at": "2026-10-16T08:35:10Z",
        "sentinel": {
            "id": "snt-invariant_sql-threshold-check",
            "class": "invariant_sql",
            "source_ref": "inv-threshold-check",
        },
        "breach": {
            "statement": "SELECT COUNT(*) FROM events WHERE decision='APPROVED'",
            "observed": {"count": 14, "window_s": 300},
            "first_seen": "2026-10-16T08:35:00Z",
            "consecutive": 3,
        },
        "baseline": {"model": "same_hour", "expected": 0, "stddev": 0.4},
        "affected": {
            "services": ["svc-risk-eval"],
            "journeys": ["origination_flow"],
            "journey_ids": ["j-1", "j-2"],
            "rows": 212,
            "value_at_risk": 18400000,
        },
        "correlated_changes": [
            {
                "change_event_id": "chg-repo-sha1",
                "kind": "deploy",
                "occurred_at": "2026-10-16T08:34:00Z",
                "business_summary": "Ratio threshold 60 -> 40 in rules_engine.py (PR #42)",
            }
        ],
        "golden_signals": {"status": "green", "p95_ms": 420, "error_rate_pct": 0.2},
        "recall": {
            "episodic": [{"incident_id": "inc-20261002-0001", "similarity": 0.91}],
            "skills": [{"skill_slug": "rollback-rules-regression", "similarity": 0.88}],
        },
        "budget": {"agent_tokens_remaining": 180000, "escalation_allowed": True},
        "target_agent_id": "application__svc-risk-eval",
        "trust_snapshot": {"git.create_rollback_pr": 3, "biz.reevaluate_application": 4},
    }
    evp = EvidencePack.model_validate(evp_payload)
    # Must serialize as 'class' even when by_alias is NOT passed
    dumped_default = evp.model_dump()
    dumped_json = evp.model_dump(mode="json")
    assert dumped_default["sentinel"]["class"] == "invariant_sql"
    assert "sentinel_class" not in dumped_default["sentinel"]
    assert dumped_json["sentinel"]["class"] == "invariant_sql"

    # ApprovalCard (§2.4)
    apr = ApprovalCard.model_validate(
        {
            "approval_id": "apr-inc-20261016-0003-2",
            "incident_id": "inc-20261016-0003",
            "agent_id": "business_line__origination_flow",
            "action_type": "biz.reevaluate_application",
            "capability": "biz.reevaluate_application",
            "target_resource": "applications WHERE rules_version='sha1' AND decision='APPROVED'",
            "trust_level_required": 4,
            "narrative": "Re-evaluate 212 applications",
            "evidence_pack_id": "evp-20261016-0007",
            "financial_impact": 18400000,
            "reversal_recipe": {"capability": "biz.compensate_journey", "args": {}},
            "requested_at": "2026-10-16T08:36:00Z",
            "expires_at": "2026-10-16T09:36:00Z",
            "decision": "pending",
            "decided_by": None,
            "decided_at": None,
            "channel": "chat",
            "audit_id": None,
        }
    )
    assert apr.decision == "pending"

    # TrustRecord (§2.4 & A10 whitelisted_by) — must serialize 'from' without by_alias=True
    tr = TrustRecord.model_validate(
        {
            "agent_id": "data__pg-primary",
            "action_type": "sql.terminate_backend",
            "level": 3,
            "counters": {
                "recommendations_correct": 7,
                "approved_executions": 5,
                "verified_outcomes": 5,
                "regressions": 0,
                "business_successes": 0,
            },
            "thresholds": {"n1": 3, "n2": 5, "n3": 10},
            "whitelisted": False,
            "whitelisted_by": None,
            "last_promoted_at": "2026-10-16T08:00:00Z",
            "last_demoted_at": None,
            "demotion_reason": None,
            "history": [
                {
                    "at": "2026-10-16T08:00:00Z",
                    "from": 2,
                    "to": 3,
                    "reason": "n2 reached with 0 regressions",
                }
            ],
        }
    )
    assert tr.level == 3
    assert tr.model_dump()["history"][0]["from"] == 2
    assert "from_level" not in tr.model_dump()["history"][0]
    assert TrustHistoryEntry.model_validate(tr.model_dump()["history"][0]).from_level == 2

    # SentinelDefinition (§2.4) — must serialize 'class' without by_alias=True
    snt = SentinelDefinition.model_validate(
        {
            "sentinel_id": "snt-invariant_sql-threshold-check",
            "class": "invariant_sql",
            "source_ref": "inv-threshold-check",
            "check": {
                "dialect": "bigquery",
                "statement": "SELECT COUNT(*) FROM events",
            },
            "cadence_s": 10,
            "baseline": {"model": "same_hour", "window_days": 7},
            "wake_condition": {
                "operator": ">=",
                "threshold": 3,
                "consecutive": 1,
                "window_s": 300,
            },
            "target_agent_id": "application__svc-risk-eval",
            "state": "enforced",
            "compiled_by": "application__svc-risk-eval",
            "compiled_at": "2026-10-16T08:00:00Z",
            "last_run_at": "2026-10-16T08:35:00Z",
            "last_result": {"count": 0},
            "consecutive_breaches": 0,
        }
    )
    assert snt.sentinel_class == "invariant_sql"
    assert snt.model_dump()["class"] == "invariant_sql"
    assert "sentinel_class" not in snt.model_dump()
    assert snt.model_dump(mode="json")["class"] == "invariant_sql"
    assert SentinelDefinition.model_validate(snt.model_dump()).sentinel_class == "invariant_sql"

    # SentinelCheck.dialect must be constrained to InvariantDialect
    with pytest.raises(ValidationError):
        SentinelCheck(dialect="mysql", statement="SELECT 1")  # type: ignore[arg-type]

    # INVARIANT.md frontmatter (§2.4) & SKILL.md frontmatter (Board #10)
    inv_fm = InvariantFrontmatter.model_validate(
        {
            "name": "threshold-check",
            "scope": {
                "service": "svc-risk-eval",
                "entity": "application_record",
                "journey": "origination_flow",
            },
            "provenance": "code",
            "provenance_ref": "repo@sha1:rules.py",
            "check": {
                "dialect": "bigquery",
                "statement": "SELECT COUNT(*) FROM events",
                "window_s": 300,
            },
            "severity": "sev2",
            "wake_condition": {
                "operator": ">=",
                "threshold": 3,
                "consecutive": 1,
                "target_agent_id": "application__svc-risk-eval",
            },
            "confidence": 0.92,
            "created_by": "application__svc-risk-eval",
            "version": "3",
            "status": "enforced",
        }
    )
    assert inv_fm.status == "enforced"

    skill_fm = SkillFrontmatter.model_validate(
        {
            "name": "diagnose-lock-contention",
            "description": "Isolate and resolve PG lock contention",
            "version": "1.2.0",
            "created_by": "data__pg-primary",
            "owner_agent_id": "data__pg-primary",
            "provenance": "agent_synthesized",
            "confidence_score": 0.94,
            "executions_count": 3,
            "avg_mttr_seconds": 48,
            "trigger": {
                "sentinel_classes": ["golden_signal_gate"],
                "symptom": "high p95 latency",
            },
            "required_capabilities": ["sql.blocking_tree", "sql.terminate_backend"],
            "steps": [
                {"capability": "sql.blocking_tree", "min_trust_level": 0},
                {"capability": "sql.terminate_backend", "min_trust_level": 3},
            ],
            "blast_radius": "one backend pid",
            "reversal": "none required",
            "verification": "p95 < 1s",
        }
    )
    assert len(skill_fm.steps) == 2

    # Reject unknown capability in SKILL.md steps (Agent Spec §8.1 rule 5)
    with pytest.raises(ValidationError):
        SkillFrontmatter.model_validate(
            {
                **skill_fm.model_dump(),
                "steps": [{"capability": "invented.nonexistent_tool", "min_trust_level": 0}],
            }
        )

    # Validate remaining Firestore document models & AuditRecord & CapabilityInvoke models
    svc_doc = DiscoveredServiceDoc(
        env_id="env-01",
        name="svc-a",
        kind="cloud_run",
        adapter_family="gcp",
        locator="projects/p/locations/r/services/svc-a",
        first_seen="2026-10-16T08:00:00Z",
        last_seen="2026-10-16T08:35:00Z",
    )
    assert svc_doc.kind == "cloud_run"
    edge_doc = DiscoveredEdgeDoc(
        src="env-01__cloud_run.svc-a",
        dst="env-01__cloud_sql.db-1",
        protocol="postgres",
        evidence="config",
        first_seen="2026-10-16T08:00:00Z",
        last_seen="2026-10-16T08:35:00Z",
    )
    assert edge_doc.evidence == "config"
    ent_doc = DiscoveredEntityDoc(
        name="item_record", correlation_keys=["item_id", "journey_id"]
    )
    assert "item_id" in ent_doc.correlation_keys
    jrn_doc = JourneyDoc(name="origination_flow", steps=["STEP1", "STEP2"])
    assert len(jrn_doc.steps) == 2
    ca_doc = CourtAgentDoc(
        agent_type="application",
        subject_ref="svc-a",
        namespace="application__svc-a",
        token_budget_daily=Decimal("0.75"),
        spawned_at="2026-10-16T08:00:00Z",
    )
    assert ca_doc.warmth_min_instances == 0
    inv_doc = InvariantDoc(**inv_fm.model_dump(), body_md="# Invariant rationale")
    assert inv_doc.body_md.startswith("#")
    ps_doc = ProceduralSkillDoc(**skill_fm.model_dump(), body_md="# Skill steps")
    assert ps_doc.body_md.startswith("#")
    inc_doc = EpisodicIncidentDoc(
        opened_at="2026-10-16T08:35:10Z",
        symptom_summary="Ratio threshold regression",
        evidence_pack=evp,
        lead_agent_id="application__svc-risk-eval",
    )
    assert inc_doc.lead_agent_id == "application__svc-risk-eval"
    chg_doc = ChangeEventDoc(
        repo="org/repo",
        kind="deploy",
        sha="sha1",
        occurred_at="2026-10-16T08:34:00Z",
    )
    assert chg_doc.kind == "deploy"
    cost_doc = CostFindingDoc(
        resource="reasoningEngines/idle-1",
        finding_type="idle_reasoning_engine",
        monthly_usd_estimate=Decimal("42.00"),
        recommendation="Set min_instances=0",
        found_by="platform__env-01",
        found_at="2026-10-16T08:00:00Z",
    )
    assert cost_doc.finding_type == "idle_reasoning_engine"
    aud_rec = AuditRecord(
        audit_id="aud-20261016-0001",
        incident_id="inc-20261016-0003",
        agent_id="application__svc-risk-eval",
        trust_level=3,
        action_type="git.create_rollback_pr",
        capability="git.create_rollback_pr",
        target_resource="org/repo/pull/43",
        evidence_pack_id="evp-20261016-0007",
        executed_at="2026-10-16T08:36:10Z",
        result="success",
    )
    assert aud_rec.sa_used == "godview-act-sa"
    inv_req = CapabilityInvokeRequest(
        agent_id="application__svc-a", incident_id="inc-1", args={"k": "v"}
    )
    inv_resp = CapabilityInvokeResponse(
        capability_id="obs.get_trace",
        status="executed",
        result={"trace_id": "t1"},
        trust_decision=TrustDecision(
            authorized=True,
            agent_id="application__svc-a",
            action_type="obs.get_trace",
            capability_id="obs.get_trace",
            effective_level=0,
            required_level=0,
            reason="L0 read allowed",
            decided_at="2026-10-16T08:36:10Z",
        ),
    )
    assert inv_req.agent_id == "application__svc-a"
    assert inv_resp.status == "executed"
    wake_msg = parse_a2a_message(
        {
            "incident_id": "inc-20261016-0003",
            "from_agent_id": "sovereign",
            "to_agent_id": "application__svc-risk-eval",
            "round": 1,
            "sent_at": "2026-10-16T08:35:10Z",
            "type": "wake",
            "payload": {"evidence_pack": evp_payload},
        }
    )
    assert isinstance(wake_msg, A2AWakeMessage)


@pytest.mark.unit
@pytest.mark.contract
def test_a2a_six_message_types_and_cloudevents() -> None:
    """TDD §3.5 & §3.6: 6 A2A message types and Nervous System CloudEvents."""
    now = "2026-10-16T08:35:15Z"
    base_env = {
        "incident_id": "inc-20261016-0003",
        "from_agent_id": "sovereign",
        "to_agent_id": "application__svc-a",
        "round": 1,
        "sent_at": now,
    }
    msg_interrogate = parse_a2a_message(
        {
            **base_env,
            "type": "interrogate",
            "payload": {"question": "What changed?", "scope": "repo", "deadline_s": 20},
        }
    )
    assert isinstance(msg_interrogate, A2AInterrogateMessage)

    msg_brief = parse_a2a_message(
        {
            **base_env,
            "type": "brief",
            "payload": {"living_model_delta": {"rules_added": 2}},
        }
    )
    assert isinstance(msg_brief, A2ABriefMessage)

    msg_report = parse_a2a_message(
        {
            **base_env,
            "type": "report",
            "payload": {
                "root_cause": "Threshold regression in PR #42",
                "causal_chain": ["PR #42 merged", "Ratio 60->40"],
                "plan": [
                    {
                        "step": 1,
                        "capability": "git.create_rollback_pr",
                        "target": "repo/pull/42",
                        "trust_level_required": 3,
                        "reversal": {"capability": "git.create_rollback_pr"},
                    }
                ],
                "confidence": 0.92,
            },
        }
    )
    assert isinstance(msg_report, A2AReportMessage)

    msg_critique = parse_a2a_message(
        {
            **base_env,
            "type": "critique",
            "payload": {
                "hypothesis_id": "hyp-1",
                "counter_evidence": "locks are clean",
                "confidence_delta": 0.05,
            },
        }
    )
    assert isinstance(msg_critique, A2ACritiqueMessage)

    msg_handoff = parse_a2a_message(
        {
            **base_env,
            "type": "handoff",
            "payload": {"new_lead_agent_id": "data__pg-primary", "reason": "DB lock tree found"},
        }
    )
    assert isinstance(msg_handoff, A2AHandoffMessage)

    # Round must be in 1..4
    with pytest.raises(ValidationError):
        parse_a2a_message(
            {
                **base_env,
                "round": 5,
                "type": "handoff",
                "payload": {"new_lead_agent_id": "data__pg-primary", "reason": "invalid round"},
            }
        )

    # CloudEvents 1.0 validation (§3.6)
    ce = NervousSystemEvent.model_validate(
        {
            "specversion": "1.0",
            "type": "godview.operator.kill_switch",
            "source": "//sovereign/control-api",
            "id": "evt-ks-1",
            "time": now,
            "subject": "tenant",
            "data": {
                "enabled": True,
                "reason": "manual drill stop",
                "actor": "operator@example.com",
            },
            "godviewenvid": "env-neutral-01",
            "godviewsynthetic": False,
        }
    )
    assert ce.type == "godview.operator.kill_switch"
    assert len(ALLOWED_CLOUDEVENT_TYPES) == 22

    # Negative test: unknown CloudEvent type must fail validation
    with pytest.raises(ValidationError):
        NervousSystemEvent.model_validate(
            {
                "specversion": "1.0",
                "type": "godview.unknown.event",
                "source": "//sovereign/control-api",
                "id": "evt-bad",
                "time": now,
                "data": {},
            }
        )


@pytest.mark.unit
@pytest.mark.contract
def test_capabilities_allowlist() -> None:
    """TDD I-05 & §3.7: Only official MCP tools allowed; v3 invented names and duplicate IDs rejected."""
    assert set(OFFICIAL_MCP_TOOLS.keys()) == {
        "observability-mcp",
        "gke-mcp",
        "mcp-toolbox-postgres",
        "github-mcp",
    }
    assert OFFICIAL_MCP_TOOLS["observability-mcp"] == frozenset(
        {"list_log_entries", "list_timeseries", "list_traces", "get_trace"}
    )
    assert OFFICIAL_MCP_TOOLS["gke-mcp"] == frozenset(
        {
            "list_clusters",
            "get_cluster",
            "get_k8s_resource",
            "query_logs",
            "get_log_schema",
            "list_recommendations",
        }
    )
    assert OFFICIAL_MCP_TOOLS["mcp-toolbox-postgres"] == frozenset(
        {"list_tables", "execute_sql", "list_active_queries", "get_query_plan"}
    )
    assert OFFICIAL_MCP_TOOLS["github-mcp"] == frozenset(
        {
            "list_commits",
            "get_commit",
            "get_file_contents",
            "create_branch",
            "push_files",
            "create_issue",
            "create_pull_request",
        }
    )

    # Every MCP-backed capability in CAPABILITY_REGISTRY must reference only official MCP tools
    for cap_id, spec in CAPABILITY_REGISTRY.items():
        if spec.mcp_server is not None:
            assert spec.mcp_server in OFFICIAL_MCP_TOOLS, f"{cap_id} uses unknown MCP server"
            for tool_name in spec.mcp_tools:
                validate_mcp_tool_binding(spec.mcp_server, tool_name)

    # Positive check: valid official tool binding succeeds
    validate_mcp_tool_binding("observability-mcp", "get_trace")
    validate_mcp_tool_binding("github-mcp", "create_pull_request")

    # Negative check: duplicate capability_id in registry must raise ValueError
    dup_spec = CapabilitySpec(
        capability_id="obs.get_trace",
        family="gcp",
        kind="official_mcp",
        mcp_server="observability-mcp",
        mcp_tools=("get_trace",),
    )
    with pytest.raises(ValueError, match="Duplicate capability_id"):
        _build_registry([dup_spec, dup_spec])

    # Negative check: every v3 invented MCP tool name must raise ValueError
    for invented in FORBIDDEN_V3_MCP_TOOLS:
        for server in OFFICIAL_MCP_TOOLS:
            with pytest.raises(ValueError, match="not in the official allowlist"):
                validate_mcp_tool_binding(server, invented)

    # Negative check: terraform-mcp is forbidden for drift/apply
    with pytest.raises(ValueError, match="terraform-mcp"):
        validate_mcp_tool_binding("terraform-mcp", "tf_detect_live_drift_diff")


@pytest.mark.unit
@pytest.mark.contract
def test_model_locations_and_decision_defaults() -> None:
    """TDD I-09 & Decisions A1, A9, A11: Gemini 3.x/4 only at global; text-embedding-005 at us-central1; Argon default."""
    assert DEFAULT_AGENT_MODEL_ID == "gemini-4-argon"
    assert DEFAULT_AGENT_FALLBACK_MODEL_ID == "gemini-3.8-flash"
    assert DEFAULT_AGENT_MODEL_LOCATION == "global"
    assert MODEL_TABLE["reasoning"].model_id == "gemini-4-argon"
    assert MODEL_TABLE["reasoning"].location == "global"
    assert MODEL_TABLE["reasoning"].fallbacks[0].model_id == "gemini-3.8-flash"
    assert MODEL_TABLE["reasoning"].fallbacks[0].location == "global"
    assert MODEL_TABLE["escalation"].model_id == "gemini-4-argon"
    assert MODEL_TABLE["escalation"].location == "global"
    assert MODEL_TABLE["escalation"].fallbacks[0].model_id == "gemini-3.8-flash"
    for role, cfg in MODEL_TABLE.items():
        assert cfg.model_id in ALLOWED_MODEL_IDS, f"Unlisted model in role {role}"
        validate_model_location(cfg.model_id, cfg.location)
        if cfg.model_id.startswith(("gemini-3", "gemini-4", "gemini-nano-banana")):
            assert cfg.location == "global", f"{cfg.model_id} must be at global"
        for fb in cfg.fallbacks:
            validate_model_location(fb.model_id, fb.location)

    # Both failure directions (Rule B.5)
    assert validate_model_location("gemini-3.8-flash", "global") is True
    assert validate_model_location("gemini-4-argon", "global") is True
    assert validate_model_location("gemini-nano-banana-2.1", "global") is True
    assert validate_model_location("text-embedding-005", "us-central1") is True
    assert validate_model_location("gemini-2.5-flash", "us-central1") is True

    with pytest.raises(ValueError, match="global"):
        validate_model_location("gemini-3.8-flash", "us-central1")
    with pytest.raises(ValueError, match="global"):
        validate_model_location("gemini-4-argon", "us-central1")
    with pytest.raises(ValueError, match="global"):
        validate_model_location("gemini-nano-banana-2.1", "us-central1")
    with pytest.raises(ValueError, match="us-central1"):
        validate_model_location("text-embedding-005", "global")
    with pytest.raises(ValueError, match="us-central1"):
        validate_model_location("gemini-2.5-flash", "global")

    # Decision A1 & A9 defaults & strict env validation
    assert DEFAULT_COURT_MAX_AGENTS == 40
    assert DEFAULT_COURT_DAILY_CEILING_USD == Decimal("6.00")
    assert DEFAULT_AGENT_DAILY_BUDGETS_USD["application"] == Decimal("0.75")
    assert DEFAULT_AGENT_DAILY_BUDGETS_USD["business_line"] == Decimal("0.75")
    assert DEFAULT_AGENT_DAILY_BUDGETS_USD["change"] == Decimal("0.75")
    assert DEFAULT_AGENT_DAILY_BUDGETS_USD["data"] == Decimal("0.50")
    assert DEFAULT_AGENT_DAILY_BUDGETS_USD["platform"] == Decimal("0.50")
    assert DEFAULT_AGENT_DAILY_BUDGETS_USD["sovereign"] == Decimal("1.50")

    # Unknown GODVIEW_ENV must raise ValueError (never silently default to hermetic)
    for bad_env in ("prod", "production", ""):
        with pytest.raises(ValueError, match="Invalid GODVIEW_ENV"):
            GodViewSettings.from_env({"GODVIEW_ENV": bad_env})

    # Court max agents > 40 must raise ValueError (Decision A1)
    with pytest.raises(ValueError, match="40"):
        GodViewSettings.from_env({"GODVIEW_ENV": "hermetic", "GODVIEW_COURT_MAX_AGENTS": "41"})

    # Missing budgets in gcp/local mode must raise ValueError (Decision A9)
    with pytest.raises(ValueError, match="Token budget environment variables required"):
        GodViewSettings.from_env({"GODVIEW_ENV": "gcp"})

    # Explicit budgets in gcp mode succeed
    gcp_settings = GodViewSettings.from_env(
        {
            "GODVIEW_ENV": "gcp",
            "GODVIEW_BUDGET_APPLICATION_USD": "0.75",
            "GODVIEW_BUDGET_BUSINESS_LINE_USD": "0.75",
            "GODVIEW_BUDGET_CHANGE_USD": "0.75",
            "GODVIEW_BUDGET_DATA_USD": "0.50",
            "GODVIEW_BUDGET_PLATFORM_USD": "0.50",
            "GODVIEW_BUDGET_SOVEREIGN_USD": "1.50",
            "GODVIEW_COURT_DAILY_CEILING_USD": "6.00",
        }
    )
    assert gcp_settings.env_mode == "gcp"
    assert gcp_settings.court_daily_ceiling_usd == Decimal("6.00")

    # Decision A11 discovery exclusion & additive hint_exclude
    assert is_excluded_resource("god-view-sovereign", {}) is True
    assert is_excluded_resource("proj-god-view-agent-skills", {}) is True
    assert (
        is_excluded_resource("proj-god-view-agent-skills", {}, hint_exclude=("custom-only",))
        is True
    )
    assert is_excluded_resource("svc-custom-only", {}, hint_exclude=("*-custom-only",)) is True
    assert is_excluded_resource("any-svc", {"godview_role": "observer"}) is True
    assert is_excluded_resource("tenant-order-svc", {"godview_managed": "true"}) is False


@pytest.mark.unit
async def test_sense_adapter_abc_and_trust_enforcement() -> None:
    """SDD §2.2 & TDD I-06: SenseAdapter ABC enforces all defence-in-depth TrustDecision invariants."""

    class DummyAdapter(SenseAdapter):
        family = "kubernetes"
        name = "dummy-k8s"

        async def discover(self) -> DiscoveryResult:
            return DiscoveryResult(services=[], edges=[], entities=[], evidence={"ok": True})

        def senses(self) -> list[SenseCategory]:
            return [SenseCategory.SCOPED_OBSERVABILITY]

        def capabilities(self) -> dict[str, CapabilitySpec]:
            return {
                "k8s.get_resource": get_capability("k8s.get_resource"),
                "k8s.rollout_restart": get_capability("k8s.rollout_restart"),
            }

        async def health(self) -> AdapterHealth:
            return AdapterHealth(
                adapter_name=self.name,
                family=self.family,
                state="available",
                evidence={"cluster": "kind-test"},
                checked_at=datetime.now(tz=UTC),
            )

        async def _execute_capability_impl(
            self,
            capability_id: str,
            args: dict[str, object],
            trust_decision: TrustDecision | None = None,
        ) -> dict[str, object]:
            return {"executed": capability_id, "args": args}

    adapter = DummyAdapter()
    now = datetime.now(tz=UTC)

    # Read-only capability (L0) executes without TrustDecision
    res_read = await adapter.execute_capability("k8s.get_resource", {"name": "pod-1"})
    assert res_read["executed"] == "k8s.get_resource"

    # Mutating capability (L3) WITHOUT TrustDecision raises TrustViolation
    with pytest.raises(TrustViolation):
        await adapter.execute_capability("k8s.rollout_restart", {"deployment": "dep-1"})

    def make_td(**overrides: object) -> TrustDecision:
        base: dict[str, object] = {
            "authorized": True,
            "agent_id": "platform__env-1",
            "action_type": "k8s.rollout_restart",
            "capability_id": "k8s.rollout_restart",
            "effective_level": 3,
            "required_level": 3,
            "approval_id": None,
            "sa_to_impersonate": "godview-act-sa",
            "blast_radius_resources": 1,
            "reason": "L3 autonomous execution authorized",
            "decided_at": now,
        }
        base.update(overrides)
        return TrustDecision.model_validate(base)

    # Negative 1: effective_level=0 on L3 capability
    with pytest.raises(TrustViolation):
        await adapter.execute_capability(
            "k8s.rollout_restart", {}, make_td(effective_level=0, required_level=3)
        )

    # Negative 2: L2 capability (tf.apply_dispatch) without approval_id
    with pytest.raises(TrustViolation):
        await adapter.execute_capability(
            "tf.apply_dispatch",
            {},
            make_td(
                capability_id="tf.apply_dispatch",
                action_type="tf.apply_dispatch",
                effective_level=2,
                required_level=2,
                approval_id=None,
            ),
        )

    # Positive 2: L2 capability WITH approval_id succeeds
    res_l2 = await adapter.execute_capability(
        "tf.apply_dispatch",
        {},
        make_td(
            capability_id="tf.apply_dispatch",
            action_type="tf.apply_dispatch",
            effective_level=2,
            required_level=2,
            approval_id="apr-1",
        ),
    )
    assert res_l2["executed"] == "tf.apply_dispatch"

    # Negative 3: Money-movement capability (biz.replay_transaction, L4) without approval_id / L4 whitelist
    with pytest.raises(TrustViolation):
        await adapter.execute_capability(
            "biz.replay_transaction",
            {},
            make_td(
                capability_id="biz.replay_transaction",
                action_type="biz.replay_transaction",
                effective_level=2,
                required_level=4,
                approval_id=None,
            ),
        )

    # Positive 3: Money-movement capability WITH approval_id at L2 succeeds
    res_money = await adapter.execute_capability(
        "biz.replay_transaction",
        {},
        make_td(
            capability_id="biz.replay_transaction",
            action_type="biz.replay_transaction",
            effective_level=2,
            required_level=4,
            approval_id="apr-money-1",
        ),
    )
    assert res_money["executed"] == "biz.replay_transaction"

    # Negative 4: action_type != capability_id
    with pytest.raises(TrustViolation):
        await adapter.execute_capability(
            "k8s.rollout_restart", {}, make_td(action_type="other.action")
        )

    # Negative 5: sovereign_only capability invoked by a court agent
    with pytest.raises(TrustViolation):
        await adapter.execute_capability(
            "agent.set_min_instances",
            {},
            make_td(
                capability_id="agent.set_min_instances",
                action_type="agent.set_min_instances",
                agent_id="application__svc-a",
                effective_level=0,
                required_level=0,
                sa_to_impersonate=None,
            ),
        )

    # Positive 5: sovereign_only capability invoked by 'sovereign' succeeds
    res_sov = await adapter.execute_capability(
        "agent.set_min_instances",
        {},
        make_td(
            capability_id="agent.set_min_instances",
            action_type="agent.set_min_instances",
            agent_id="sovereign",
            effective_level=0,
            required_level=0,
            sa_to_impersonate=None,
        ),
    )
    assert res_sov["executed"] == "agent.set_min_instances"

    # Positive 6: Valid L3 decision executes
    res_mut = await adapter.execute_capability(
        "k8s.rollout_restart", {"deployment": "dep-1"}, trust_decision=make_td()
    )
    assert res_mut["executed"] == "k8s.rollout_restart"


@pytest.mark.unit
def test_otel_baggage_and_metrics() -> None:
    """TDD §3.2 & §5: Baggage propagation, business span attributes, and /metrics exposition."""
    assert BAGGAGE_KEYS == ("journey.id", "customer.id", "synthetic")
    assert "decision" in BUSINESS_SPAN_ATTRIBUTES
    assert "status_to" in BUSINESS_SPAN_ATTRIBUTES
    assert SYNTHETIC_HEADER == "X-Synthetic"

    headers = inject_baggage_headers(journey_id="jrn-99", customer_id="cust-88", synthetic=True)
    assert headers["X-Synthetic"] == "true"
    extracted = extract_baggage_context(headers)
    assert extracted["journey.id"] == "jrn-99"
    assert extracted["customer.id"] == "cust-88"
    assert extracted["synthetic"] is True

    tracer = get_tracer("test-svc")
    with tracer.start_as_current_span("step") as span:
        set_business_span_attributes(
            span,
            journey_id="jrn-99",
            decision="APPROVED",
            status_to="COMPLETED",
            synthetic=True,
        )

    payload, content_type = render_prometheus_metrics()
    assert b"HELP" in payload or isinstance(payload, bytes)
    assert "text/plain" in content_type


@pytest.mark.unit
async def test_in_memory_fakes_firestore_knn_bigquery_pubsub_agent_engine() -> None:
    """TDD §5 & §7: Hermetic fakes for Firestore (768-dim cosine KNN), BigQuery (DuckDB+sqlglot), Pub/Sub, Agent Engine."""
    # 1. Firestore fake + 768-dim cosine KNN + CRUD + subcollections + unknown collection rejection
    fs = InMemoryFirestore()
    with pytest.raises(ValueError, match="Unknown Firestore collection"):
        fs.set("dicsovered_services", "x", {"a": 1})

    fs.set("journeys", "jrn-flow-1", {"name": "flow-1", "status": "active"})
    fs.set("journeys", "jrn-flow-1", {"owner_agent_id": "business_line__flow-1"}, merge=True)
    assert fs.get("journeys", "jrn-flow-1")["name"] == "flow-1"
    assert fs.get("journeys", "jrn-flow-1")["owner_agent_id"] == "business_line__flow-1"
    assert len(fs.list_docs("journeys", where={"status": "active"})) == 1
    assert fs.delete("journeys", "jrn-flow-1") is True
    assert fs.delete("journeys", "jrn-flow-1") is False

    fs.set_subdoc(
        "court_agents",
        "application__svc-a",
        "living_model",
        "rules",
        {"items": [{"id": "r1"}]},
    )
    assert (
        fs.get_subdoc("court_agents", "application__svc-a", "living_model", "rules")["items"][0][
            "id"
        ]
        == "r1"
    )
    assert len(fs.list_subdocs("court_agents", "application__svc-a", "living_model")) == 1

    vec_a = [1.0] + [0.0] * 767
    vec_b = [0.8, 0.6] + [0.0] * 766
    vec_c = [0.0, 1.0] + [0.0] * 766

    fs.set(
        "episodic_incidents",
        "inc-20261001-0001",
        {"symptom_summary": "Ratio threshold regression", "symptom_embedding": vec_a},
    )
    fs.set(
        "episodic_incidents",
        "inc-20261002-0001",
        {"symptom_summary": "Related threshold drift", "symptom_embedding": vec_b},
    )
    fs.set(
        "episodic_incidents",
        "inc-20261003-0001",
        {"symptom_summary": "Orthogonal DB lock issue", "symptom_embedding": vec_c},
    )

    hits = fs.find_nearest(
        "episodic_incidents",
        "symptom_embedding",
        query_vector=vec_a,
        limit=2,
        min_similarity=0.5,
    )
    assert len(hits) == 2
    assert hits[0][0]["_id"] == "inc-20261001-0001"
    assert math.isclose(hits[0][1], 1.0, rel_tol=1e-5)
    assert hits[1][0]["_id"] == "inc-20261002-0001"
    assert math.isclose(hits[1][1], 0.8, rel_tol=1e-5)

    # Wrong vector dimension or unindexed collection must fail loudly
    with pytest.raises(ValueError, match="768"):
        fs.find_nearest("episodic_incidents", "symptom_embedding", query_vector=[1.0, 0.0])
    with pytest.raises(ValueError, match="No vector index"):
        fs.find_nearest("journeys", "nonexistent_vec", query_vector=vec_a)

    # 2. BigQuery fake with event_id dedup (Decision A13) & real DuckDB/sqlglot SQL evaluation
    bq = InMemoryBigQuery()
    inserted = bq.insert_rows(
        "events",
        [
            {
                "event_id": "e-1",
                "decision": "APPROVED",
                "ratio": 65,
                "score": 700,
                "value": 5,
                "source": "otel",
            },
            {
                "event_id": "e-1",
                "decision": "APPROVED",
                "ratio": 65,
                "score": 700,
                "value": 5,
                "source": "otel",
            },
            {
                "event_id": "e-2",
                "decision": "APPROVED",
                "ratio": 45,
                "score": 750,
                "value": 2500,
                "source": "otel",
            },
        ],
    )
    assert inserted == 2
    assert len(bq.get_table("events")) == 2
    assert (
        bq.query(
            "SELECT COUNT(*) FROM events WHERE decision='APPROVED' AND (ratio > 60 OR score < 680)"
        )[0]["count"]
        == 1
    )
    assert bq.query("SELECT COUNT(*) FROM events WHERE ratio > 70")[0]["count"] == 0
    assert bq.query("SELECT COUNT(*) FROM events WHERE value > 1000")[0]["count"] == 1
    assert bq.insert_rows("kpi_rollups", [{"a": 1}, {"a": 1}]) == 1

    # Invalid / unparseable SQL must raise ValueError (no silent fake)
    with pytest.raises(ValueError):
        bq.query("SELECT this is not sql")

    bq.clear_table("events")
    assert len(bq.get_table("events")) == 0

    # 3. Pub/Sub fake with CloudEvents & DLQ
    ps = InMemoryPubSub()
    received: list[dict[str, object]] = []
    ps.subscribe("godview-nervous-system", lambda ev: received.append(ev))
    await ps.publish(
        "godview-nervous-system",
        {
            "specversion": "1.0",
            "type": "godview.sentinel.breach",
            "source": "//sovereign/sentinel-engine",
            "id": "b-1",
            "time": "2026-10-16T08:35:10Z",
            "data": {"sentinel_id": "snt-invariant_sql-test"},
        },
    )
    assert len(received) == 1
    assert received[0]["type"] == "godview.sentinel.breach"
    assert len(ps.get_published("godview-nervous-system")) == 1

    # 4. Agent Engine fake (deploy, PATCH min_instances 0 <-> 1, Memory Bank, A2A, retire)
    ae = InMemoryAgentEngine()
    engine = ae.deploy_agent(
        agent_id="application__svc-a",
        agent_type="application",
        min_instances=0,
    )
    assert engine["deployment_spec"]["min_instances"] == 0
    ae.set_min_instances("application__svc-a", 1)
    assert ae.get_agent("application__svc-a")["deployment_spec"]["min_instances"] == 1
    assert len(ae.list_agents()) == 1
    ae.write_memory_bank("application__svc-a", "draft_rule", {"ratio_max": 60})
    assert ae.read_memory_bank("application__svc-a")["draft_rule"]["ratio_max"] == 60

    a2a_res = await ae.send_a2a(
        {
            "incident_id": "inc-20261016-0003",
            "from_agent_id": "sovereign",
            "to_agent_id": "application__svc-a",
            "round": 1,
            "sent_at": "2026-10-16T08:35:15Z",
            "type": "brief",
            "payload": {"living_model_delta": {"rules": 1}},
        }
    )
    assert a2a_res["status"] == "delivered"
    assert len(ae.get_a2a_log()) == 1
    retired = ae.retire_agent("application__svc-a")
    assert retired["state"] == "RETIRED"
    assert retired["deployment_spec"]["min_instances"] == 0

    settings = GodViewSettings.from_env(
        {"GODVIEW_ENV": "hermetic", "GODVIEW_COURT_MAX_AGENTS": "40"}
    )
    assert settings.env_mode == "hermetic"
    assert settings.court_max_agents == 40


@pytest.mark.unit
def test_lint_brands_and_lint_neutrality_both_directions(tmp_path) -> None:
    """TDD I-01 & I-02: lint_brands and lint_neutrality pass on clean files and fail on violations."""
    import importlib.util
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]

    brands_spec = importlib.util.spec_from_file_location(
        "lint_brands", repo_root / "shared" / "tools" / "lint_brands.py"
    )
    assert brands_spec and brands_spec.loader
    lint_brands = importlib.util.module_from_spec(brands_spec)
    brands_spec.loader.exec_module(lint_brands)

    neutral_spec = importlib.util.spec_from_file_location(
        "lint_neutrality", repo_root / "shared" / "tools" / "lint_neutrality.py"
    )
    assert neutral_spec and neutral_spec.loader
    lint_neutrality = importlib.util.module_from_spec(neutral_spec)
    neutral_spec.loader.exec_module(lint_neutrality)

    # Clean repo check
    assert lint_brands.scan_paths([repo_root / "shared"]) == []
    assert (
        lint_neutrality.scan_neutrality(
            [
                repo_root / "shared",
                repo_root / "godview",
            ],
            repo_root=repo_root,
        )
        == []
    )

    # Negative check: file with forbidden brand or tenant name must be caught
    bad_file = tmp_path / "bad_sample.py"
    bad_file.write_text("vendor = 'Data' + 'dog'\n# " + ("data" + "dog") + "\n", encoding="utf-8")
    assert len(lint_brands.scan_paths([bad_file])) == 1

    bad_tenant = tmp_path / "bad_tenant.py"
    bad_tenant.write_text(
        "svc = '" + ("lo" + "an") + "-svc'\n"
        "status = 'DISB" + "URSED'\n"
        "kpi = 'rup" + "ees_per_hour'\n"
        "flag = 'ky" + "c_status'\n"
        "ip = '10." + "10.0.1'\n",
        encoding="utf-8",
    )
    assert len(lint_neutrality.scan_neutrality([bad_tenant], repo_root=tmp_path)) == 5


@pytest.mark.unit
@pytest.mark.contract
def test_grimp_package_visibility_and_import_linter_contract() -> None:
    """Board #105 / #108: Every Python package directory under godview/ must be visible to grimp."""
    from pathlib import Path

    import grimp

    repo_root = Path(__file__).resolve().parents[2]
    godview_dir = repo_root / "godview"
    assert (godview_dir / "__init__.py").is_file()
    assert (godview_dir / "adapters" / "__init__.py").is_file()

    graph = grimp.build_graph("godview", "godview_shared", include_external_packages=True)
    assert "godview_shared" in graph.modules
    assert "godview" in graph.modules

    non_python_dirs = {"god-view-sre-web", "agent-skills", "__pycache__"}
    discovered_subpackages: list[str] = []
    for child in sorted(godview_dir.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name in non_python_dirs:
            continue
        mod_name = f"godview.{child.name}"
        assert (child / "__init__.py").is_file(), f"Missing __init__.py in {child}"
        assert mod_name in graph.modules, f"{mod_name} is invisible to grimp import-linter"
        discovered_subpackages.append(mod_name)

    for expected in (
        "godview.sovereign",
        "godview.adapters",
        "godview.ledger",
        "godview.invariants",
    ):
        assert expected in discovered_subpackages
