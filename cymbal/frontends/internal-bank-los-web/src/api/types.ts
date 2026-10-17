/**
 * Cymbal Auto Finance — Internal Bank LOS & Audit Portal Contract Types (TDD §2.1 & §3.1)
 *
 * Backed by `src/api/generated.ts` generated via `openapi-typescript` from
 * `cymbal/services/loan-orchestrator-api/openapi.json`.
 */

import type { components, operations, paths } from './generated';

export type { components, operations, paths };
export type OpenApiSchemas = components['schemas'];

export type VehicleCategory =
  components['schemas']['VehicleRow']['category'];

export interface Vehicle {
  vehicle_id: string;
  make: string;
  model: string;
  category: VehicleCategory;
  ex_showroom_inr: number;
  cdn_image_url: string;
  min_down_payment_pct: number;
}

export type EmploymentType = 'SALARIED' | 'SELF_EMPLOYED' | 'FLEET_OPERATOR';

export interface Customer {
  customer_id: string;
  full_name: string;
  dob: string;
  email: string;
  phone: string;
  city: string;
  employment_type: EmploymentType;
  employer_name: string;
  monthly_income_inr: number;
  pan_number: string;
  aadhaar_number: string;
  is_ai_generated: boolean;
  ai_persona_json?: Record<string, unknown> | null;
  created_at: string;
}

export type KycMode = components['schemas']['LoanApplyRequest']['kyc_mode'];
export type ModelArmorTextStatus = 'CLEAN' | 'BLOCKED' | 'PENDING';

export interface ModelArmorFinding {
  category: string;
  field: 'loan_purpose_text' | 'employment_remarks' | 'applicant_notes' | string;
  severity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL' | string;
  description: string;
}

export interface ModelArmorFindingsPayload {
  template_id: string;
  region: string;
  scanned_fields: string[];
  images_scanned: false;
  findings: ModelArmorFinding[];
}

export interface KycDocument {
  kyc_id: string;
  customer_id: string;
  loan_id: string;
  kyc_mode: KycMode;
  pan_gcs_uri: string | null;
  aadhaar_gcs_uri: string | null;
  pan_image_model: string | null;
  aadhaar_image_model: string | null;
  pan_verified: boolean;
  aadhaar_verified: boolean;
  model_armor_text_status: ModelArmorTextStatus;
  model_armor_findings_json: ModelArmorFindingsPayload | null;
  verified_at: string;
}

export type UtilityType =
  components['schemas']['LoanJourneyDetailResponse']['utility_type'];

export type LoanStatus =
  components['schemas']['LoanJourneyDetailResponse']['status'];

export interface UnderwritingAudit {
  audit_id: string;
  loan_id: string;
  cibil_score: number;
  cibil_bureau_ref: string;
  foir_pct: number;
  ltv_pct: number;
  rules_version: string;
  gke_pod_name: string;
  gce_vm_name: string;
  decision: 'APPROVED' | 'REJECTED';
  rejection_reason: string | null;
  evaluated_at: string;
}

export interface CommercialCreditLine {
  credit_line_id: string;
  customer_id: string;
  loan_id: string;
  vehicle_category: 'TWO_WHEELER' | 'COMMERCIAL_FLEET';
  fleet_size: number;
  requested_limit_inr: number;
  sanctioned_limit_inr: number;
  utilisation_pct: number;
  decision: 'APPROVED' | 'REJECTED';
  status: string;
  trace_id: string;
  evaluated_at: string;
}

export interface InsurancePolicy {
  policy_id: string;
  customer_id: string;
  loan_id: string;
  policy_type: string;
  fasttag_id: string;
  premium_inr: number;
  coverage_inr: number;
  status: string;
  trace_id: string;
  issued_at: string;
}

export type DisbursementStatus = 'PENDING' | 'DISBURSED' | 'FAILED' | 'REPLAYED';

export interface Disbursement {
  disbursement_id: string;
  loan_id: string;
  sanction_letter_no: string;
  cbs_utr_number: string | null;
  nach_mandate_id: string;
  disbursed_amount_inr: number;
  dealer_account_no: string;
  status: DisbursementStatus;
  attempts: number;
  replay_of: string | null;
  disbursed_at: string | null;
}

export interface LoanApplication {
  loan_id: string;
  customer_id: string;
  vehicle_id: string;
  utility_type: UtilityType;
  loan_amount_inr: number;
  down_payment_inr: number;
  tenure_months: number;
  interest_rate_pct: number;
  emi_inr: number;
  loan_purpose_text: string;
  employment_remarks: string;
  applicant_notes: string;
  status: LoanStatus;
  journey_id: string;
  trace_id: string;
  total_latency_ms: number;
  created_at: string;
  updated_at: string;
  customer?: Customer;
  vehicle?: Vehicle;
  kyc_document?: KycDocument | null;
  underwriting_audit?: UnderwritingAudit | null;
  commercial_credit_line?: CommercialCreditLine | null;
  insurance_policy?: InsurancePolicy | null;
  disbursement?: Disbursement | null;
}

export interface KycSignedUrlResponse {
  customer_id: string;
  doc_type: 'pan' | 'aadhaar';
  kyc_mode: KycMode;
  gcs_uri: string;
  signed_url: string;
  image_model: string;
  watermark_text: string;
  verified: boolean;
  model_armor_scanned: false;
  extracted_fields: Record<string, string>;
}

export interface TopologyNode {
  id: string;
  service: string;
  name?: string;
  kind?: string;
  utility_label: string;
  compute_tier: string;
  endpoint: string;
  status: 'HEALTHY' | 'DEGRADED' | 'DOWN';
  p95_ms: number;
  rps: number;
  requests_per_min?: number;
  error_rate_pct: number;
}

export interface TopologyEdge {
  source: string;
  target: string;
  protocol: string;
  port: number;
  step_order: string;
}

export interface ObservabilityTopology {
  intended_sequence: string;
  nodes: TopologyNode[];
  edges: TopologyEdge[];
}

export interface TraceSpanRecord {
  span_id: string;
  parent_span_id: string | null;
  service: string;
  operation: string;
  start_offset_ms: number;
  duration_ms: number;
  http_status: number;
  attributes: Record<string, string | number | boolean>;
}

export interface TraceRecord {
  trace_id: string;
  journey_id: string;
  loan_id: string;
  total_duration_ms: number;
  is_synthetic: boolean;
  spans: TraceSpanRecord[];
}

export interface IapIdentityResponse {
  email: string;
  user_id?: string;
  iap_authenticated: boolean;
  header_source?: string;
}

export type TelemetryEventType =
  | 'kyc.verified'
  | 'armor.blocked'
  | 'loan.decided'
  | 'credit_line.decided'
  | 'insurance.issued'
  | 'disbursement.settled'
  | 'heartbeat';

export interface TelemetryStreamEvent {
  event_id: string;
  event_time: string;
  journey_id: string;
  loan_id: string;
  customer_id: string;
  trace_id: string;
  service: string;
  operation: string;
  event_type: TelemetryEventType;
  status_from?: string;
  status_to?: LoanStatus;
  decision?: string;
  foir_pct?: number;
  cibil_score?: number;
  emi_inr?: number;
  amount_inr?: number;
  duration_ms?: number;
  is_synthetic?: boolean;
}
