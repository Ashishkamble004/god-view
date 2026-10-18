"""Authoritative Pydantic v2 data and protocol contracts for God-View v4.

Covers TDD §2.1 (9 tenant business tables), §2.2 (4 BigQuery ledger tables),
§2.3 (13 Firestore collections + 4 vector indexes), §2.4 (JSON contracts:
EvidencePack, ApprovalCard, TrustRecord, SentinelDefinition, INVARIANT.md,
SKILL.md, AuditRecord), §3.5 (6 A2A message types), §3.6 (CloudEvents 1.0),
and Coordinator Decision A3 (CapabilityInvokeRequest/Response) & A10 (whitelisted_by).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from godview_shared.capabilities import CAPABILITY_REGISTRY

# ==============================================================================
# §2.1 Tenant Business Database (9 tables; zero telemetry tables — TDD I-04)
# ==============================================================================

TENANT_BUSINESS_TABLES: tuple[str, ...] = ()
CHAOS_SCENARIO_IDS: tuple[str, ...] = ()

FORBIDDEN_TELEMETRY_TABLES: frozenset[str] = frozenset(
    {
        "service_metric_rollups",
        "otel_trace_spans",
    }
)


# ==============================================================================
# §2.2 Business Event Ledger: BigQuery dataset godview_ledger (4 tables)
# ==============================================================================

LEDGER_TABLES: tuple[str, ...] = (
    "events",
    "state_transitions",
    "journeys",
    "kpi_rollups",
)

LedgerEventSource = Literal["otel", "cdc", "probe"]
StateTransitionEntity = str
JourneyConformance = Literal[
    "CONFORMANT",
    "SKIPPED",
    "REORDERED",
    "LOOPED",
    "STALLED",
]
KpiName = str


class LedgerEventRow(BaseModel):
    event_id: str
    event_time: datetime | str
    env_id: str
    service: str
    operation: str
    event_type: str
    entity_type: str | None = None
    entity_id: str | None = None
    state_from: str | None = None
    state_to: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    numeric_attributes: dict[str, float] = Field(default_factory=dict)
    currency: str | None = None

    journey_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None

    http_status: int | None = None
    duration_ms: int | None = None
    is_synthetic: bool = False
    source: LedgerEventSource = "otel"


class StateTransitionRow(BaseModel):
    transition_id: str
    transition_time: datetime | str
    env_id: str
    entity: str
    entity_id: str
    journey_id: str
    status_from: str | None = None
    status_to: str | None = None
    state_from: str | None = None
    state_to: str | None = None
    actor_service: str
    source: Literal["otel", "cdc"] | str = "otel"
    dwell_ms: int | None = None
    is_synthetic: bool = False
    attributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _shim_state_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "status_from" in data and not data.get("state_from"):
                data["state_from"] = data["status_from"]
            elif "state_from" in data and not data.get("status_from"):
                data["status_from"] = data["state_from"]
            if "status_to" in data and not data.get("state_to"):
                data["state_to"] = data["status_to"]
            elif "state_to" in data and not data.get("status_to"):
                data["status_to"] = data["state_to"]
        return data


class JourneyStep(BaseModel):
    seq: int
    service: str
    operation: str
    at: datetime
    status: str
    duration_ms: int


class LedgerJourneyRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    journey_id: str
    journey_name: str
    started_at: datetime
    ended_at: datetime | None = None
    env_id: str
    entity_id: str | None = None
    customer_id: str | None = None
    steps: list[JourneyStep] = Field(default_factory=list)
    observed_sequence: str
    conformance: JourneyConformance
    final_status: str
    total_value: Decimal | float = Decimal("0")
    total_duration_ms: int = 0
    is_synthetic: bool = False
    journey_model_version: str = "1.0"

    @model_validator(mode="before")
    @classmethod
    def _shim_entity_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "customer_id" in data and "entity_id" not in data:
                data["entity_id"] = data["customer_id"]
            elif "entity_id" in data and "customer_id" not in data:
                data["customer_id"] = data["entity_id"]
        return data


class KpiRollupRow(BaseModel):
    window_start: datetime
    window_end: datetime
    env_id: str
    journey_name: str
    kpi: KpiName
    segment: str = "all"
    value: Decimal | float
    baseline_value: Decimal | float
    zscore: Decimal | float
    cusum: Decimal | float
    sample_n: int
    breach: bool = False


# ==============================================================================
# §2.3 & §2.4 Firestore Native DB (13 collections)
# ==============================================================================

FIRESTORE_COLLECTIONS: tuple[str, ...] = (
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
)

FIRESTORE_VECTOR_INDEXES: tuple[tuple[str, str], ...] = (
    ("discovered_services", "purpose_embedding"),
    ("invariants", "statement_embedding"),
    ("episodic_incidents", "symptom_embedding"),
    ("procedural_skills", "trigger_embedding"),
)

DiscoveredServiceKind = Literal[
    "cloud_run",
    "gke_workload",
    "gce_instance",
    "gce_vm",
    "cloud_sql",
    "lb_frontend",
    "backend_service",
    "security_policy",
    "api_gateway",
    "pubsub_topic",
    "pubsub_subscription",
    "gcs_bucket",
    "bucket",
    "bigquery_dataset",
    "firestore_db",
    "secret",
    "external",
    "agent",
    "reasoning_engine",
    "other",
]
ServiceClassification = Literal["journey_linked", "other_read_only"]
CourtAgentType = Literal["application", "data", "business_line", "change", "platform"]
CourtAgentState = Literal[
    "perceived",
    "spawning",
    "learning",
    "sentinel_compiled",
    "sleeping",
    "awake",
    "retiring",
    "archived",
]
SentinelClass = Literal[
    "lb_requests",
    "posture",
    "probe",
    "pubsub_health",
    "golden_signals",
    "journey_conformance",
    "kpi_changepoint",
    "invariant",
    "invariant_sql",
    "probe_plan",
    "golden_signal_gate",
    "drift_diff",
    "budget",
    "discovery",
]
SentinelState = Literal["shadow", "enforced", "paused", "retired"]
InvariantStatus = Literal["proposed", "shadow", "enforced", "retired"]
InvariantSeverity = Literal["sev1", "sev2", "sev3"]
InvariantProvenance = Literal["code", "policy", "observed"]
InvariantDialect = Literal["bigquery", "postgres", "python"]


class BusinessContextEvidence(BaseModel):
    kind: str = Field(default="openapi", description="openapi | env | trace | log | code | schema | doc")
    ref: str = Field(default="", description="Reference pointer (e.g. HTTP route, span name, metric)")
    excerpt: str = Field(default="", description="Short evidence snippet or description")


class BusinessContextJourney(BaseModel):
    name: str = Field(default="", description="Journey or process flow name")
    steps: list[dict[str, str] | str] = Field(default_factory=list, description="Ordered steps {service, operation} or operation string")
    confidence: float = Field(default=0.0, description="Confidence score 0.0-1.0")
    evidence: str = Field(default="", description="Evidence supporting this journey")


class BusinessContextKpi(BaseModel):
    name: str = Field(default="", description="KPI name")
    definition: str = Field(default="", description="Metric formula or SQL query over ledger")
    unit: str = Field(default="", description="Unit of measurement")
    confidence: float = Field(default=0.0, description="Confidence score 0.0-1.0")
    evidence: str = Field(default="", description="Evidence supporting this KPI")


class BusinessContextInvariant(BaseModel):
    statement: str = Field(default="", description="Invariant assertion statement")
    check: str = Field(default="", description="SQL / CEL / python-safe verification check")
    severity: str = Field(default="sev2", description="sev1 | sev2 | sev3")
    confidence: float = Field(default=0.0, description="Confidence score 0.0-1.0")
    evidence: str = Field(default="", description="Evidence supporting this invariant")


class BusinessContext(BaseModel):
    role_summary: str = ""
    domain: str = ""
    capabilities: list[str] = Field(default_factory=list)
    upstream: list[str] = Field(default_factory=list)
    downstream: list[str] = Field(default_factory=list)
    journeys: list[BusinessContextJourney] = Field(default_factory=list)
    kpis: list[BusinessContextKpi] = Field(default_factory=list)
    invariants: list[BusinessContextInvariant] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    evidence: list[BusinessContextEvidence] = Field(default_factory=list)
    inferred_by: str = ""
    inferred_at: datetime | str | None = None


class Posture(BaseModel):
    ingress: str | None = None
    iap: bool | None = None
    invoker_iam: bool | None = None
    waf: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class VerificationCheck(BaseModel):
    check: str
    passed: bool
    evidence: Any = None


class ExecutionResult(BaseModel):
    ok: bool
    before: Any = None
    after: Any = None
    verification: list[VerificationCheck] = Field(default_factory=list)
    reverted: bool = False
    revert_error: str | None = None
    error: str | None = None


class ProposedAction(BaseModel):
    capability_id: str
    args: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    confidence: float = 0.0


class Diagnosis(BaseModel):
    hypothesis: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[dict[str, Any]] | dict[str, Any] | list[str] = Field(default_factory=list)
    proposed_actions: list[ProposedAction | dict[str, Any]] = Field(default_factory=list)
    root_cause_category: str | None = None
    citations: list[str] = Field(default_factory=list)


def _validate_embedding(vec: list[float] | None) -> list[float] | None:
    if vec is not None and len(vec) != 768:
        raise ValueError(f"Vector embedding must have 768 dimensions (got {len(vec)})")
    return vec


class DiscoveredServiceDoc(BaseModel):
    doc_id: str | None = None
    env_id: str
    name: str
    kind: DiscoveredServiceKind
    adapter_family: str
    locator: str
    labels: dict[str, str] = Field(default_factory=dict)
    inferred_role: str = ""
    role_confidence: float = 0.0
    classification: ServiceClassification = "other_read_only"
    routes: list[dict[str, Any]] = Field(default_factory=list)
    metrics_endpoint: str | None = None
    repo_ref: str | None = None
    tf_address: str | None = None
    owner_agent_id: str | None = None
    first_seen: datetime | str
    last_seen: datetime | str
    status: Literal["active", "gone"] = "active"
    purpose_embedding: list[float] | None = None
    business_context: BusinessContext | None = None
    posture: Posture | None = None

    @field_validator("purpose_embedding")
    @classmethod
    def check_dim(cls, v: list[float] | None) -> list[float] | None:
        return _validate_embedding(v)


DiscoveredEdgeEvidence = Literal[
    "trace",
    "code",
    "config",
    "k8s",
    "lb_route",
    "env_ref",
    "pubsub_push",
    "k8s_service",
    "db_conn",
    "openapi",
]


class DiscoveredEdgeDoc(BaseModel):
    doc_id: str | None = None
    src: str
    dst: str
    protocol: str = "http"
    evidence: DiscoveredEdgeEvidence | str = "trace"
    evidence_kind: str | None = None
    rps: float | None = None
    error_ratio: float | None = None
    confidence: float = 0.0
    call_rate_baseline: float = 0.0
    first_seen: datetime | str
    last_seen: datetime | str


class DiscoveredEntityDoc(BaseModel):
    doc_id: str | None = None
    name: str
    correlation_keys: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    owning_service_id: str | None = None
    states: list[str] = Field(default_factory=list)
    transitions: list[dict[str, Any]] = Field(default_factory=list)
    rules_ref: list[str] = Field(default_factory=list)
    provenance: str = ""


class JourneyDoc(BaseModel):
    model_config = ConfigDict(extra="ignore")

    doc_id: str | None = None
    name: str
    steps: list[dict[str, Any]] | list[str] = Field(default_factory=list)
    directly_follows: dict[str, list[str]] = Field(default_factory=dict)
    intended_order_source: str = ""
    conformance_rules: list[dict[str, Any]] = Field(default_factory=list)
    funnel_kpis: list[str] = Field(default_factory=list)
    impact_model: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    evidence: list[dict[str, Any]] | list[str] = Field(default_factory=list)
    inferred_by: str = ""
    inferred_at: str | None = None
    owner_agent_id: str | None = None
    status: str = "active"


class LivingModelSubdoc(BaseModel):
    section: Literal[
        "rules",
        "state_machines",
        "decision_boundaries",
        "routes",
        "dependencies",
        "feature_flags",
        "commit_diffs",
    ]
    items: list[dict[str, Any]] = Field(default_factory=list)
    updated_at: datetime | str | None = None


class CourtAgentDoc(BaseModel):
    doc_id: str | None = None
    agent_type: CourtAgentType
    subject_ref: str
    reasoning_engine: str | None = None
    a2a_card_url: str | None = None
    state: CourtAgentState = "perceived"
    warmth_min_instances: Literal[0, 1] = 0
    memory_bank_id: str | None = None
    namespace: str
    token_budget_daily: Decimal | float
    tokens_spent_today: int = 0
    usd_spent_today: Decimal | float = Decimal("0.00")
    last_wake_at: datetime | str | None = None
    last_reflection_at: datetime | str | None = None
    wakes_7d: int = 0
    spawned_at: datetime | str
    retired_at: datetime | str | None = None


class TrustCounters(BaseModel):
    recommendations_correct: int = 0
    approved_executions: int = 0
    verified_outcomes: int = 0
    regressions: int = 0
    business_successes: int = 0


class TrustThresholds(BaseModel):
    n1: int = 3
    n2: int = 5
    n3: int = 10


class TrustHistoryEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    at: datetime | str
    from_level: int = Field(alias="from", ge=0, le=4)
    to: int = Field(ge=0, le=4)
    reason: str


class TrustRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    agent_id: str
    action_type: str
    level: int = Field(default=0, ge=0, le=4)
    counters: TrustCounters = Field(default_factory=TrustCounters)
    thresholds: TrustThresholds = Field(default_factory=TrustThresholds)
    whitelisted: bool = False
    whitelisted_by: str | None = None
    last_promoted_at: datetime | str | None = None
    last_demoted_at: datetime | str | None = None
    demotion_reason: str | None = None
    history: list[TrustHistoryEntry] = Field(default_factory=list)


class SentinelCheck(BaseModel):
    dialect: InvariantDialect = "bigquery"
    statement: str | None = None
    spec: dict[str, Any] | None = None
    window_s: int | None = None


class SentinelBaseline(BaseModel):
    model: str = "same_hour"
    window_days: int = 7
    expected: float | None = None
    stddev: float | None = None


class SentinelWakeCondition(BaseModel):
    operator: Literal[">=", ">", "<=", "<", "==", "!="] = ">="
    threshold: float = 1.0
    consecutive: int = 1
    window_s: int = 300
    target_agent_id: str | None = None


class SentinelDefinition(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    sentinel_id: str
    sentinel_class: SentinelClass = Field(alias="class")
    source_ref: str
    check: SentinelCheck
    cadence_s: int = 10
    baseline: SentinelBaseline = Field(default_factory=SentinelBaseline)
    wake_condition: SentinelWakeCondition
    target_agent_id: str
    state: SentinelState = "enforced"
    compiled_by: str
    compiled_at: datetime | str
    last_run_at: datetime | str | None = None
    last_result: dict[str, Any] | None = None
    consecutive_breaches: int = 0


class InvariantScope(BaseModel):
    service: str | None = None
    entity: str | None = None
    journey: str | None = None


class InvariantCheckSpec(BaseModel):
    dialect: InvariantDialect
    statement: str
    window_s: int = 300


class InvariantWakeCondition(BaseModel):
    operator: Literal[">=", ">", "<=", "<", "==", "!="] = ">="
    threshold: float = 1.0
    consecutive: int = 1
    target_agent_id: str


class InvariantShadowStats(BaseModel):
    runs: int = 0
    breaches: int = 0
    false_positive_rate: float = 0.0


class InvariantFrontmatter(BaseModel):
    name: str
    scope: InvariantScope
    provenance: InvariantProvenance
    provenance_ref: str
    check: InvariantCheckSpec
    severity: InvariantSeverity
    wake_condition: InvariantWakeCondition
    confidence: float = Field(ge=0.0, le=1.0)
    created_by: str
    version: str | int
    status: InvariantStatus = "proposed"


class InvariantDoc(InvariantFrontmatter):
    doc_id: str | None = None
    body_md: str = ""
    shadow_stats: InvariantShadowStats = Field(default_factory=InvariantShadowStats)
    sentinel_id: str | None = None
    git_path: str | None = None
    git_sha: str | None = None
    statement_embedding: list[float] | None = None

    @field_validator("statement_embedding")
    @classmethod
    def check_dim(cls, v: list[float] | None) -> list[float] | None:
        return _validate_embedding(v)


class SkillTriggerSpec(BaseModel):
    sentinel_classes: list[SentinelClass]
    symptom: str


class SkillStepSpec(BaseModel):
    capability: str
    min_trust_level: int = Field(ge=0, le=4)

    @field_validator("capability")
    @classmethod
    def check_capability_exists(cls, v: str) -> str:
        if v not in CAPABILITY_REGISTRY:
            raise ValueError(
                f"Skill step references unknown capability '{v}' (must be in CAPABILITY_REGISTRY)"
            )
        return v


class SkillFrontmatter(BaseModel):
    name: str
    description: str
    version: str
    created_by: str
    owner_agent_id: str
    provenance: Literal["agent_synthesized", "human_runbook"]
    confidence_score: float = Field(ge=0.0, le=1.0)
    executions_count: int = 0
    avg_mttr_seconds: float = 0.0
    trigger: SkillTriggerSpec
    required_capabilities: list[str]
    steps: list[SkillStepSpec]
    blast_radius: str
    reversal: str
    verification: str

    @field_validator("required_capabilities")
    @classmethod
    def check_required_capabilities(cls, v: list[str]) -> list[str]:
        for cap in v:
            if cap not in CAPABILITY_REGISTRY:
                raise ValueError(f"Unknown required capability '{cap}'")
        return v


class ProceduralSkillDoc(SkillFrontmatter):
    doc_id: str | None = None
    body_md: str = ""
    gcs_uri: str | None = None
    git_path: str | None = None
    last_executed_at: datetime | str | None = None
    trigger_embedding: list[float] | None = None

    @field_validator("trigger_embedding")
    @classmethod
    def check_dim(cls, v: list[float] | None) -> list[float] | None:
        return _validate_embedding(v)


class EvidenceSentinelRef(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str
    sentinel_class: SentinelClass = Field(alias="class")
    source_ref: str


class EvidenceObserved(BaseModel):
    count: int | float
    window_s: int


class EvidenceBreach(BaseModel):
    statement: str
    observed: EvidenceObserved
    first_seen: datetime | str
    consecutive: int


class EvidenceBaseline(BaseModel):
    model: str = "same_hour"
    expected: float
    stddev: float


class EvidenceAffected(BaseModel):
    model_config = ConfigDict(extra="ignore")

    services: list[str] = Field(default_factory=list)
    journeys: list[str] = Field(default_factory=list)
    journey_ids: list[str] = Field(default_factory=list)
    rows: int = 0
    value_at_risk: Decimal | float | int = 0


class EvidenceCorrelatedChange(BaseModel):
    change_event_id: str
    kind: str
    occurred_at: datetime | str
    business_summary: str


class EvidenceGoldenSignals(BaseModel):
    status: Literal["green", "yellow", "amber", "red", "unknown"] = "unknown"
    p95_ms: float | None = None
    error_rate_pct: float | None = None


class EpisodicRecallHit(BaseModel):
    incident_id: str
    similarity: float


class SkillRecallHit(BaseModel):
    skill_slug: str
    similarity: float


class EvidenceRecall(BaseModel):
    episodic: list[EpisodicRecallHit] = Field(default_factory=list)
    skills: list[SkillRecallHit] = Field(default_factory=list)


class EvidenceBudget(BaseModel):
    agent_tokens_remaining: int
    escalation_allowed: bool


class EvidencePack(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    schema_version: Literal["1.0"] = "1.0"
    evidence_pack_id: str
    incident_id: str
    fired_at: datetime | str
    sentinel: EvidenceSentinelRef
    breach: EvidenceBreach
    baseline: EvidenceBaseline
    affected: EvidenceAffected
    correlated_changes: list[EvidenceCorrelatedChange] = Field(default_factory=list)
    golden_signals: EvidenceGoldenSignals
    recall: EvidenceRecall = Field(default_factory=EvidenceRecall)
    budget: EvidenceBudget
    target_agent_id: str
    trust_snapshot: dict[str, int] = Field(default_factory=dict)


class IncidentActionRef(BaseModel):
    audit_id: str


class ConvergenceRecord(BaseModel):
    consensus: float
    rounds: int = Field(ge=1, le=4)
    seconds: float
    escalated_to_argon: bool = False


class EpisodicIncidentDoc(BaseModel):
    model_config = ConfigDict(extra="ignore")

    doc_id: str | None = None
    dedupe_key: str | None = None
    opened_at: datetime | str
    closed_at: datetime | str | None = None
    severity: InvariantSeverity = "sev2"
    symptom_summary: str
    evidence_pack: EvidencePack | dict[str, Any]
    lead_agent_id: str
    participants: list[str] = Field(default_factory=list)
    root_cause: str | None = None
    causal_chain: list[str] = Field(default_factory=list)
    actions: list[IncidentActionRef] = Field(default_factory=list)
    outcome: Literal["resolved", "mitigated", "false_positive", "open"] = "open"
    financial_impact: Decimal | float | int = 0
    affected_journeys: list[str] = Field(default_factory=list)
    diagnoses: list[Diagnosis] = Field(default_factory=list)
    convergence: ConvergenceRecord | None = None
    skills_used: list[str] = Field(default_factory=list)
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    symptom_embedding: list[float] | None = None

    @field_validator("symptom_embedding")
    @classmethod
    def check_dim(cls, v: list[float] | None) -> list[float] | None:
        return _validate_embedding(v)


class CanaryWindow(BaseModel):
    window_start: datetime | str
    window_end: datetime | str
    verdict: Literal["pending", "passed", "failed"] = "pending"


class ChangeEventDoc(BaseModel):
    doc_id: str | None = None
    repo: str
    kind: Literal[
        "commit",
        "pr_opened",
        "pr_merged",
        "ci_run",
        "deploy",
        "audit_config_change",
        "tfstate_change",
        "operator_note",
    ]
    sha: str | None = None
    pr_number: int | None = None
    author: str | None = None
    business_summary: str = ""
    risk_score: float = 0.0
    affected_services: list[str] = Field(default_factory=list)
    affected_journeys: list[str] = Field(default_factory=list)
    canary: CanaryWindow | None = None
    linked_incident_ids: list[str] = Field(default_factory=list)
    occurred_at: datetime | str


class CostFindingDoc(BaseModel):
    doc_id: str | None = None
    resource: str
    finding_type: Literal["idle_reasoning_engine", "oversized", "unused", "budget_threshold"]
    monthly_usd_estimate: Decimal | float
    recommendation: str
    status: Literal["open", "accepted", "dismissed"] = "open"
    found_by: str
    found_at: datetime | str


class ApprovalCard(BaseModel):
    model_config = ConfigDict(extra="ignore")

    approval_id: str
    incident_id: str
    agent_id: str
    action_type: str
    capability: str
    target_resource: str
    trust_level_required: int = Field(ge=1, le=4)
    narrative: str
    evidence_pack_id: str
    impact_amount: Decimal | float | int = 0
    currency: str = "USD"
    financial_impact: Decimal | float | int = 0
    reversal_recipe: dict[str, Any]
    requested_at: datetime | str
    expires_at: datetime | str
    decision: Literal["pending", "approved", "rejected", "snoozed", "expired"] = "pending"
    decided_by: str | None = None
    decided_at: datetime | str | None = None
    channel: Literal["chat", "cockpit", "email"] = "cockpit"
    audit_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _shim_impact_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "impact_amount" in data and "financial_impact" not in data:
                data["financial_impact"] = data["impact_amount"]
            elif "financial_impact" in data and "impact_amount" not in data:
                data["impact_amount"] = data["financial_impact"]
        return data


class AuditApprover(BaseModel):
    principal: str
    channel: str
    at: datetime | str


class AuditRecord(BaseModel):
    audit_id: str
    incident_id: str
    agent_id: str
    trust_level: int = Field(ge=0, le=4)
    action_type: str
    capability: str
    target_resource: str
    approver: AuditApprover | None = None
    evidence_pack_id: str
    sa_used: str = "godview-act-sa"
    executed_at: datetime | str
    result: Literal["success", "failed", "reverted"]
    verification: dict[str, Any] = Field(default_factory=dict)
    reversal_recipe: dict[str, Any] = Field(default_factory=dict)
    reversed_at: datetime | str | None = None


# ==============================================================================
# TrustGate & Capability Invocation Contracts (TDD I-06 & Decision A3)
# ==============================================================================


class TrustViolation(Exception):
    """Raised when a mutating capability is invoked without authorization or breaks a trust invariant."""


class TrustDecision(BaseModel):
    authorized: bool
    agent_id: str
    action_type: str
    capability_id: str
    effective_level: int = Field(ge=0, le=4)
    required_level: int = Field(ge=0, le=4)
    approval_id: str | None = None
    sa_to_impersonate: str | None = None
    blast_radius_resources: int = 1
    reason: str
    decided_at: datetime | str


class CapabilityInvokeRequest(BaseModel):
    """Request body for POST /v1/capabilities/{capability_id}:invoke (Decision A3)."""

    model_config = ConfigDict(extra="ignore")

    agent_id: str
    incident_id: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    target_resource: str | None = None
    blast_radius_resources: int = 1
    approval_id: str | None = None
    evidence_pack_id: str | None = None
    reversal_recipe: dict[str, Any] | None = None
    novel_skill_first_step: bool = False
    financial_impact: Decimal | float | int = 0
    narrative: str | None = None


class CapabilityInvokeResponse(BaseModel):
    """Response from POST /v1/capabilities/{capability_id}:invoke."""

    capability_id: str
    status: Literal["executed", "approval_required"]
    result: Any = None
    audit_id: str | None = None
    approval_id: str | None = None
    trust_decision: TrustDecision


# ==============================================================================
# §3.5 A2A Message Types (6 message types + common envelope)
# ==============================================================================

A2AMessageType = Literal["wake", "interrogate", "brief", "report", "critique", "handoff"]


class A2ABaseEnvelope(BaseModel):
    incident_id: str
    from_agent_id: str
    to_agent_id: str
    round: int = Field(ge=1, le=4)
    sent_at: datetime | str


class A2AWakePayload(BaseModel):
    evidence_pack: EvidencePack


class A2AInterrogatePayload(BaseModel):
    question: str
    scope: str | dict[str, Any]
    deadline_s: int = Field(ge=1, le=90)


class A2ABriefPayload(BaseModel):
    living_model_delta: dict[str, Any]


class RemediationPlanStep(BaseModel):
    step: int
    capability: str
    target: str
    trust_level_required: int = Field(ge=0, le=4)
    reversal: dict[str, Any]


class A2AReportPayload(BaseModel):
    root_cause: str
    causal_chain: list[str]
    plan: list[RemediationPlanStep]
    confidence: float = Field(ge=0.0, le=1.0)


class A2ACritiquePayload(BaseModel):
    hypothesis_id: str
    counter_evidence: str | dict[str, Any]
    confidence_delta: float


class A2AHandoffPayload(BaseModel):
    new_lead_agent_id: str
    reason: str


class A2AWakeMessage(A2ABaseEnvelope):
    type: Literal["wake"] = "wake"
    payload: A2AWakePayload


class A2AInterrogateMessage(A2ABaseEnvelope):
    type: Literal["interrogate"] = "interrogate"
    payload: A2AInterrogatePayload


class A2ABriefMessage(A2ABaseEnvelope):
    type: Literal["brief"] = "brief"
    payload: A2ABriefPayload


class A2AReportMessage(A2ABaseEnvelope):
    type: Literal["report"] = "report"
    payload: A2AReportPayload


class A2ACritiqueMessage(A2ABaseEnvelope):
    type: Literal["critique"] = "critique"
    payload: A2ACritiquePayload


class A2AHandoffMessage(A2ABaseEnvelope):
    type: Literal["handoff"] = "handoff"
    payload: A2AHandoffPayload


A2AMessage = Annotated[
    A2AWakeMessage
    | A2AInterrogateMessage
    | A2ABriefMessage
    | A2AReportMessage
    | A2ACritiqueMessage
    | A2AHandoffMessage,
    Field(discriminator="type"),
]

_A2A_ADAPTER: TypeAdapter[A2AMessage] = TypeAdapter(A2AMessage)


def parse_a2a_message(data: dict[str, Any]) -> A2AMessage:
    """Validate and parse any of the 6 A2A message types."""
    return _A2A_ADAPTER.validate_python(data)


class A2ASkill(BaseModel):
    id: str
    name: str
    tags: list[str] = Field(default_factory=list)


class A2ACapabilities(BaseModel):
    streaming: bool = False
    pushNotifications: bool = False


class A2AAgentCard(BaseModel):
    name: str
    version: str = "4.0.0"
    description: str
    url: str
    capabilities: A2ACapabilities = Field(default_factory=A2ACapabilities)
    skills: list[A2ASkill] = Field(default_factory=list)


# ==============================================================================
# §3.6 Nervous System CloudEvents (CloudEvents 1.0 JSON)
# ==============================================================================

ALLOWED_CLOUDEVENT_TYPES: frozenset[str] = frozenset(
    {
        # sensor.*
        "godview.sensor.alert",
        "godview.sensor.uptime",
        "godview.sensor.log_metric",
        # webhook.github.*
        "godview.webhook.github.push",
        "godview.webhook.github.pull_request",
        "godview.webhook.github.workflow_run",
        # audit.*
        "godview.audit.config_change",
        "godview.audit.iam_change",
        # cdc.*
        "godview.cdc.schema_change",
        "godview.cdc.row_batch",
        # schedule.*
        "godview.schedule.reflection",
        "godview.schedule.pause",
        "godview.schedule.resume",
        "godview.schedule.probe",
        # sentinel.*
        "godview.sentinel.breach",
        "godview.sentinel.recovered",
        # court.*
        "godview.court.spawn",
        "godview.court.retire",
        "godview.court.warm",
        # operator.*
        "godview.operator.kill_switch",
        "godview.operator.hibernate",
        "godview.operator.note",
    }
)


class NervousSystemEvent(BaseModel):
    """CloudEvents 1.0 envelope for Pub/Sub topic godview-nervous-system (TDD §3.6)."""

    specversion: Literal["1.0"] = "1.0"
    type: str
    source: str
    id: str
    time: datetime | str
    subject: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    godviewenvid: str | None = None
    godviewsynthetic: bool | None = None

    @model_validator(mode="after")
    def validate_event_type(self) -> NervousSystemEvent:
        if self.type not in ALLOWED_CLOUDEVENT_TYPES:
            raise ValueError(f"CloudEvent type '{self.type}' is not in ALLOWED_CLOUDEVENT_TYPES")
        return self
