import type { LoanApplication, LoanStatus, UtilityType } from '../api/types';

export type SortField =
  | 'created_at'
  | 'loan_amount_inr'
  | 'emi_inr'
  | 'foir_pct'
  | 'cibil_score'
  | 'total_latency_ms';

export type SortDirection = 'asc' | 'desc';

export interface QueueFilterCriteria {
  status: 'ALL' | LoanStatus;
  utilityType: 'ALL' | UtilityType;
  syntheticMode: 'ALL' | 'HUMAN' | 'SYNTHETIC';
  search: string;
}

export interface LosKpiSummary {
  totalApplications: number;
  approvedOrDisbursedCount: number;
  approvalRatePct: number;
  modelArmorBlockedCount: number;
  stuckDisbursementCount: number;
  stuckDisbursementRupees: number;
  syntheticCount: number;
  humanCount: number;
  avgLatencyMs: number;
}

export function formatInr(amountInr: number): string {
  const safe = Number.isFinite(amountInr) ? Math.round(amountInr) : 0;
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(safe);
}

export function formatLatencyMs(ms?: number | null): string {
  if (ms == null || !Number.isFinite(ms)) return '—';
  return `${Math.round(ms).toLocaleString('en-IN')} ms`;
}

const ENUM_LABELS: Record<string, string> = {
  DISBURSED: 'Disbursed',
  DISBURSEMENT_PENDING: 'Disbursement Pending',
  APPROVED: 'Approved',
  INSURED: 'Insured',
  REJECTED: 'Rejected',
  MODEL_ARMOR_BLOCKED: 'Model Armor Blocked',
  KYC_PENDING: 'KYC Pending',
  KYC_VERIFIED: 'KYC Verified',
  PENDING: 'Pending',
  FAILED: 'Failed',
  REPLAYED: 'Replayed',
  ISSUED: 'Issued',
  ACTIVE: 'Active',
  CLEAN: 'Clean',
  BLOCKED: 'Blocked',
  HEALTHY: 'Healthy',
  DEGRADED: 'Degraded',
  DOWN: 'Down',
  RETAIL_AUTO_LOAN: 'Retail Auto Loan',
  COMMERCIAL_FLEET_TOPUP: 'Commercial Fleet Top-Up',
  EV_SUV: 'Electric SUV',
  PASSENGER_SUV: 'Passenger SUV',
  TWO_WHEELER: 'Two-Wheeler',
  COMMERCIAL_FLEET: 'Commercial Fleet',
  IMAGE: 'Specimen Image',
  TEXT_ONLY: 'Text Only',
  SALARIED: 'Salaried',
  SELF_EMPLOYED: 'Self-Employed',
  FLEET_OPERATOR: 'Fleet Operator',
  COMPREHENSIVE_MOTOR_3YR: 'Comprehensive Motor (3-Yr)',
  COMMERCIAL_GOODS_CARRIER_1YR: 'Commercial Goods Carrier (1-Yr)',
  TWO_WHEELER_FLEET_BUNDLED: 'Two-Wheeler Fleet Bundled',
  PROMPT_INJECTION_JAILBREAK: 'Prompt Injection / Jailbreak',
  CRITICAL: 'Critical',
  HIGH: 'High',
  MEDIUM: 'Medium',
  LOW: 'Low',
};

export function formatEnumLabel(raw?: string | null): string {
  if (!raw) return '—';
  const trimmed = raw.trim();
  if (ENUM_LABELS[trimmed]) {
    return ENUM_LABELS[trimmed];
  }
  if (trimmed.startsWith('FOIR_EXCEEDS_MAX_THRESHOLD')) {
    return trimmed.replace(
      'FOIR_EXCEEDS_MAX_THRESHOLD',
      'FOIR Exceeds 60% Policy Ceiling'
    );
  }
  return trimmed
    .split('_')
    .map((part) =>
      part.length <= 3 && /^(KYC|LOS|CBS|SUV|EV|UTR|ID|PII)$/.test(part)
        ? part
        : part.charAt(0).toUpperCase() + part.slice(1).toLowerCase()
    )
    .join(' ');
}

