export type BadgeVariant =
  | 'default'
  | 'secondary'
  | 'outline'
  | 'muted'
  | 'success'
  | 'warning'
  | 'danger'
  | 'info';

export interface IapUserIdentity {
  email: string;
  principal: string;
  iap_authenticated: boolean;
  role: string;
}

export type AdapterAvailability = 'available' | 'partial' | 'unavailable';

export interface SenseAdapterReport {
  adapter_family: string;
  availability: AdapterAvailability;
  evidence: string;
  discovered_count: number;
  last_checked_at: string;
}

export type DiscoveredServiceKind =
  | 'cloud_run'
  | 'gke_workload'
  | 'gce_vm'
  | 'cloud_sql'
  | 'api_gateway'
  | 'bucket'
  | 'reasoning_engine'
  | 'other';

export interface DiscoveredService {
  id: string;
  env_id: string;
  name: string;
  kind: DiscoveredServiceKind;
  adapter_family: string;
  locator: string;
  labels: Record<string, string>;
  inferred_role: string;
  role_confidence: number;
  classification: 'journey_linked' | 'other_read_only';
  routes: string[];
  metrics_endpoint: string | null;
  repo_ref: string | null;
  tf_address: string | null;
  owner_agent_id: string;
  first_seen: string;
  last_seen: string;
  status: 'active' | 'gone';
}

export interface DiscoveredEdge {
  id: string;
  src: string;
  dst: string;
  protocol: string;
  evidence: 'trace' | 'code' | 'config' | 'k8s';
  call_rate_baseline: number;
  first_seen: string;
  last_seen: string;
}

export interface DiscoveredEntity {
  id: string;
  name: string;
  correlation_keys: string[];
  tables: string[];
  owning_service_id: string;
  states: string[];
  transitions: Array<{ from: string; to: string }>;
  rules_ref: string[];
  provenance: string;
}

export interface OperatorControlState {
  kill_switch_enabled: boolean;
  kill_switch_reason: string | null;
  kill_switch_actor: string | null;
  kill_switch_updated_at: string | null;
  tenant_paused: boolean;
  pause_schedule_ist: string;
  hibernated: boolean;
  simulator_enabled: boolean;
  simulator_window_ist: string;
  simulator_journeys_today: number;
  simulator_target_per_day: number;
  simulator_specimen_ratio: string;
}

export interface PerceptionReport {
  env_id: string;
  sovereign_id: string;
  sovereign_status: 'healthy' | 'degraded' | 'hibernating';
  heartbeat_at: string;
  sentinel_cadence_s: number;
  tokens_at_rest: number;
  sovereign_budget_daily_usd: number;
  sovereign_spent_today_usd: number;
  court_ceiling_daily_usd: number;
  monthly_ceiling_usd: number;
  escalation_reserve_pct: number;
  reasoning_model: string;
  escalation_model: string;
  embedding_model: string;
  managed_scope_label: string;
  warm_tier_spec: string;
  active_window_label: string;
  approval_window_label: string;
  hibernate_job_name: string;
  memory_db_name: string;
  simulator_target_daily: number;
  simulator_image_ratio_label: string;
  controls_enabled: boolean;
  senses: SenseAdapterReport[];
  discovered_services: DiscoveredService[];
  discovered_edges: DiscoveredEdge[];
  discovered_entities: DiscoveredEntity[];
  operator_state: OperatorControlState;
}

export type CourtAgentType =
  | 'application'
  | 'data'
  | 'business_line'
  | 'change'
  | 'platform';

export type CourtAgentState =
  | 'perceived'
  | 'spawning'
  | 'learning'
  | 'sentinel_compiled'
  | 'sleeping'
  | 'awake'
  | 'retiring'
  | 'archived';

export interface TrustRecordHistoryEntry {
  at: string;
  from: number;
  to: number;
  reason: string;
}

export interface TrustRecord {
  id: string;
  agent_id: string;
  action_type: string;
  level: 0 | 1 | 2 | 3 | 4;
  counters: {
    recommendations_correct: number;
    approved_executions: number;
    verified_outcomes: number;
    regressions: number;
    business_successes: number;
  };
  thresholds: {
    n1: number;
    n2: number;
    n3: number;
  };
  whitelisted: boolean;
  whitelisted_by: string | null;
  last_promoted_at: string | null;
  last_demoted_at: string | null;
  demotion_reason: string | null;
  history: TrustRecordHistoryEntry[];
}

