import type {
  ApprovalCard,
  ChaosDrill,
  ChangeEvent,
  CockpitDataset,
  CostAndDriftOverview,
  CourtAgent,
  EpisodicIncident,
  IapUserIdentity,
  InvariantDocument,
  JourneyModel,
  PerceptionReport,
  SentinelDefinition,
  TerraformDriftItem,
  TrustRecord,
} from './types';

export class CockpitApiError extends Error {
  public readonly status: number;
  public readonly code: string;
  public readonly endpoint: string;

  constructor(status: number, code: string, message: string, endpoint: string) {
    super(message);
    this.name = 'CockpitApiError';
    this.status = status;
    this.code = code;
    this.endpoint = endpoint;
  }
}

export interface SettledDatasetResult {
  partial: Partial<CockpitDataset>;
  errors: Partial<Record<keyof CockpitDataset, CockpitApiError>>;
  iapExpired: boolean;
  anySucceeded: boolean;
  firstError: CockpitApiError | null;
}

async function performFetch(endpoint: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(endpoint, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...(init?.headers ?? {}),
      },
    });
  } catch (err) {
    if (err instanceof TypeError) {
      throw new CockpitApiError(
        401,
        'IAP_SESSION_EXPIRED',
        'Session expired — Reload to sign in',
        endpoint
      );
    }
    throw err;
  }
}