export function sortLoanQueue(
  loans: LoanApplication[],
  field: SortField,
  direction: SortDirection
): LoanApplication[] {
  const factor = direction === 'asc' ? 1 : -1;
  return [...loans].sort((a, b) => {
    let valA = 0;
    let valB = 0;

    switch (field) {
      case 'loan_amount_inr':
        valA = a.loan_amount_inr;
        valB = b.loan_amount_inr;
        break;
      case 'emi_inr':
        valA = a.emi_inr;
        valB = b.emi_inr;
        break;
      case 'foir_pct':
        valA = a.underwriting_audit?.foir_pct ?? 0;
        valB = b.underwriting_audit?.foir_pct ?? 0;
        break;
      case 'cibil_score':
        valA = a.underwriting_audit?.cibil_score ?? 0;
        valB = b.underwriting_audit?.cibil_score ?? 0;
        break;
      case 'total_latency_ms':
        valA = a.total_latency_ms;
        valB = b.total_latency_ms;
        break;
      case 'created_at':
      default:
        valA = new Date(a.created_at).getTime();
        valB = new Date(b.created_at).getTime();
        break;
    }

    if (valA === valB) {
      return a.loan_id.localeCompare(b.loan_id);
    }
    return (valA - valB) * factor;
  });
}

export function filterLoanQueue(
  loans: LoanApplication[],
  criteria: QueueFilterCriteria
): LoanApplication[] {
  const q = criteria.search.trim().toLowerCase();

  return loans.filter((loan) => {
    if (criteria.status !== 'ALL' && loan.status !== criteria.status) {
      return false;
    }
    if (
      criteria.utilityType !== 'ALL' &&
      loan.utility_type !== criteria.utilityType
    ) {
      return false;
    }
    if (criteria.syntheticMode === 'HUMAN') {
      if (loan.customer?.is_ai_generated !== false) return false;
    } else if (criteria.syntheticMode === 'SYNTHETIC') {
      if (loan.customer?.is_ai_generated !== true) return false;
    }

    if (q.length > 0) {
      const haystack = [
        loan.loan_id,
        loan.journey_id,
        loan.trace_id,
        loan.customer?.full_name ?? '',
        loan.customer?.pan_number ?? '',
        loan.customer?.city ?? '',
        loan.vehicle?.make ?? '',
        loan.vehicle?.model ?? '',
        loan.disbursement?.cbs_utr_number ?? '',
      ]
        .join(' ')
        .toLowerCase();
      if (!haystack.includes(q)) {
        return false;
      }
    }

    return true;
  });
}

export function computeLosKpis(loans: LoanApplication[]): LosKpiSummary {
  const totalApplications = loans.length;
  if (totalApplications === 0) {
    return {
      totalApplications: 0,
      approvedOrDisbursedCount: 0,
      approvalRatePct: 0,
      modelArmorBlockedCount: 0,
      stuckDisbursementCount: 0,
      stuckDisbursementRupees: 0,
      syntheticCount: 0,
      humanCount: 0,
      avgLatencyMs: 0,
    };
  }

  let approvedOrDisbursedCount = 0;
  let modelArmorBlockedCount = 0;
  let stuckDisbursementCount = 0;
  let stuckDisbursementRupees = 0;
  let syntheticCount = 0;
  let humanCount = 0;
  let totalLatency = 0;

  for (const loan of loans) {
    if (
      loan.status === 'APPROVED' ||
      loan.status === 'INSURED' ||
      loan.status === 'DISBURSED' ||
      loan.status === 'DISBURSEMENT_PENDING'
    ) {
      approvedOrDisbursedCount += 1;
    }
    if (
      loan.status === 'MODEL_ARMOR_BLOCKED' ||
      loan.kyc_document?.model_armor_text_status === 'BLOCKED'
    ) {
      modelArmorBlockedCount += 1;
    }
    if (
      loan.status === 'DISBURSEMENT_PENDING' ||
      loan.disbursement?.status === 'PENDING' ||
      loan.disbursement?.status === 'FAILED'
    ) {
      stuckDisbursementCount += 1;
      stuckDisbursementRupees +=
        loan.disbursement?.disbursed_amount_inr ?? loan.loan_amount_inr;
    }
    if (loan.customer?.is_ai_generated) {
      syntheticCount += 1;
    } else {
      humanCount += 1;
    }
    totalLatency += loan.total_latency_ms;
  }

  const approvalRatePct = Number(
    ((approvedOrDisbursedCount / totalApplications) * 100).toFixed(1)
  );

  return {
    totalApplications,
    approvedOrDisbursedCount,
    approvalRatePct,
    modelArmorBlockedCount,
    stuckDisbursementCount,
    stuckDisbursementRupees,
    syntheticCount,
    humanCount,
    avgLatencyMs: Math.round(totalLatency / totalApplications),
  };
}