export interface LivingModelEntity {
  name: string;
  fields: string[];
  correlation_keys: string[];
  tables: string[];
  provenance: string;
}

export interface LivingModelRule {
  id: string;
  expression: string;
  description: string;
  source_path: string;
  source_sha: string;
  confidence: number;
}

export interface LivingModelStateMachine {
  entity: string;
  states: string[];
  transitions: Array<{
    from: string;
    to: string;
    trigger: string;
    route: string;
  }>;
}

export interface LivingModelRoute {
  method: string;
  path: string;
  auth: string;
  probe_safe: boolean;
  admin: boolean;
  admin_capability: string | null;
  writes: string[];
}

export interface LivingModelDependency {
  target: string;
  kind: 'service' | 'table' | 'topic' | 'external';
  protocol: string;
  evidence: string;
}

export interface LivingModelDecisionBoundary {
  input: string;
  boundary: string;
  expected: string;
  method: 'bisection';
  learned_at: string;
  confidence: number;
  status: 'aligned' | 'drifted';
}

export interface LivingModelFeatureFlag {
  name: string;
  default_value: string;
  source_path: string;
}

export interface LivingModelCommitDiff {
  sha: string;
  author: string;
  occurred_at: string;
  business_summary: string;
  files_changed: string[];
}

export interface LivingSemanticModel {
  entities: LivingModelEntity[];
  rules: LivingModelRule[];
  state_machines: LivingModelStateMachine[];
  routes: LivingModelRoute[];
  dependencies: LivingModelDependency[];
  decision_boundaries: LivingModelDecisionBoundary[];
  feature_flags: LivingModelFeatureFlag[];
  commit_diffs: LivingModelCommitDiff[];
}

export interface CourtAgent {
  id: string;
  agent_type: CourtAgentType;
  subject_ref: string;
  reasoning_engine: string;
  model_id: string;
  a2a_card_url: string;
  state: CourtAgentState;
  warmth_min_instances: 0 | 1;
  memory_bank_id: string;
  namespace: string;
  token_budget_daily: number;
  token_budget_daily_usd: number;
  tokens_spent_today: number;
  tokens_spent_today_usd: number;
  budget_exhausted: boolean;
  last_wake_at: string;
  last_reflection_at: string;
  wakes_7d: number;
  spawned_at: string;
  retired_at: string | null;
  trust_records: TrustRecord[];
  living_model: LivingSemanticModel;
}

export type InvariantStatus = 'proposed' | 'shadow' | 'enforced' | 'retired';
export type InvariantProvenance = 'code' | 'policy' | 'observed';
export type SeverityLevel = 'sev1' | 'sev2' | 'sev3';

export interface InvariantHistoryEntry {
  at: string;
  from_status: string;
  to_status: string;
  actor: string;
  note: string;
}

export interface InvariantDocument {
  id: string;
  name: string;
  scope: {
    service: string;
    entity: string;
    journey: string;
  };
  provenance: InvariantProvenance;
  provenance_ref: string;
  check: {
    dialect: 'bigquery' | 'postgres' | 'python';
    statement: string;
    window_s: number;
  };
  severity: SeverityLevel;
  wake_condition: {
    operator: string;
    threshold: number;
    consecutive: number;
    target_agent_id: string;
  };
  confidence: number;
  created_by: string;
  version: string;
  status: InvariantStatus;
  shadow_stats: {
    runs: number;
    breaches: number;
    false_positive_rate: number;
  };
  sentinel_id: string;
  git_path: string;
  git_sha: string;
  body_md: string;
  history: InvariantHistoryEntry[];
}

export interface SentinelDefinition {
  sentinel_id: string;
  class:
    | 'invariant_sql'
    | 'kpi_changepoint'
    | 'journey_conformance'
    | 'probe_plan'
    | 'golden_signal_gate'
    | 'drift_diff'
    | 'budget'
    | 'discovery';
  source_ref: string;
  check: {
    dialect: string;
    statement: string;
  };
  cadence_s: number;
  baseline: {
    model: string;
    window_days: number;
  };
  wake_condition: {
    operator: string;
    threshold: number;
    consecutive: number;
    window_s: number;
  };
  target_agent_id: string;
  state: 'shadow' | 'enforced' | 'paused' | 'retired';
  compiled_by: string;
  compiled_at: string;
  last_run_at: string;
  last_result: {
    count: number;
    breach?: boolean;
  };
  consecutive_breaches: number;
}