async function requestJson<T>(endpoint: string, init?: RequestInit): Promise<T> {
  let response = await performFetch(endpoint, init);

  // Automatic fallback from /api/v1/* to /v1/* if Sovereign mounted /v1/* without /api prefix
  if (response.status === 404 && endpoint.startsWith('/api/v1/')) {
    const fallbackEndpoint = endpoint.replace(/^\/api\/v1\//, '/v1/');
    const fallbackRes = await performFetch(fallbackEndpoint, init);
    if (fallbackRes.ok || fallbackRes.status !== 404) {
      response = fallbackRes;
    }
  }

  if (
    response.type === 'opaqueredirect' ||
    response.status === 401 ||
    response.status === 302
  ) {
    throw new CockpitApiError(
      401,
      'IAP_SESSION_EXPIRED',
      'Session expired — Reload to sign in',
      endpoint
    );
  }

  if (!response.ok) {
    let errorCode = `HTTP_${response.status}`;
    let errorMessage = `Request to ${endpoint} failed with status ${response.status}`;
    try {
      const payload = (await response.json()) as {
        code?: string;
        detail?: string;
        message?: string;
        error?: string;
      };
      if (payload.code) errorCode = payload.code;
      if (payload.message || payload.detail || payload.error) {
        errorMessage = payload.message ?? payload.detail ?? payload.error ?? errorMessage;
      }
    } catch {
      // Response body was not JSON; preserve status message
    }
    throw new CockpitApiError(response.status, errorCode, errorMessage, endpoint);
  }

  return (await response.json()) as T;
}

function toCockpitError(reason: unknown, endpoint: string): CockpitApiError {
  if (reason instanceof CockpitApiError) return reason;
  if (reason instanceof TypeError) {
    return new CockpitApiError(
      401,
      'IAP_SESSION_EXPIRED',
      'Session expired — Reload to sign in',
      endpoint
    );
  }
  const msg = reason instanceof Error ? reason.message : 'Unexpected Sovereign API error';
  return new CockpitApiError(500, 'API_ERROR', msg, endpoint);
}

export const cockpitApi = {
  getMe(): Promise<IapUserIdentity> {
    return requestJson<IapUserIdentity>('/api/v1/me');
  },

  getPerception(): Promise<PerceptionReport> {
    return requestJson<PerceptionReport>('/api/v1/perceive');
  },

  triggerPerceive(reset = true): Promise<PerceptionReport> {
    return requestJson<PerceptionReport>('/api/v1/perceive', {
      method: 'POST',
      body: JSON.stringify({ reset }),
    });
  },

  getCourt(): Promise<{ agents: CourtAgent[] }> {
    return requestJson<{ agents: CourtAgent[] }>('/api/v1/court');
  },

  getCourtAgent(agentId: string): Promise<CourtAgent> {
    return requestJson<CourtAgent>(`/api/v1/court/${encodeURIComponent(agentId)}`);
  },

  controlCourtAgent(
    agentId: string,
    action: 'wake' | 'retire' | 'warm' | 'cool'
  ): Promise<CourtAgent> {
    return requestJson<CourtAgent>(
      `/api/v1/court/${encodeURIComponent(agentId)}:${action}`,
      { method: 'POST', body: JSON.stringify({}) }
    );
  },

  toggleWhitelist(recordId: string, whitelisted: boolean): Promise<TrustRecord> {
    return requestJson<TrustRecord>(
      `/api/v1/trust-records/${encodeURIComponent(recordId)}:whitelist`,
      {
        method: 'POST',
        body: JSON.stringify({ whitelisted }),
      }
    );
  },

  getInvariants(): Promise<{ invariants: InvariantDocument[] }> {
    return requestJson<{ invariants: InvariantDocument[] }>('/api/v1/invariants');
  },

  getSentinels(): Promise<{ sentinels: SentinelDefinition[] }> {
    return requestJson<{ sentinels: SentinelDefinition[] }>('/api/v1/sentinels');
  },

  compileSentinel(invariantId: string): Promise<SentinelDefinition> {
    return requestJson<SentinelDefinition>('/api/v1/sentinels', {
      method: 'POST',
      body: JSON.stringify({ invariant_id: invariantId }),
    });
  },

  updateSentinelState(
    sentinelId: string,
    state: 'shadow' | 'enforced' | 'paused' | 'retired'
  ): Promise<SentinelDefinition> {
    return requestJson<SentinelDefinition>(
      `/api/v1/sentinels/${encodeURIComponent(sentinelId)}`,
      {
        method: 'PATCH',
        body: JSON.stringify({ state }),
      }
    );
  },

  getJourneys(): Promise<{ journeys: JourneyModel[] }> {
    return requestJson<{ journeys: JourneyModel[] }>('/api/v1/journeys');
  },

  getIncidents(): Promise<{ incidents: EpisodicIncident[] }> {
    return requestJson<{ incidents: EpisodicIncident[] }>('/api/v1/incidents');
  },

  getIncident(incidentId: string): Promise<EpisodicIncident> {
    return requestJson<EpisodicIncident>(
      `/api/v1/incidents/${encodeURIComponent(incidentId)}`
    );
  },

  getApprovals(): Promise<{ approvals: ApprovalCard[] }> {
    return requestJson<{ approvals: ApprovalCard[] }>('/api/v1/approvals');
  },

  decideApproval(
    approvalId: string,
    action: 'approve' | 'reject' | 'snooze',
    note?: string
  ): Promise<ApprovalCard> {
    return requestJson<ApprovalCard>(
      `/api/v1/approvals/${encodeURIComponent(approvalId)}:${action}`,
      {
        method: 'POST',
        body: JSON.stringify({ note: note ?? '' }),
      }
    );
  },

  getChanges(): Promise<{ changes: ChangeEvent[] }> {
    return requestJson<{ changes: ChangeEvent[] }>('/api/v1/changes');
  },

  getCostAndDrift(): Promise<CostAndDriftOverview> {
    return requestJson<CostAndDriftOverview>('/api/v1/cost');
  },

  remediateDrift(
    driftId: string,
    mode: 'restore_attribute' | 'apply_dispatch'
  ): Promise<TerraformDriftItem> {
    return requestJson<TerraformDriftItem>(
      `/api/v1/cost/drift/${encodeURIComponent(driftId)}:${mode}`,
      {
        method: 'POST',
        body: JSON.stringify({}),
      }
    );
  },

  getChaosDrills(): Promise<{ drills: ChaosDrill[] }> {
    return requestJson<{ drills: ChaosDrill[] }>('/api/v1/chaos/drills');
  },

  controlChaosDrill(
    drillId: string,
    action: 'start' | 'stop'
  ): Promise<ChaosDrill> {
    return requestJson<ChaosDrill>(
      `/api/v1/chaos/drills/${encodeURIComponent(drillId)}:${action}`,
      {
        method: 'POST',
        body: JSON.stringify({}),
      }
    );
  },

  invokeCapability(
    capabilityId: string,
    args: Record<string, unknown>
  ): Promise<Record<string, unknown>> {
    return requestJson<Record<string, unknown>>(
      `/api/v1/capabilities/${encodeURIComponent(capabilityId)}:invoke`,
      {
        method: 'POST',
        body: JSON.stringify(args),
      }
    );
  },

  setKillSwitch(
    enabled: boolean,
    reason: string
  ): Promise<PerceptionReport['operator_state']> {
    return requestJson<PerceptionReport['operator_state']>(
      '/api/v1/tenant/kill-switch',
      {
        method: 'POST',
        body: JSON.stringify({ enabled, reason }),
      }
    );
  },

  pauseTenant(): Promise<PerceptionReport['operator_state']> {
    return requestJson<PerceptionReport['operator_state']>(
      '/api/v1/tenant/pause',
      {
        method: 'POST',
        body: JSON.stringify({}),
      }
    );
  },

  resumeTenant(): Promise<PerceptionReport['operator_state']> {
    return requestJson<PerceptionReport['operator_state']>(
      '/api/v1/tenant/resume',
      {
        method: 'POST',
        body: JSON.stringify({}),
      }
    );
  },

  setHibernate(enabled: boolean): Promise<PerceptionReport['operator_state']> {
    return requestJson<PerceptionReport['operator_state']>('/api/v1/hibernate', {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    });
  },

  setSimulatorState(
    enabled: boolean
  ): Promise<PerceptionReport['operator_state']> {
    return requestJson<PerceptionReport['operator_state']>(
      '/api/v1/simulator/state',
      {
        method: 'POST',
        body: JSON.stringify({ enabled }),
      }
    );
  },

  triggerSimulatorBurst(
    count: number
  ): Promise<PerceptionReport['operator_state']> {
    return requestJson<PerceptionReport['operator_state']>(
      '/api/v1/simulator/journeys',
      {
        method: 'POST',
        body: JSON.stringify({ count }),
      }
    );
  },

  async fetchDatasetSettled(): Promise<SettledDatasetResult> {
    const results = await Promise.allSettled([
      this.getMe(),
      this.getPerception(),
      this.getCourt(),
      this.getInvariants(),
      this.getSentinels(),
      this.getJourneys(),
      this.getIncidents(),
      this.getApprovals(),
      this.getChanges(),
      this.getCostAndDrift(),
      this.getChaosDrills(),
    ]);

    const partial: Partial<CockpitDataset> = {};
    const errors: Partial<Record<keyof CockpitDataset, CockpitApiError>> = {};
    let iapExpired = false;
    let anySucceeded = false;
    let firstError: CockpitApiError | null = null;

    const recordErr = (key: keyof CockpitDataset, reason: unknown, endpoint: string) => {
      const apiErr = toCockpitError(reason, endpoint);
      errors[key] = apiErr;
      if (apiErr.code === 'IAP_SESSION_EXPIRED' || apiErr.status === 401) {
        iapExpired = true;
      }
      if (!firstError) firstError = apiErr;
    };

    const [
      meRes,
      perceiveRes,
      courtRes,
      invariantsRes,
      sentinelsRes,
      journeysRes,
      incidentsRes,
      approvalsRes,
      changesRes,
      costRes,
      drillsRes,
    ] = results;

    if (meRes.status === 'fulfilled') {
      partial.me = meRes.value;
      anySucceeded = true;
    } else {
      recordErr('me', meRes.reason, '/api/v1/me');
    }

    if (perceiveRes.status === 'fulfilled') {
      partial.perceive = perceiveRes.value;
      anySucceeded = true;
    } else {
      recordErr('perceive', perceiveRes.reason, '/api/v1/perceive');
    }

    if (courtRes.status === 'fulfilled') {
      partial.court = courtRes.value.agents;
      anySucceeded = true;
    } else {
      recordErr('court', courtRes.reason, '/api/v1/court');
    }

    if (invariantsRes.status === 'fulfilled') {
      partial.invariants = invariantsRes.value.invariants;
      anySucceeded = true;
    } else {
      recordErr('invariants', invariantsRes.reason, '/api/v1/invariants');
    }

    if (sentinelsRes.status === 'fulfilled') {
      partial.sentinels = sentinelsRes.value.sentinels;
      anySucceeded = true;
    } else {
      recordErr('sentinels', sentinelsRes.reason, '/api/v1/sentinels');
    }

    if (journeysRes.status === 'fulfilled') {
      partial.journeys = journeysRes.value.journeys;
      anySucceeded = true;
    } else {
      recordErr('journeys', journeysRes.reason, '/api/v1/journeys');
    }

    if (incidentsRes.status === 'fulfilled') {
      partial.incidents = incidentsRes.value.incidents;
      anySucceeded = true;
    } else {
      recordErr('incidents', incidentsRes.reason, '/api/v1/incidents');
    }

    if (approvalsRes.status === 'fulfilled') {
      partial.approvals = approvalsRes.value.approvals;
      anySucceeded = true;
    } else {
      recordErr('approvals', approvalsRes.reason, '/api/v1/approvals');
    }

    if (changesRes.status === 'fulfilled') {
      partial.changes = changesRes.value.changes;
      anySucceeded = true;
    } else {
      recordErr('changes', changesRes.reason, '/api/v1/changes');
    }

    if (costRes.status === 'fulfilled') {
      partial.cost = costRes.value;
      anySucceeded = true;
    } else {
      recordErr('cost', costRes.reason, '/api/v1/cost');
    }

    if (drillsRes.status === 'fulfilled') {
      partial.drills = drillsRes.value.drills;
      anySucceeded = true;
    } else {
      recordErr('drills', drillsRes.reason, '/api/v1/chaos/drills');
    }

    return {
      partial,
      errors,
      iapExpired,
      anySucceeded,
      firstError,
    };
  },
};

export interface SseSubscriptionOptions {
  onEvent: (eventType: string, payload: unknown) => void;
  onStatusChange?: (status: 'connected' | 'reconnecting' | 'polling') => void;
}

export function subscribeCockpitStream(options: SseSubscriptionOptions): () => void {
  if (typeof window === 'undefined' || typeof window.EventSource === 'undefined') {
    options.onStatusChange?.('polling');
    return () => {};
  }

  let es: EventSource | null = null;
  let retryTimeout: ReturnType<typeof setTimeout> | null = null;
  let retryAttempt = 0;
  let disposed = false;

  const connect = () => {
    if (disposed) return;
    try {
      es = new window.EventSource('/api/v1/events/stream');
      es.onopen = () => {
        retryAttempt = 0;
        options.onStatusChange?.('connected');
      };
      es.onmessage = (evt) => {
        try {
          const parsed = JSON.parse(evt.data) as { type?: string; data?: unknown };
          options.onEvent(parsed.type ?? 'message', parsed.data ?? parsed);
        } catch {
          options.onEvent('message', evt.data);
        }
      };
      es.onerror = () => {
        if (es) {
          es.close();
          es = null;
        }
        if (disposed) return;
        options.onStatusChange?.('reconnecting');
        const delayMs = Math.min(1000 * Math.pow(2, retryAttempt), 15000);
        retryAttempt += 1;
        retryTimeout = setTimeout(connect, delayMs);
      };
    } catch {
      options.onStatusChange?.('polling');
    }
  };

  connect();

  return () => {
    disposed = true;
    if (retryTimeout) clearTimeout(retryTimeout);
    if (es) es.close();
  };
}
