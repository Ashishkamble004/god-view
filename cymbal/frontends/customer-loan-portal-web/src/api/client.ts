import type {
  LoanApplication,
  LoanApplyRequest,
  LoanStatus,
  Vehicle,
} from './types';

const API_BASE = '/api/v1';

export class TenantApiError extends Error {
  public readonly status: number;
  public readonly code: string;
  public readonly details?: Record<string, unknown>;
  public readonly fieldErrors?: Record<string, string>;

  constructor(
    message: string,
    status: number,
    code = 'API_ERROR',
    details?: Record<string, unknown>,
    fieldErrors?: Record<string, string>
  ) {
    super(message);
    this.name = 'TenantApiError';
    this.status = status;
    this.code = code;
    this.details = details;
    this.fieldErrors = fieldErrors;
  }

  get isTenantPaused(): boolean {
    return this.code === 'TENANT_PAUSED';
  }

  get isModelArmorBlocked(): boolean {
    return this.code === 'MODEL_ARMOR_BLOCKED';
  }
}

function extractFieldErrors(
  detail: unknown
): Record<string, string> | undefined {
  if (!Array.isArray(detail)) return undefined;
  const map: Record<string, string> = {};
  for (const item of detail) {
    if (item && typeof item === 'object') {
      const rec = item as Record<string, unknown>;
      const loc = Array.isArray(rec.loc) ? rec.loc : [];
      const lastKey = loc.length > 0 ? String(loc[loc.length - 1]) : 'form';
      const msg =
        typeof rec.msg === 'string' ? rec.msg : 'Please check this field.';
      map[lastKey] = msg;
    }
  }
  return Object.keys(map).length > 0 ? map : undefined;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...(init?.headers || {}),
    },
  });

  if (!response.ok) {
    let errorPayload: Record<string, unknown> = {};
    try {
      errorPayload = (await response.json()) as Record<string, unknown>;
    } catch {
      // Non-JSON error body
    }

    const fieldErrors = extractFieldErrors(errorPayload.detail);

    let code = 'API_ERROR';
    if (typeof errorPayload.code === 'string' && errorPayload.code) {
      code = errorPayload.code;
    } else if (typeof errorPayload.error === 'string' && errorPayload.error) {
      code = errorPayload.error;
    } else if (
      typeof errorPayload.detail === 'string' &&
      errorPayload.detail.includes('TENANT_PAUSED')
    ) {
      code = 'TENANT_PAUSED';
    } else if (
      typeof errorPayload.detail === 'string' &&
      errorPayload.detail.includes('MODEL_ARMOR_BLOCKED')
    ) {
      code = 'MODEL_ARMOR_BLOCKED';
    } else if (response.status === 422 && fieldErrors) {
      code = 'VALIDATION_ERROR';
    }

    const message =
      (typeof errorPayload.message === 'string' && errorPayload.message) ||
      (typeof errorPayload.detail === 'string' && errorPayload.detail) ||
      (fieldErrors
        ? 'Please review the highlighted fields and try again.'
        : `Service request failed (HTTP ${response.status}). Please try again.`);

    throw new TenantApiError(
      message,
      response.status,
      code,
      errorPayload,
      fieldErrors
    );
  }

  return (await response.json()) as T;
}

export async function fetchVehicles(): Promise<Vehicle[]> {
  const res = await requestJson<Vehicle[] | { vehicles: Vehicle[] }>('/vehicles');
  const list = Array.isArray(res) ? res : res.vehicles;
  return list.map((v) => ({
    ...v,
    ex_showroom_inr: Number(v.ex_showroom_inr),
    min_down_payment_pct: Number(v.min_down_payment_pct),
  }));
}

export async function fetchLoans(params?: {
  status?: LoanStatus;
  limit?: number;
}): Promise<LoanApplication[]> {
  const query = new URLSearchParams();
  if (params?.status) query.set('status', params.status);
  if (params?.limit) query.set('limit', String(params.limit));
  const qs = query.toString() ? `?${query.toString()}` : '';
  const res = await requestJson<LoanApplication[] | { loans: LoanApplication[] }>(
    `/loans${qs}`
  );
  return Array.isArray(res) ? res : res.loans;
}

export async function fetchLoanById(loanId: string): Promise<LoanApplication> {
  return requestJson<LoanApplication>(`/loans/${encodeURIComponent(loanId)}`);
}

export async function submitLoanApplication(
  payload: LoanApplyRequest
): Promise<LoanApplication> {
  const body = {
    ...payload,
    customer: {
      full_name: payload.full_name,
      dob: payload.dob,
      email: payload.email,
      phone: payload.phone,
      city: payload.city,
      employment_type: payload.employment_type,
      employer_name: payload.employer_name,
      monthly_income_inr: payload.monthly_income_inr,
      pan_number: payload.pan_number,
      aadhaar_number: payload.aadhaar_number,
    },
    dealer_account_no: 'CYMBAL-DLR-0001',
  };
  return requestJson<LoanApplication>('/loans/apply', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function getTelemetryStreamUrl(journeyId?: string | null): string {
  if (journeyId) {
    return `${API_BASE}/telemetry/stream?journey_id=${encodeURIComponent(journeyId)}`;
  }
  return `${API_BASE}/telemetry/stream`;
}