export interface JourneyStepNode {
  id: string;
  label: string;
  service: string;
  operation: string;
  completion_rate_pct: number;
  avg_dwell_ms: number;
  p95_dwell_ms: number;
}

export interface DirectlyFollowsEdge {
  from: string;
  to: string;
  count: number;
  p95_ms: number;
  kind: 'conformant' | 'skipped' | 'reordered' | 'looped' | 'stalled';
}

export interface ConformanceRuleSummary {
  id: string;
  rule: string;
  violations_24h: number;
  status: 'healthy' | 'violated';
}

export interface FunnelKpiStage {
  step_id: string;
  step_name: string;
  entered: number;
  completed: number;
  conversion_pct: number;
  dropoff_rupee_value: number;
}

export type JourneyConformanceStatus =
  | 'CONFORMANT'
  | 'SKIPPED'
  | 'REORDERED'
  | 'LOOPED'
  | 'STALLED';

export interface JourneyInstanceRow {
  journey_id: string;
  started_at: string;
  ended_at: string | null;
  observed_sequence: string;
  conformance: JourneyConformanceStatus;
  final_status: string;
  rupee_value: number;
  total_duration_ms: number;
  is_synthetic: boolean;
}

export interface KpiRollup {
  window_start: string;
  window_end: string;
  env_id: string;
  journey_name: string;
  kpi: string;
  label: string;
  unit: 'pct' | 'inr' | 'ms';
  segment: string;
  value: number;
  baseline_value: number;
  zscore: number;
  cusum: number;
  sample_n: number;
  breach: boolean;
}

export interface JourneyModel {
  journey_key: string;
  name: string;
  owner_agent_id: string;
  status: 'active' | 'degraded' | 'stalled';
  intended_order_source: string;
  steps: JourneyStepNode[];
  directly_follows: Record<string, DirectlyFollowsEdge>;
  conformance_rules: ConformanceRuleSummary[];
  funnel_kpis: FunnelKpiStage[];
  rupee_impact_model: {
    avg_ticket_inr: number;
    stuck_journeys_count: number;
    violated_journeys_count: number;
    total_rupee_at_risk: number;
  };
  recent_instances: JourneyInstanceRow[];
  kpi_rollups: KpiRollup[];
}

export interface EvidencePack {
  schema_version: string;
  evidence_pack_id: string;
  incident_id: string;
  fired_at: string;
  sentinel: {
    id: string;
    class: string;
    source_ref: string;
  };
  breach: {
    statement: string;
    observed: {
      count: number;
      window_s: number;
    };
    first_seen: string;
    consecutive: number;
  };
  baseline: {
    model: string;
    expected: number;
    stddev: number;
  };
  affected: {
    services: string[];
    journeys: string[];
    journey_ids: string[];
    rows: number;
    rupee_at_risk: number;
  };
  correlated_changes: Array<{
    change_event_id: string;
    kind: string;
    occurred_at: string;
    business_summary: string;
  }>;
  golden_signals: {
    status: 'green' | 'red';
    p95_ms: number;
    error_rate_pct: number;
  };
  recall: {
    episodic: Array<{ incident_id: string; similarity: number }>;
    skills: Array<{ skill_slug: string; similarity: number }>;
  };
  budget: {
    agent_tokens_remaining: number;
    escalation_allowed: boolean;
  };
  target_agent_id: string;
  trust_snapshot: Record<string, number>;
}

export type A2AMessageType =
  | 'wake'
  | 'interrogate'
  | 'brief'
  | 'report'
  | 'critique'
  | 'handoff';

export interface SwarmTranscriptEntry {
  seq: number;
  incident_id: string;
  from_agent_id: string;
  to_agent_id: string;
  round: 1 | 2 | 3 | 4;
  sent_at: string;
  type: A2AMessageType;
  summary: string;
  payload: Record<string, unknown>;
}

