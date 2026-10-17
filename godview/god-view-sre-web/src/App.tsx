import { RefreshCw, Sliders, UserCheck, X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  CockpitApiError,
  cockpitApi,
  subscribeCockpitStream,
} from './api/client';
import type {
  CockpitDataset,
  CostAndDriftOverview,
  IapUserIdentity,
  PerceptionReport,
  SentinelDefinition,
} from './api/types';
import { AgentDetailTab } from './components/AgentDetailTab';
import { ApprovalsTab } from './components/ApprovalsTab';
import { ChangesTab } from './components/ChangesTab';
import { ControlsTab } from './components/ControlsTab';
import { CostTab } from './components/CostTab';
import { IncidentsTab } from './components/IncidentsTab';
import { InvariantsTab } from './components/InvariantsTab';
import { JourneysTab } from './components/JourneysTab';
import { OrganismTab } from './components/OrganismTab';
import { Badge, Button, TableSkeleton, formatEnumLabel } from './components/ui';

export type CockpitRouteTab =
  | 'organism'
  | 'agent'
  | 'invariants'
  | 'journeys'
  | 'incidents'
  | 'approvals'
  | 'changes'
  | 'cost'
  | 'controls';

interface ParsedHashRoute {
  tab: CockpitRouteTab;
  param: string | null;
}

