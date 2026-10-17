import type {
  IapIdentityResponse,
  KycSignedUrlResponse,
  LoanApplication,
  LoanStatus,
  ObservabilityTopology,
  TraceRecord,
  Vehicle,
} from './types';

const API_BASE = '/api/v1';

export class LosApiError extends Error {
  public readonly status: number;
  public readonly code: string;

  constructor(message: string, status: number, code = 'API_ERROR') {
    super(message);
    this.name = 'LosApiError';
    this.status = status;
    this.code = code;
  }

  get isIapSessionExpired(): boolean {
    return this.status === 401 || this.status === 403;
  }
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
      // Non-JSON body
    }
    const code =
      (typeof errorPayload.code === 'string' && errorPayload.code) ||
      `HTTP_${response.status}`;
    const message =
      (typeof errorPayload.message === 'string' && errorPayload.message) ||
      (typeof errorPayload.detail === 'string' && errorPayload.detail) ||
      `Request to ${path} failed with HTTP ${response.status}`;
    throw new LosApiError(message, response.status, code);
  }

  return (await response.json()) as T;
}

export async function fetchIapIdentity(): Promise<IapIdentityResponse> {
  try {
    const data = await requestJson<
      Partial<IapIdentityResponse> & {
        principal?: string;
        authenticated?: boolean;
      }
    >('/me');
    const rawIdentity = data.email || data.principal || '';
    const cleanEmail = rawIdentity
      .replace(/^accounts\.google\.com:/, '')
      .trim();
    if (!cleanEmail || cleanEmail === 'anonymous' || data.iap_authenticated === false) {
      return {
        email: 'Not signed in (no IAP header)',
        iap_authenticated: false,
        header_source: 'missing-iap-header',
      };
    }
    return {
      email: cleanEmail,
      user_id: data.user_id,
      iap_authenticated: Boolean(
        data.iap_authenticated ?? data.authenticated ?? true
      ),
      header_source: data.header_source || 'X-Goog-Authenticated-User-Email',
    };
  } catch {
    return {
      email: 'Not signed in (no IAP header)',
      iap_authenticated: false,
      header_source: 'missing-iap-header',
    };
  }
}

export async function fetchLosLoans(params?: {
  status?: LoanStatus;
  limit?: number;
}): Promise<LoanApplication[]> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set('status', params.status);
  if (params?.limit) qs.set('limit', String(params.limit));
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  const data = await requestJson<LoanApplication[] | { loans: LoanApplication[] }>(
    `/loans${suffix}`
  );
  return Array.isArray(data) ? data : data.loans;
}

export async function fetchLosLoanById(loanId: string): Promise<LoanApplication> {
  return requestJson<LoanApplication>(`/loans/${encodeURIComponent(loanId)}`);
}

export async function fetchLosVehicles(): Promise<Vehicle[]> {
  const data = await requestJson<Vehicle[] | { vehicles: Vehicle[] }>('/vehicles');
  return Array.isArray(data) ? data : data.vehicles;
}

export async function fetchKycSpecimenDocument(
  customerId: string,
  docType: 'pan' | 'aadhaar'
): Promise<KycSignedUrlResponse> {
  return requestJson<KycSignedUrlResponse>(
    `/kyc-documents/${encodeURIComponent(customerId)}/${docType}`
  );
}

export async function fetchTenantTopology(): Promise<ObservabilityTopology> {
  const raw = await requestJson<
    ObservabilityTopology & {
      nodes?: Array<
        Partial<ObservabilityTopology['nodes'][number]> & {
          name?: string;
          kind?: string;
          requests_per_min?: number;
        }
      >;
    }
  >('/observability/topology');

  const nodes = (raw.nodes || []).map((n) => {
    const rawStatus = (n.status || 'HEALTHY').toUpperCase();
    const mappedStatus: 'HEALTHY' | 'DEGRADED' | 'DOWN' =
      rawStatus === 'CRITICAL' || rawStatus === 'DOWN'
        ? 'DOWN'
        : rawStatus === 'DEGRADED'
          ? 'DEGRADED'
          : 'HEALTHY';
    return {
      id: n.id || n.service || n.name || 'service',
      service: n.service || n.name || n.id || 'service',
      utility_label: n.utility_label || 'Tenant Utility',
      compute_tier: n.compute_tier || n.kind || 'GCP Managed',
      endpoint: n.endpoint || '/internal',
      status: mappedStatus,
      p95_ms: Number(n.p95_ms ?? 0),
      rps:
        n.rps !== undefined
          ? Number(n.rps)
          : Number(((n.requests_per_min ?? 0) / 60).toFixed(1)),
      error_rate_pct: Number(n.error_rate_pct ?? 0),
    };
  });

  return {
    intended_sequence: raw.intended_sequence || 'KYC < LOS < [FLEET] < INS < CBS',
    nodes,
    edges: raw.edges || [],
  };
}

export async function fetchTenantTrace(traceId: string): Promise<TraceRecord> {
  const raw = await requestJson<
    TraceRecord & {
      spans?: Array<
        Partial<TraceRecord['spans'][number]> & {
          started_at?: string;
        }
      >;
    }
  >(`/observability/traces/${encodeURIComponent(traceId)}`);

  let cumulativeOffset = 0;
  const spans = (raw.spans || []).map((sp) => {
    const duration = Number(sp.duration_ms ?? 50);
    const offset =
      sp.start_offset_ms !== undefined ? Number(sp.start_offset_ms) : cumulativeOffset;
    cumulativeOffset += duration;
    return {
      span_id: sp.span_id || 'spn',
      parent_span_id: sp.parent_span_id ?? null,
      service: sp.service || 'service',
      operation: sp.operation || 'op',
      start_offset_ms: offset,
      duration_ms: duration,
      http_status: Number(sp.http_status ?? 200),
      attributes: (sp.attributes || {}) as Record<string, string | number | boolean>,
    };
  });

  return {
    trace_id: raw.trace_id,
    journey_id: raw.journey_id,
    loan_id: raw.loan_id,
    total_duration_ms: Number(raw.total_duration_ms ?? cumulativeOffset),
    is_synthetic: Boolean(raw.is_synthetic),
    spans,
  };
}

export function getTelemetryStreamUrl(): string {
  return `${API_BASE}/telemetry/stream`;
}