export interface AuditActionRecord {
  audit_id: string;
  incident_id: string;
  agent_id: string;
  trust_level: number;
  action_type: string;
  capability: string;
  target_resource: string;
  approver: {
    principal: string;
    channel: string;
    at: string;
  } | null;
  evidence_pack_id: string;
  sa_used: string;
  executed_at: string;
  result: 'success' | 'failed' | 'reverted' | 'pending_approval';
  verification: Record<string, string | number | boolean>;
  reversal_recipe: {
    capability: string;
    target?: string;
    args?: string;
  };
  reversed_at: string | null;
}

export interface ProceduralSkillSummary {
  skill_slug: string;
  name: string;
  version: string;
  owner_agent_id: string;
  provenance: 'agent_synthesized' | 'human_runbook';
  confidence_score: number;
  executions_count: number;
  avg_mttr_seconds: number;
  blast_radius: string;
  body_md: string;
}

export interface EpisodicIncident {
  incident_id: string;
  opened_at: string;
  closed_at: string | null;
  severity: SeverityLevel;
  symptom_summary: string;
  evidence_pack: EvidencePack;
  lead_agent_id: string;
  participants: string[];
  root_cause: string;
  causal_chain: string[];
  actions: AuditActionRecord[];
  outcome: 'resolved' | 'mitigated' | 'false_positive' | 'investigating';
  rupee_impact: number;
  affected_journeys: string[];
  convergence: {
    consensus: number;
    rounds: number;
    seconds: number;
    escalated_to_argon: boolean;
    escalation_model?: string | null;
    exit_reason: 'consensus_reached' | 'max_rounds' | 'timeout_90s';
  };
  skills_used: string[];
  skill_details: ProceduralSkillSummary[];
  transcript: SwarmTranscriptEntry[];
  timeline: Array<{
    at: string;
    phase: 'detected' | 'woken' | 'converged' | 'remediated' | 'verified';
    label: string;
    detail: string;
  }>;
}

export type ApprovalDecisionStatus =
  | 'pending'
  | 'approved'
  | 'rejected'
  | 'snoozed'
  | 'expired';

export interface ApprovalCard {
  approval_id: string;
  incident_id: string;
  agent_id: string;
  action_type: string;
  capability: string;
  target_resource: string;
  trust_level_required: number;
  narrative: string;
  evidence_pack_id: string;
  rupee_impact: number;
  reversal_recipe: {
    capability: string;
    args: string;
  };
  requested_at: string;
  expires_at: string;
  decision: ApprovalDecisionStatus;
  decided_by: string | null;
  decided_at: string | null;
  channel: 'chat' | 'cockpit';
  audit_id: string | null;
}

export type ChangeEventKind =
  | 'commit'
  | 'pr_opened'
  | 'pr_merged'
  | 'ci_run'
  | 'deploy'
  | 'audit_config_change'
  | 'tfstate_change'
  | 'operator_note';

export interface ChangeEvent {
  id: string;
  repo: string;
  kind: ChangeEventKind;
  sha: string;
  pr_number: number | null;
  author: string;
  business_summary: string;
  risk_score: number;
  affected_services: string[];
  affected_journeys: string[];
  canary: {
    window_start: string;
    window_end: string;
    verdict: 'passed' | 'failed' | 'running' | 'not_applicable';
    details: string;
  };
  linked_incident_ids: string[];
  occurred_at: string;
  rollback_pr_url?: string | null;
}

export type CostFindingType =
  | 'idle_reasoning_engine'
  | 'oversized'
  | 'unused'
  | 'budget_threshold';

export interface CostFinding {
  id: string;
  resource: string;
  finding_type: CostFindingType;
  monthly_usd_estimate: number;
  recommendation: string;
  status: 'open' | 'accepted' | 'dismissed';
  found_by: string;
  found_at: string;
}

export interface TerraformDriftItem {
  id: string;
  resource_address: string;
  service_name: string;
  kind: string;
  attribute: string;
  expected_tfstate: string;
  live_value: string;
  severity: 'high' | 'medium' | 'low';
  detected_at: string;
  status: 'drifted' | 'restored' | 'reconciling';
  fast_restore_capability: string;
  iac_reconcile_capability: string;
}

export interface CostAndDriftOverview {
  monthly_ceiling_usd: number;
  projected_active_month_usd: number;
  projected_hibernated_month_usd: number;
  budget_thresholds_pct: number[];
  current_month_spend_usd: number;
  daily_court_ceiling_usd: number;
  daily_court_spent_usd: number;
  escalation_reserve_pct: number;
  tier_budgets_summary: string;
  cost_findings_subtitle: string;
  tfstate_uri: string;
  last_drift_scan_at: string;
  cost_findings: CostFinding[];
  drift_diffs: TerraformDriftItem[];
}