function parseWindowHash(rawHash: string): ParsedHashRoute {
  const cleaned = rawHash.replace(/^#\/?/, '').trim();
  if (!cleaned || cleaned === 'organism') {
    return { tab: 'organism', param: null };
  }
  if (cleaned.startsWith('agent/')) {
    return {
      tab: 'agent',
      param: decodeURIComponent(cleaned.slice('agent/'.length)),
    };
  }
  if (cleaned === 'agent') {
    return { tab: 'agent', param: null };
  }
  if (cleaned.startsWith('incident/')) {
    return {
      tab: 'incidents',
      param: decodeURIComponent(cleaned.slice('incident/'.length)),
    };
  }
  if (cleaned === 'incidents') {
    return { tab: 'incidents', param: null };
  }
  if (cleaned === 'invariants') return { tab: 'invariants', param: null };
  if (cleaned === 'journeys') return { tab: 'journeys', param: null };
  if (cleaned === 'approvals') return { tab: 'approvals', param: null };
  if (cleaned === 'changes') return { tab: 'changes', param: null };
  if (cleaned === 'cost') return { tab: 'cost', param: null };
  if (cleaned === 'controls') return { tab: 'controls', param: null };

  return { tab: 'organism', param: null };
}

function getMockQueryMode(): string | null {
  if (typeof window === 'undefined') return null;
  const params = new URLSearchParams(window.location.search);
  return params.get('mock');
}

const FALLBACK_ME: IapUserIdentity = {
  email: 'operator@unavailable',
  principal: 'accounts.google.com:unavailable',
  role: 'SRE Lead / CTO',
  iap_authenticated: true,
};

const FALLBACK_PERCEIVE: PerceptionReport = {
  env_id: 'env-degraded',
  sovereign_id: 'sovereign-degraded',
  sovereign_status: 'degraded',
  heartbeat_at: new Date().toISOString(),
  sentinel_cadence_s: 10,
  tokens_at_rest: 0,
  sovereign_budget_daily_usd: 1.5,
  sovereign_spent_today_usd: 0,
  court_ceiling_daily_usd: 4.5,
  monthly_ceiling_usd: 150,
  escalation_reserve_pct: 20,
  reasoning_model: 'model-unavailable',
  escalation_model: 'model-unavailable',
  embedding_model: 'model-unavailable',
  managed_scope_label: 'godview_managed=true',
  warm_tier_spec: '1 vCPU / 512 MiB',
  active_window_label: '08:00–20:00 IST',
  approval_window_label: '08:00–22:00 IST',
  hibernate_job_name: 'god-view-hibernate-job',
  memory_db_name: 'god-view-memory',
  simulator_target_daily: 200,
  simulator_image_ratio_label: '15% image',
  controls_enabled: true,
  senses: [],
  discovered_services: [],
  discovered_edges: [],
  discovered_entities: [],
  operator_state: {
    kill_switch_enabled: false,
    kill_switch_reason: null,
    kill_switch_actor: null,
    kill_switch_updated_at: null,
    tenant_paused: false,
    pause_schedule_ist: '20:00–08:00 IST',
    hibernated: false,
    simulator_enabled: false,
    simulator_window_ist: '08:00–20:00 IST',
    simulator_journeys_today: 0,
    simulator_target_per_day: 200,
    simulator_specimen_ratio: '15% image / 85% text-only',
  },
};

const FALLBACK_COST: CostAndDriftOverview = {
  monthly_ceiling_usd: 150,
  current_month_spend_usd: 0,
  projected_active_month_usd: 0,
  projected_hibernated_month_usd: 0,
  daily_court_ceiling_usd: 4.5,
  daily_court_spent_usd: 0,
  escalation_reserve_pct: 20,
  budget_thresholds_pct: [50, 80, 100],
  tfstate_uri: 'gs://unavailable/default.tfstate',
  last_drift_scan_at: new Date().toISOString(),
  tier_budgets_summary: 'App / Biz / Change $0.75/d · Data / Platform $0.50/d · Sovereign $1.50/d',
  cost_findings_subtitle: 'Surfaces idle Reasoning Engines, oversized tiers, and specimen pool levers',
  cost_findings: [],
  drift_diffs: [],
};

export function App() {
  const [route, setRoute] = useState<ParsedHashRoute>(() =>
    parseWindowHash(window.location.hash)
  );
  const [dataset, setDataset] = useState<CockpitDataset | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [sliceErrors, setSliceErrors] = useState<
    Partial<Record<keyof CockpitDataset, CockpitApiError>>
  >({});
  const [iapExpired, setIapExpired] = useState<boolean>(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [secondsSinceUpdate, setSecondsSinceUpdate] = useState<number>(0);
  const [streamStatus, setStreamStatus] = useState<
    'connected' | 'reconnecting' | 'polling'
  >('polling');
  const [drawerOpen, setDrawerOpen] = useState<boolean>(false);

  const drawerTriggerRef = useRef<HTMLButtonElement | null>(null);
  const drawerCloseRef = useRef<HTMLButtonElement | null>(null);

  const navigateToHash = useCallback((nextHash: string) => {
    const normalized = nextHash.startsWith('#') ? nextHash : `#${nextHash}`;
    window.history.replaceState(null, '', normalized);
    setRoute(parseWindowHash(normalized));
  }, []);

  useEffect(() => {
    const onHashChange = () => {
      setRoute(parseWindowHash(window.location.hash));
    };
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  const loadAll = useCallback(async (showSkeleton = false) => {
    const mockMode = getMockQueryMode();
    if (mockMode === 'skeleton') {
      setLoading(true);
      setDataset(null);
      setError(null);
      return;
    }
    if (mockMode === 'error') {
      setLoading(false);
      setDataset(null);
      setError(
        'HTTP_503: Sovereign control plane upstream unreachable (/api/v1/perceive)'
      );
      return;
    }

    if (showSkeleton) setLoading(true);
    setError(null);

    try {
      const settled = await cockpitApi.fetchDatasetSettled();
      if (settled.iapExpired) {
        setIapExpired(true);
        setError(
          settled.firstError
            ? `${settled.firstError.code}: ${settled.firstError.message}`
            : 'IAP_SESSION_EXPIRED: Session expired — Reload to sign in'
        );
        setLoading(false);
        return;
      }

      setIapExpired(false);
      setSliceErrors(settled.errors);

      if (!settled.anySucceeded) {
        setError(
          settled.firstError
            ? `${settled.firstError.code}: ${settled.firstError.message} (${settled.firstError.endpoint})`
            : 'Failed to load Sovereign control plane data'
        );
        setLoading(false);
        return;
      }

      setDataset((prev) => {
        const merged: CockpitDataset = {
          me: settled.partial.me ?? prev?.me ?? FALLBACK_ME,
          perceive:
            settled.partial.perceive ?? prev?.perceive ?? FALLBACK_PERCEIVE,
          court: settled.partial.court ?? prev?.court ?? [],
          invariants: settled.partial.invariants ?? prev?.invariants ?? [],
          sentinels: settled.partial.sentinels ?? prev?.sentinels ?? [],
          journeys: settled.partial.journeys ?? prev?.journeys ?? [],
          incidents: settled.partial.incidents ?? prev?.incidents ?? [],
          approvals:
            mockMode === 'empty-approvals'
              ? []
              : settled.partial.approvals ?? prev?.approvals ?? [],
          changes: settled.partial.changes ?? prev?.changes ?? [],
          cost: settled.partial.cost ?? prev?.cost ?? FALLBACK_COST,
          drills: settled.partial.drills ?? prev?.drills ?? [],
        };
        return merged;
      });
      setSecondsSinceUpdate(0);
    } catch (err) {
      if (err instanceof CockpitApiError) {
        if (err.code === 'IAP_SESSION_EXPIRED') {
          setIapExpired(true);
        }
        setError(`${err.code}: ${err.message} (${err.endpoint})`);
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError('Failed to load Sovereign control plane data');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAll(true);
  }, [loadAll]);

  // 1s age counter + 10s fallback polling ONLY when SSE is not connected
  useEffect(() => {
    const secTimer = setInterval(() => {
      setSecondsSinceUpdate((prev) => prev + 1);
    }, 1000);

    const pollTimer =
      streamStatus !== 'connected'
        ? setInterval(() => {
            void loadAll(false);
          }, 10000)
        : null;

    return () => {
      clearInterval(secTimer);
      if (pollTimer) clearInterval(pollTimer);
    };
  }, [loadAll, streamStatus]);

  // SSE stream subscription: triggers dataset refresh on event
  useEffect(() => {
    const unsubscribe = subscribeCockpitStream({
      onEvent: () => {
        void loadAll(false);
      },
      onStatusChange: (st) => {
        setStreamStatus(st);
      },
    });
    return unsubscribe;
  }, [loadAll]);

  // Drawer keyboard Escape & focus management
  useEffect(() => {
    if (!drawerOpen) return;
    drawerCloseRef.current?.focus();

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        setDrawerOpen(false);
        drawerTriggerRef.current?.focus();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [drawerOpen]);

  const closeDrawer = useCallback(() => {
    setDrawerOpen(false);
    drawerTriggerRef.current?.focus();
  }, []);

  const handleSelectAgent = useCallback(
    (agentId: string) => {
      navigateToHash(`#agent/${encodeURIComponent(agentId)}`);
    },
    [navigateToHash]
  );

  const handleSelectIncident = useCallback(
    (incidentId: string) => {
      navigateToHash(`#incident/${encodeURIComponent(incidentId)}`);
    },
    [navigateToHash]
  );

  const runMutation = async (key: string, fn: () => Promise<void>) => {
    setBusyKey(key);
    setActionError(null);
    try {
      await fn();
      setSecondsSinceUpdate(0);
    } catch (err) {
      if (err instanceof CockpitApiError && err.code === 'IAP_SESSION_EXPIRED') {
        setIapExpired(true);
      }
      setActionError(
        err instanceof Error
          ? err.message
          : 'Operation failed against Sovereign API'
      );
    } finally {
      setBusyKey(null);
    }
  };

  const navItems: Array<{
    tab: CockpitRouteTab;
    hash: string;
    label: string;
    badge?: string | number;
  }> = [
    {
      tab: 'organism',
      hash: '#organism',
      label: 'Organism Court',
      badge: dataset?.court.length,
    },
    {
      tab: 'agent',
      hash: dataset?.court[0]
        ? `#agent/${encodeURIComponent(dataset.court[0].id)}`
        : '#agent',
      label: 'Living Models',
    },
    {
      tab: 'invariants',
      hash: '#invariants',
      label: 'Invariants',
      badge: dataset?.invariants.length,
    },
    {
      tab: 'journeys',
      hash: '#journeys',
      label: 'Journeys',
      badge: dataset?.journeys.length,
    },
    {
      tab: 'incidents',
      hash: '#incidents',
      label: 'Incidents',
      badge: dataset?.incidents.length,
    },
    {
      tab: 'approvals',
      hash: '#approvals',
      label: 'Approvals',
      badge: dataset?.approvals.filter((a) => a.decision === 'pending').length,
    },
    {
      tab: 'changes',
      hash: '#changes',
      label: 'Changes',
      badge: dataset?.changes.length,
    },
    {
      tab: 'cost',
      hash: '#cost',
      label: 'Cost & Drift',
      badge: dataset?.cost.drift_diffs.filter((d) => d.status === 'drifted')
        .length,
    },
    {
      tab: 'controls',
      hash: '#controls',
      label: 'Controls & Drills',
      badge: dataset?.drills.filter((d) => d.is_active).length || undefined,
    },
  ];

  const sliceErrorKeys = Object.keys(sliceErrors) as Array<
    keyof CockpitDataset
  >;

  const renderHeartbeatBadge = (perceive: PerceptionReport) => {
    if (perceive.sovereign_status === 'healthy') {
      return (
        <Badge variant="success" dot>
          Heartbeat OK · {perceive.sentinel_cadence_s}s tick ·{' '}
          {perceive.tokens_at_rest} tok at rest
        </Badge>
      );
    }
    if (perceive.sovereign_status === 'hibernating') {
      return (
        <Badge variant="warning" dot>
          Hibernating · {perceive.sentinel_cadence_s}s tick ·{' '}
          {perceive.tokens_at_rest} tok at rest
        </Badge>
      );
    }
    return (
      <Badge variant="danger" dot>
        Heartbeat degraded · {perceive.sentinel_cadence_s}s tick
      </Badge>
    );
  };

  const renderConnectionLabel = () => {
    if (streamStatus === 'connected') {
      return `Live · updated ${secondsSinceUpdate}s ago`;
    }
    if (streamStatus === 'reconnecting') {
      return `Reconnecting… · updated ${secondsSinceUpdate}s ago`;
    }
    return `Polling 10s · updated ${secondsSinceUpdate}s ago`;
  };

  return (
    <div className="min-h-screen bg-white text-slate-700">
      {/* Hairline Google four-colour top rule */}
      <div className="google-top-hairline w-full" />

      {/* Global Top Bar */}
      <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/95 backdrop-blur-sm">
        <div className="mx-auto flex max-w-[1440px] flex-wrap items-center justify-between gap-3 px-6 py-3">
          {/* Brand & Environment Chip */}
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2">
              <div className="flex h-7 w-7 items-center justify-center rounded border border-slate-300 bg-slate-900 text-xs font-semibold text-white">
                GV
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-slate-900">
                    God-View Cockpit
                  </span>
                  <span className="text-xs text-slate-500">
                    Sovereign SRE Organism v4.0
                  </span>
                </div>
              </div>
            </div>

            {dataset && (
              <div className="flex flex-wrap items-center gap-2 border-l border-slate-200 pl-3 text-xs">
                <Badge variant="outline" className="font-mono">
                  {dataset.perceive.env_id}
                </Badge>
                {renderHeartbeatBadge(dataset.perceive)}
                {dataset.perceive.operator_state.kill_switch_enabled && (
                  <Badge variant="danger" dot>
                    Kill switch engaged
                  </Badge>
                )}
              </div>
            )}
          </div>

          {/* Right: Live Sync Status, Quick Chaos Drawer Trigger & IAP User Chip */}
          <div className="flex flex-wrap items-center gap-2.5 text-xs">
            <div className="flex items-center gap-1.5 rounded border border-slate-200 bg-slate-50 px-2.5 py-1 text-slate-600 num-tabular">
              <span
                className={`h-2 w-2 rounded-full ${
                  streamStatus === 'connected'
                    ? 'bg-green-600'
                    : streamStatus === 'reconnecting'
                    ? 'bg-amber-600'
                    : 'bg-blue-600'
                }`}
              />
              <span>{renderConnectionLabel()}</span>
              <button
                type="button"
                aria-label="Refresh Sovereign Data"
                onClick={() => void loadAll(false)}
                className="ml-1 rounded p-0.5 text-slate-600 hover:bg-slate-200 hover:text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-600"
              >
                <RefreshCw className="h-3 w-3" />
              </button>
            </div>

            {dataset?.perceive.controls_enabled && (
              <button
                ref={drawerTriggerRef}
                type="button"
                onClick={() => setDrawerOpen(true)}
                className="inline-flex items-center gap-1.5 rounded border border-slate-300 bg-white px-2.5 py-1 text-xs font-semibold text-slate-800 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-blue-600 focus:ring-offset-1"
              >
                <Sliders className="h-3.5 w-3.5 text-blue-700" />
                Chaos Drills Drawer
              </button>
            )}

            {dataset && (
              <div
                className="inline-flex items-center gap-1.5 rounded border border-slate-200 bg-slate-50 px-2.5 py-1 font-mono text-xs text-slate-900"
                title={dataset.me.principal}
              >
                <UserCheck className="h-3.5 w-3.5 text-green-700" />
                <span>{dataset.me.email}</span>
              </div>
            )}
          </div>
        </div>

        {/* Hash-Routed Navigation Bar */}
        <nav
          aria-label="Cockpit primary navigation"
          className="mx-auto flex max-w-[1440px] items-center gap-1 overflow-x-auto border-t border-slate-100 px-6"
        >
          {navItems.map((item) => {
            const active = route.tab === item.tab;
            return (
              <button
                key={item.tab}
                type="button"
                onClick={() => navigateToHash(item.hash)}
                className={`inline-flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-xs whitespace-nowrap transition-colors focus:outline-none focus:ring-2 focus:ring-inset focus:ring-blue-600 ${
                  active
                    ? 'border-blue-600 text-blue-700 font-semibold'
                    : 'border-transparent text-slate-600 hover:border-slate-300 hover:text-slate-900'
                }`}
              >
                <span>{item.label}</span>
                {item.badge !== undefined && (
                  <span
                    className={`rounded px-1.5 py-0.2 font-mono text-[11px] num-tabular ${
                      active
                        ? 'bg-blue-50 text-blue-800 font-semibold'
                        : 'bg-slate-100 text-slate-700'
                    }`}
                  >
                    {item.badge}
                  </span>
                )}
              </button>
            );
          })}
        </nav>
      </header>

      {/* Main Content Container */}
      <main className="mx-auto max-w-[1440px] px-6 py-6">
        {/* IAP Session Expiry Banner */}
        {iapExpired && (
          <div
            role="alert"
            data-testid="iap-session-expired-banner"
            className="mb-4 flex flex-wrap items-center justify-between gap-4 rounded-md border border-amber-300 bg-amber-50 px-5 py-4 text-slate-900"
          >
            <div>
              <h2 className="text-sm font-semibold text-amber-900">
                Session expired — Reload to sign in
              </h2>
              <p className="mt-1 text-xs text-amber-800">
                Your Identity-Aware Proxy (IAP) session cookie has expired or redirected to Google login. Reload the Cockpit to re-authenticate with IAP.
              </p>
            </div>
            <Button
              variant="primary"
              size="sm"
              onClick={() => window.location.reload()}
            >
              Reload to sign in
            </Button>
          </div>
        )}

        {/* Partial Slice Error Banner (keeps healthy tabs and Kill Switch accessible) */}
        {dataset && sliceErrorKeys.length > 0 && !iapExpired && (
          <div
            role="alert"
            data-testid="partial-slice-error-banner"
            className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-xs text-amber-900"
          >
            <div>
              <span className="font-semibold">
                Stale data · Retrying failed slice ({sliceErrorKeys.join(', ')})
              </span>
              <p className="mt-0.5 font-mono text-amber-800">
                {sliceErrors[sliceErrorKeys[0]]?.code}:{' '}
                {sliceErrors[sliceErrorKeys[0]]?.message}
              </p>
            </div>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void loadAll(false)}
            >
              Retry Failed Slice
            </Button>
          </div>
        )}

        {actionError && (
          <div
            role="alert"
            className="mb-4 flex items-center justify-between rounded border border-red-300 bg-red-50 px-4 py-2.5 text-xs text-red-900"
          >
            <span>Action Error: {actionError}</span>
            <button
              type="button"
              onClick={() => setActionError(null)}
              className="rounded font-semibold underline focus:outline-none focus:ring-2 focus:ring-red-600"
            >
              Dismiss
            </button>
          </div>
        )}

        {loading && !dataset && <TableSkeleton rows={6} />}

        {error && !dataset && !iapExpired && (
          <div
            role="alert"
            className="rounded-md border border-red-300 bg-red-50 p-6 text-slate-900"
          >
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h2 className="text-sm font-semibold text-red-900">
                  Sovereign Control API Error
                </h2>
                <p className="mt-1 font-mono text-xs text-red-800">{error}</p>
                <p className="mt-2 text-xs text-slate-700">
                  The Cockpit never falls back to synthetic or fake data on production API errors. Verify IAP session and Sovereign Cloud Run service status.
                </p>
              </div>
              <Button
                variant="primary"
                size="sm"
                onClick={() => void loadAll(true)}
              >
                Retry Connection
              </Button>
            </div>
          </div>
        )}

        {dataset && (
          <>
            {route.tab === 'organism' && (
              <OrganismTab
                perceive={dataset.perceive}
                court={dataset.court}
                onSelectAgent={handleSelectAgent}
                onControlAgent={(agentId, action) =>
                  runMutation(`${agentId}:${action}`, async () => {
                    const updated = await cockpitApi.controlCourtAgent(
                      agentId,
                      action
                    );
                    setDataset((prev) =>
                      prev
                        ? {
                            ...prev,
                            court: prev.court.map((a) =>
                              a.id === updated.id ? updated : a
                            ),
                          }
                        : prev
                    );
                  })
                }
                onRePerceive={() =>
                  runMutation('perceive', async () => {
                    const updated = await cockpitApi.triggerPerceive(true);
                    setDataset((prev) =>
                      prev ? { ...prev, perceive: updated } : prev
                    );
                  })
                }
                busyKey={busyKey}
              />
            )}

            {route.tab === 'agent' && (
              <AgentDetailTab
                agents={dataset.court}
                selectedAgentId={route.param}
                onSelectAgent={handleSelectAgent}
                onBackToOrganism={() => navigateToHash('#organism')}
                onToggleWhitelist={(recordId, whitelisted) =>
                  runMutation(`whitelist:${recordId}`, async () => {
                    const updatedRec = await cockpitApi.toggleWhitelist(
                      recordId,
                      whitelisted
                    );
                    setDataset((prev) =>
                      prev
                        ? {
                            ...prev,
                            court: prev.court.map((a) => ({
                              ...a,
                              trust_records: a.trust_records.map((r) =>
                                r.id === updatedRec.id ? updatedRec : r
                              ),
                            })),
                          }
                        : prev
                    );
                  })
                }
                onControlAgent={(agentId, action) =>
                  runMutation(`${agentId}:${action}`, async () => {
                    const updated = await cockpitApi.controlCourtAgent(
                      agentId,
                      action
                    );
                    setDataset((prev) =>
                      prev
                        ? {
                            ...prev,
                            court: prev.court.map((a) =>
                              a.id === updated.id ? updated : a
                            ),
                          }
                        : prev
                    );
                  })
                }
                busyKey={busyKey}
              />
            )}

            {route.tab === 'invariants' && (
              <InvariantsTab
                invariants={dataset.invariants}
                sentinels={dataset.sentinels}
                onSelectAgent={handleSelectAgent}
                onUpdateSentinel={(
                  sentinelId: string,
                  state: SentinelDefinition['state']
                ) =>
                  runMutation(`sentinel:${sentinelId}`, async () => {
                    const updated = await cockpitApi.updateSentinelState(
                      sentinelId,
                      state
                    );
                    setDataset((prev) =>
                      prev
                        ? {
                            ...prev,
                            sentinels: prev.sentinels.map((s) =>
                              s.sentinel_id === updated.sentinel_id
                                ? updated
                                : s
                            ),
                          }
                        : prev
                    );
                  })
                }
                onCompileSentinel={(invariantId: string) =>
                  runMutation(`compile:${invariantId}`, async () => {
                    const created = await cockpitApi.compileSentinel(
                      invariantId
                    );
                    setDataset((prev) =>
                      prev
                        ? {
                            ...prev,
                            sentinels: [...prev.sentinels, created],
                          }
                        : prev
                    );
                  })
                }
                busyKey={busyKey}
              />
            )}

            {route.tab === 'journeys' && (
              <JourneysTab
                journeys={dataset.journeys}
                onSelectAgent={handleSelectAgent}
              />
            )}

            {route.tab === 'incidents' && (
              <IncidentsTab
                incidents={dataset.incidents}
                selectedIncidentId={route.param}
                onSelectIncident={handleSelectIncident}
                onSelectAgent={handleSelectAgent}
              />
            )}

            {route.tab === 'approvals' && (
              <ApprovalsTab
                approvals={dataset.approvals}
                currentUserEmail={dataset.me.email}
                approvalWindowLabel={dataset.perceive.approval_window_label}
                referenceNowIso={dataset.perceive.heartbeat_at}
                onDecideApproval={(approvalId, action) =>
                  runMutation(`${approvalId}:${action}`, async () => {
                    const updated = await cockpitApi.decideApproval(
                      approvalId,
                      action
                    );
                    setDataset((prev) =>
                      prev
                        ? {
                            ...prev,
                            approvals: prev.approvals.map((a) =>
                              a.approval_id === updated.approval_id
                                ? updated
                                : a
                            ),
                          }
                        : prev
                    );
                  })
                }
                onSelectIncident={handleSelectIncident}
                onSelectAgent={handleSelectAgent}
                busyKey={busyKey}
              />
            )}

            {route.tab === 'changes' && (
              <ChangesTab
                changes={dataset.changes}
                onSelectIncident={handleSelectIncident}
              />
            )}

            {route.tab === 'cost' && (
              <CostTab
                cost={dataset.cost}
                court={dataset.court}
                onRemediateDrift={(driftId, mode) =>
                  runMutation(`drift:${driftId}:${mode}`, async () => {
                    const updated = await cockpitApi.remediateDrift(
                      driftId,
                      mode
                    );
                    setDataset((prev) =>
                      prev
                        ? {
                            ...prev,
                            cost: {
                              ...prev.cost,
                              drift_diffs: prev.cost.drift_diffs.map((d) =>
                                d.id === updated.id ? updated : d
                              ),
                            },
                          }
                        : prev
                    );
                  })
                }
                onSelectAgent={handleSelectAgent}
                busyKey={busyKey}
              />
            )}

            {route.tab === 'controls' && (
              <ControlsTab
                perceive={dataset.perceive}
                drills={dataset.drills}
                onSetKillSwitch={(enabled, reason) =>
                  runMutation('kill-switch', async () => {
                    const opState = await cockpitApi.setKillSwitch(
                      enabled,
                      reason
                    );
                    setDataset((prev) =>
                      prev
                        ? {
                            ...prev,
                            perceive: {
                              ...prev.perceive,
                              operator_state: opState,
                            },
                          }
                        : prev
                    );
                  })
                }
                onPauseTenant={() =>
                  runMutation('pause', async () => {
                    const opState = await cockpitApi.pauseTenant();
                    setDataset((prev) =>
                      prev
                        ? {
                            ...prev,
                            perceive: {
                              ...prev.perceive,
                              operator_state: opState,
                            },
                          }
                        : prev
                    );
                  })
                }
                onResumeTenant={() =>
                  runMutation('resume', async () => {
                    const opState = await cockpitApi.resumeTenant();
                    setDataset((prev) =>
                      prev
                        ? {
                            ...prev,
                            perceive: {
                              ...prev.perceive,
                              operator_state: opState,
                            },
                          }
                        : prev
                    );
                  })
                }
                onSetHibernate={(enabled) =>
                  runMutation('hibernate', async () => {
                    const opState = await cockpitApi.setHibernate(enabled);
                    setDataset((prev) =>
                      prev
                        ? {
                            ...prev,
                            perceive: {
                              ...prev.perceive,
                              operator_state: opState,
                            },
                          }
                        : prev
                    );
                  })
                }
                onSetSimulatorState={(enabled) =>
                  runMutation('simulator-state', async () => {
                    const opState = await cockpitApi.setSimulatorState(enabled);
                    setDataset((prev) =>
                      prev
                        ? {
                            ...prev,
                            perceive: {
                              ...prev.perceive,
                              operator_state: opState,
                            },
                          }
                        : prev
                    );
                  })
                }
                onTriggerSimulatorBurst={(count) =>
                  runMutation('simulator-burst', async () => {
                    const opState = await cockpitApi.triggerSimulatorBurst(
                      count
                    );
                    setDataset((prev) =>
                      prev
                        ? {
                            ...prev,
                            perceive: {
                              ...prev.perceive,
                              operator_state: opState,
                            },
                          }
                        : prev
                    );
                  })
                }
                onControlDrill={(drillId, action) =>
                  runMutation(`drill:${drillId}`, async () => {
                    const updated = await cockpitApi.controlChaosDrill(
                      drillId,
                      action
                    );
                    setDataset((prev) =>
                      prev
                        ? {
                            ...prev,
                            drills: prev.drills.map((d) =>
                              d.drill_id === updated.drill_id ? updated : d
                            ),
                          }
                        : prev
                    );
                  })
                }
                busyKey={busyKey}
              />
            )}
          </>
        )}
      </main>

      {/* Slide-out Chaos Drills Drawer (available from any tab) */}
      {drawerOpen && dataset && (
        <div
          className="fixed inset-0 z-50 flex justify-end bg-slate-900/30"
          role="dialog"
          aria-modal="true"
          aria-label="Chaos Drills Drawer"
          data-testid="chaos-drills-drawer-backdrop"
          onClick={(e) => {
            if (e.target === e.currentTarget) {
              closeDrawer();
            }
          }}
        >
          <div className="h-full w-full max-w-lg overflow-y-auto border-l border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex items-center justify-between border-b border-slate-200 pb-3">
              <div>
                <h2 className="text-sm font-semibold text-slate-900">
                  Opt-In Chaos Drills Drawer ({dataset.perceive.managed_scope_label})
                </h2>
                <p className="text-xs text-slate-500">
                  Trigger or halt proving-ground faults while watching live sentinels
                </p>
              </div>
              <button
                ref={drawerCloseRef}
                type="button"
                aria-label="Close Chaos Drills Drawer"
                onClick={closeDrawer}
                className="rounded p-1 text-slate-600 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-600"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="mt-4 space-y-2.5">
              {dataset.drills.map((drill) => (
                <div
                  key={drill.drill_id}
                  className="rounded border border-slate-200 bg-slate-50 p-3 text-xs"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <div className="flex items-center gap-1.5">
                        <span className="font-semibold text-slate-900">
                          {drill.title}
                        </span>
                        <Badge
                          variant={
                            drill.fault_class === 'BUSINESS'
                              ? 'info'
                              : 'warning'
                          }
                        >
                          {formatEnumLabel(drill.fault_class)}
                        </Badge>
                      </div>
                      <p className="mt-0.5 font-mono text-slate-600">
                        target: {drill.target_service} · {drill.mttd_target}
                      </p>
                    </div>
                    <Button
                      variant={drill.is_active ? 'secondary' : 'primary'}
                      size="sm"
                      onClick={() =>
                        void runMutation(`drill:${drill.drill_id}`, async () => {
                          const updated = await cockpitApi.controlChaosDrill(
                            drill.drill_id,
                            drill.is_active ? 'stop' : 'start'
                          );
                          setDataset((prev) =>
                            prev
                              ? {
                                  ...prev,
                                  drills: prev.drills.map((d) =>
                                    d.drill_id === updated.drill_id
                                      ? updated
                                      : d
                                  ),
                                }
                              : prev
                          );
                        })
                      }
                    >
                      {drill.is_active ? 'Stop' : 'Start'}
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