export interface ChaosDrill {
  drill_id: string;
  fault_class: 'BUSINESS' | 'INFRA';
  fault_type: string;
  title: string;
  target_service: string;
  infra_golden_signals: 'green' | 'red';
  expected_detection: string;
  mttd_target: string;
  is_active: boolean;
  triggered_by: string | null;
  activated_at: string | null;
  expires_at: string | null;
}

export interface CockpitDataset {
  me: IapUserIdentity;
  perceive: PerceptionReport;
  court: CourtAgent[];
  invariants: InvariantDocument[];
  sentinels: SentinelDefinition[];
  journeys: JourneyModel[];
  incidents: EpisodicIncident[];
  approvals: ApprovalCard[];
  changes: ChangeEvent[];
  cost: CostAndDriftOverview;
  drills: ChaosDrill[];
  topology?: TopologyResponse | null;
}

export type TopologyTier =
  | 'edge'
  | 'compute'
  | 'data'
  | 'messaging'
  | 'platform'
  | 'observer'
  | 'external';

export type TopologyStatus = 'green' | 'amber' | 'red' | 'unknown';

export interface TopologyNode {
  id: string;
  name: string;
  kind: DiscoveredServiceKind | string;
  tier: TopologyTier;
  status: TopologyStatus;
  business: {
    domain: string;
    role_summary: string;
    confidence: number;
  };
  incidents_open: number;
  agent_id: string | null;
  golden: {
    rps: number | null;
    p95_ms: number | null;
    error_ratio: number | null;
  };
  posture: {
    ingress: string | null;
    iap: boolean | null;
    invoker_iam: boolean | null;
    waf: string | null;
  };
}

export interface TopologyEdge {
  id: string;
  src: string;
  dst: string;
  kind: 'http' | 'grpc' | 'pubsub' | 'sql' | 'lb_route' | 'iam' | string;
  evidence_kind: string;
  rps: number | null;
  error_ratio: number | null;
  confidence: number;
}

export interface TopologyJourneyStep {
  node: string;
  operation: string;
}

export interface TopologyJourney {
  name: string;
  steps: TopologyJourneyStep[];
  health: 'green' | 'amber' | 'red';
  confidence: number;
}

export interface TopologyResponse {
  env_id: string;
  generated_at: string;
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  journeys: TopologyJourney[];
  layout_hint?: {
    groups?: Array<{
      tier: string;
      node_ids: string[];
    }>;
  };
}

export interface EvidenceItem {
  kind: string;
  ref: string;
  excerpt: string;
}

export interface BusinessContextKPI {
  name: string;
  definition: string;
  unit: string;
  confidence: number;
  evidence: any;
  value?: number | null;
}

export interface BusinessContextInvariant {
  statement: string;
  check: string;
  severity: string;
  confidence: number;
  evidence: any;
  status?: 'verified' | 'breached' | 'shadow';
}

export interface FullBusinessContext {
  role_summary: string;
  domain: string;
  capabilities: string[];
  upstream: string[];
  downstream: string[];
  journeys: Array<{
    name: string;
    steps: Array<{ service: string; operation: string }>;
    confidence: number;
    evidence: any;
  }>;
  kpis: BusinessContextKPI[];
  invariants: BusinessContextInvariant[];
  risk_notes: string[];
  confidence: number;
  evidence: EvidenceItem[];
  inferred_by: string;
  inferred_at: string;
  recent_incidents?: Array<{
    id: string;
    title: string;
    severity: string;
    status: string;
  }>;
}

export interface HarnessTreeNode {
  id: string;
  label: string;
  type: 'condition' | 'action' | 'heuristic' | 'verification';
  description?: string;
  children?: HarnessTreeNode[];
  confidence?: number;
  version?: string;
}

export interface HarnessTreeResponse {
  agent_id: string;
  version: string;
  active: boolean;
  accuracy_gain_pct?: number;
  tree: HarnessTreeNode;
  skills: Array<{
    id: string;
    name: string;
    fitness_score: number;
    description: string;
  }>;
}

