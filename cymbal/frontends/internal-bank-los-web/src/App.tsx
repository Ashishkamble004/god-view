import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  Radio,
  ShieldAlert,
  UserCheck,
} from 'lucide-react';
import {
  LosApiError,
  fetchIapIdentity,
  fetchKycSpecimenDocument,
  fetchLosLoans,
  fetchTenantTopology,
  fetchTenantTrace,
} from './api/client';
import type {
  IapIdentityResponse,
  KycSignedUrlResponse,
  LoanApplication,
  ObservabilityTopology,
  TraceRecord,
} from './api/types';
import { Badge } from './components/Badge';
import { Button } from './components/Button';
import { useTelemetryStream } from './hooks/useTelemetryStream';
import {
  type QueueFilterCriteria,
  type SortDirection,
  type SortField,
  computeLosKpis,
  filterLoanQueue,
  formatInr,
  formatLatencyMs,
  sortLoanQueue,
} from './utils/queueUtils';
import { LedgersView, type LedgerSection } from './views/LedgersView';
import { QueueView } from './views/QueueView';
import { ReplayView } from './views/ReplayView';
import { TopologyView } from './views/TopologyView';
import { VaultView } from './views/VaultView';

type LosTab = 'queue' | 'vault' | 'ledgers' | 'replay' | 'topology';

function parseHashTab(hash: string): LosTab {
  const cleaned = hash
    .replace(/^#/, '')
    .split('/')[0]
    .trim()
    .toLowerCase();
  if (
    cleaned === 'queue' ||
    cleaned === 'vault' ||
    cleaned === 'ledgers' ||
    cleaned === 'replay' ||
    cleaned === 'topology'
  ) {
    return cleaned;
  }
  return 'queue';
}

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<LosTab>(() =>
    parseHashTab(window.location.hash)
  );

  // IAP Identity state
  const [iapUser, setIapUser] = useState<IapIdentityResponse>({
    email: 'Not signed in (no IAP header)',
    iap_authenticated: false,
    header_source: 'initializing',
  });

  // Loans queue state
  const [loans, setLoans] = useState<LoanApplication[]>([]);
  const [loansLoading, setLoansLoading] = useState<boolean>(true);
  const [loansError, setLoansError] = useState<string | null>(null);

  // Queue filter & sort state
  const [filterCriteria, setFilterCriteria] = useState<QueueFilterCriteria>({
    status: 'ALL',
    utilityType: 'ALL',
    syntheticMode: 'ALL',
    search: '',
  });
  const [sortField, setSortField] = useState<SortField>('created_at');
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc');

  // Selected loan for Vault Inspector / Dossier / Trace
  const [selectedLoanId, setSelectedLoanId] = useState<string>('');

  // KYC Vault signed URL state
  const [panDoc, setPanDoc] = useState<KycSignedUrlResponse | null>(null);
  const [aadhaarDoc, setAadhaarDoc] = useState<KycSignedUrlResponse | null>(
    null
  );
  const [vaultLoading, setVaultLoading] = useState<boolean>(false);

  // Ledgers sub-filter
  const [activeLedgerSection, setActiveLedgerSection] =
    useState<LedgerSection>('ALL');

  // Topology & Trace state
  const [topology, setTopology] = useState<ObservabilityTopology | null>(null);
  const [selectedTrace, setSelectedTrace] = useState<TraceRecord | null>(null);

  // SSE Telemetry Stream
  const { telemetry } = useTelemetryStream((ev) => {
    if (ev.loan_id && ev.status_to) {
      setLoans((prev) =>
        prev.map((l) =>
          l.loan_id === ev.loan_id
            ? { ...l, status: ev.status_to!, updated_at: ev.event_time }
            : l
        )
      );
    }
  });

  // Load initial data from /api/v1/me, /api/v1/loans, /api/v1/observability/topology
  const loadAllData = useCallback(async () => {
    setLoansLoading(true);
    setLoansError(null);
    const [identityRes, loansRes, topologyRes] = await Promise.allSettled([
      fetchIapIdentity(),
      fetchLosLoans(),
      fetchTenantTopology(),
    ]);

    if (identityRes.status === 'fulfilled') {
      setIapUser(identityRes.value);
    }

    if (topologyRes.status === 'fulfilled') {
      setTopology(topologyRes.value);
    }

    if (loansRes.status === 'fulfilled') {
      setLoans(loansRes.value);
      if (loansRes.value.length > 0) {
        setSelectedLoanId((prev) =>
          loansRes.value.some((l) => l.loan_id === prev)
            ? prev
            : loansRes.value[0].loan_id
        );
      }
    } else {
      const err = loansRes.reason;
      if (err instanceof LosApiError && err.isIapSessionExpired) {
        setIapUser({
          email: 'Not signed in (no IAP header)',
          iap_authenticated: false,
          header_source: 'session-expired',
        });
        setLoansError(
          'IAP session expired or missing X-Goog-Authenticated-User-Email header — reload to re-authenticate via Google Cloud Identity-Aware Proxy.'
        );
      } else {
        const msg =
          err instanceof Error
            ? err.message
            : 'Failed to refresh LOS queue from orchestrator.';
        setLoansError(msg);
      }
    }
    setLoansLoading(false);
  }, []);

  useEffect(() => {
    void loadAllData();
  }, [loadAllData]);

  // Sync window.location.hash
  useEffect(() => {
    const onHashChange = () => {
      setActiveTab(parseHashTab(window.location.hash));
      if (window.location.hash.includes('session-expired')) {
        void loadAllData();
      }
    };
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, [loadAllData]);

  const navigateTab = (tab: LosTab) => {
    window.location.hash = `#${tab}`;
    setActiveTab(tab);
  };

  const selectedLoan = useMemo<LoanApplication | null>(() => {
    if (loans.length === 0) return null;
    return loans.find((l) => l.loan_id === selectedLoanId) ?? loans[0];
  }, [loans, selectedLoanId]);

  // Load KYC specimen signed URLs whenever selectedLoan changes
  useEffect(() => {
    if (!selectedLoan) {
      setPanDoc(null);
      setAadhaarDoc(null);
      return;
    }
    let cancelled = false;
    setVaultLoading(true);
    Promise.all([
      fetchKycSpecimenDocument(selectedLoan.customer_id, 'pan').catch(
        () => null
      ),
      fetchKycSpecimenDocument(selectedLoan.customer_id, 'aadhaar').catch(
        () => null
      ),
    ])
      .then(([pan, aadhaar]) => {
        if (cancelled) return;
        setPanDoc(pan);
        setAadhaarDoc(aadhaar);
      })
      .finally(() => {
        if (!cancelled) setVaultLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedLoan]);

  // Load Trace Record when selectedLoan changes
  useEffect(() => {
    if (!selectedLoan) {
      setSelectedTrace(null);
      return;
    }
    let cancelled = false;
    fetchTenantTrace(selectedLoan.trace_id)
      .then((tr) => {
        if (!cancelled) setSelectedTrace(tr);
      })
      .catch(() => {
        if (!cancelled) setSelectedTrace(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedLoan]);

  // Filtered and sorted queue
  const visibleQueue = useMemo(() => {
    const filtered = filterLoanQueue(loans, filterCriteria);
    return sortLoanQueue(filtered, sortField, sortDirection);
  }, [loans, filterCriteria, sortField, sortDirection]);

  // KPI summary
  const kpis = useMemo(() => computeLosKpis(loans), [loans]);

  // Stuck / Replayable disbursements
  const replayQueueLoans = useMemo(() => {
    return loans.filter(
      (l) =>
        l.status === 'DISBURSEMENT_PENDING' ||
        l.disbursement?.status === 'PENDING' ||
        l.disbursement?.status === 'FAILED' ||
        l.disbursement?.status === 'REPLAYED'
    );
  }, [loans]);

  const handleSortColumn = (field: SortField) => {
    if (sortField === field) {
      setSortDirection((prev) => (prev === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortField(field);
      setSortDirection('desc');
    }
  };

  // Keyboard navigation on queue table
  const handleQueueKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (visibleQueue.length === 0) return;
    const currentIndex = visibleQueue.findIndex(
      (l) => l.loan_id === selectedLoan?.loan_id
    );
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      const nextIdx =
        currentIndex < visibleQueue.length - 1 ? currentIndex + 1 : 0;
      setSelectedLoanId(visibleQueue[nextIdx].loan_id);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      const prevIdx =
        currentIndex > 0 ? currentIndex - 1 : visibleQueue.length - 1;
      setSelectedLoanId(visibleQueue[prevIdx].loan_id);
    } else if (e.key === 'Enter' && currentIndex >= 0) {
      e.preventDefault();
      navigateTab('vault');
    }
  };

  return (
    <div className="min-h-screen bg-surface text-ink-body flex flex-col">
      {/* Top Institutional LOS Header Bar */}
      <header className="sticky top-0 z-30 bg-white border-b border-slate-200">
        <div className="max-w-[1440px] mx-auto px-6 py-2.5 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5 shrink-0">
            <div className="w-8 h-8 rounded bg-ink-heading flex items-center justify-center text-white font-semibold text-xs tracking-tight shrink-0">
              LOS
            </div>
            <div>
              <div className="flex items-center gap-1.5">
                <span className="text-sm font-semibold text-ink-heading tracking-tight whitespace-nowrap">
                  Cymbal Bank Internal LOS
                </span>
                <Badge variant="info" className="whitespace-nowrap">
                  IAP Protected
                </Badge>
                <Badge variant="outline" className="whitespace-nowrap">
                  Read-Only Ops
                </Badge>
              </div>
              <p className="text-xs text-ink-muted whitespace-nowrap">
                Five-Utility Underwriting · Text-Only Model Armor · Saga Replay
              </p>
            </div>
          </div>

          {/* Primary Hash-Routed Navigation Tabs */}
          <nav
            aria-label="Internal LOS Navigation"
            className="flex items-center gap-1 bg-surface p-1 rounded border border-slate-200 shrink-0"
          >
            <button
              id="los-tab-queue"
              type="button"
              onClick={() => navigateTab('queue')}
              className={`px-2.5 py-1.5 rounded text-xs font-semibold whitespace-nowrap transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue ${
                activeTab === 'queue'
                  ? 'bg-white text-gblue border border-slate-200 shadow-sm'
                  : 'text-ink-body hover:text-ink-heading'
              }`}
            >
              Live LOS Queue ({loans.length})
            </button>
            <button
              id="los-tab-vault"
              type="button"
              onClick={() => navigateTab('vault')}
              className={`px-2.5 py-1.5 rounded text-xs font-semibold whitespace-nowrap transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue ${
                activeTab === 'vault'
                  ? 'bg-white text-gblue border border-slate-200 shadow-sm'
                  : 'text-ink-body hover:text-ink-heading'
              }`}
            >
              KYC Vault Inspector
            </button>
            <button
              id="los-tab-ledgers"
              type="button"
              onClick={() => navigateTab('ledgers')}
              className={`px-2.5 py-1.5 rounded text-xs font-semibold whitespace-nowrap transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue ${
                activeTab === 'ledgers'
                  ? 'bg-white text-gblue border border-slate-200 shadow-sm'
                  : 'text-ink-body hover:text-ink-heading'
              }`}
            >
              Multi-Utility Ledgers
            </button>
            <button
              id="los-tab-replay"
              type="button"
              onClick={() => navigateTab('replay')}
              className={`px-2.5 py-1.5 rounded text-xs font-semibold whitespace-nowrap transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue ${
                activeTab === 'replay'
                  ? 'bg-white text-gblue border border-slate-200 shadow-sm'
                  : 'text-ink-body hover:text-ink-heading'
              }`}
            >
              Saga Replay Queue ({replayQueueLoans.length})
            </button>
            <button
              id="los-tab-topology"
              type="button"
              onClick={() => navigateTab('topology')}
              className={`px-2.5 py-1.5 rounded text-xs font-semibold whitespace-nowrap transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue ${
                activeTab === 'topology'
                  ? 'bg-white text-gblue border border-slate-200 shadow-sm'
                  : 'text-ink-body hover:text-ink-heading'
              }`}
            >
              Service Topology &amp; Traces
            </button>
          </nav>

          {/* Right Status Cluster: SSE Stream + IAP Identity Chip */}
          <div className="flex items-center gap-2 shrink-0">
            <div
              className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-surface border border-slate-200 text-xs text-ink-body whitespace-nowrap"
              title="SSE Telemetry Stream (/api/v1/telemetry/stream)"
            >
              <Radio
                size={13}
                strokeWidth={1.75}
                className={telemetry.connected ? 'text-ggreen' : 'text-gamber'}
              />
              <span className="font-medium">SSE</span>
              <span className="text-ink-muted tabular-nums">
                ({iapUser.header_source === 'session-expired' ? 0 : telemetry.events.length})
              </span>
            </div>

            <div
              data-testid="iap-user-chip"
              title={`Identity-Aware Proxy Header (${iapUser.header_source || 'X-Goog-Authenticated-User-Email'})`}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded border text-xs font-medium whitespace-nowrap ${
                iapUser.iap_authenticated
                  ? 'bg-gblue-tint border-gblue-border text-gblue'
                  : 'bg-gamber-tint border-gamber-border text-gamber'
              }`}
            >
              {iapUser.iap_authenticated ? (
                <UserCheck
                  size={13}
                  strokeWidth={1.75}
                  className="text-gblue shrink-0"
                />
              ) : (
                <ShieldAlert
                  size={13}
                  strokeWidth={1.75}
                  className="text-gamber shrink-0"
                />
              )}
              <span className="font-mono text-xs">{iapUser.email}</span>
            </div>
          </div>
        </div>
      </header>

      {/* Top KPI Ribbon */}
      <section
        aria-label="LOS Executive KPI Ribbon"
        className="bg-white border-b border-slate-200"
      >
        <div className="max-w-[1440px] mx-auto px-6 py-3 grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 items-start gap-3">
          <div className="p-2.5 rounded bg-surface border border-slate-200">
            <span className="text-xs font-semibold uppercase tracking-wider text-ink-muted block">
              Total Applications
            </span>
            <div className="mt-1 flex items-baseline justify-between">
              <span className="text-lg font-semibold text-ink-heading tabular-nums">
                {kpis.totalApplications}
              </span>
              <span className="text-xs text-ink-muted tabular-nums">
                {kpis.humanCount} Human · {kpis.syntheticCount} AI
              </span>
            </div>
          </div>

          <div className="p-2.5 rounded bg-surface border border-slate-200">
            <span className="text-xs font-semibold uppercase tracking-wider text-ink-muted block">
              Underwriting Approval Rate
            </span>
            <div className="mt-1 flex items-baseline justify-between">
              <span className="text-lg font-semibold text-ggreen tabular-nums">
                {kpis.approvalRatePct.toFixed(1)}%
              </span>
              <span className="text-xs text-ink-muted tabular-nums">
                CIBIL ≥ 680 · FOIR ≤ 60%
              </span>
            </div>
          </div>

          <div className="p-2.5 rounded bg-surface border border-slate-200">
            <span className="text-xs font-semibold uppercase tracking-wider text-ink-muted block">
              Model Armor Blocks
            </span>
            <div className="mt-1 flex items-baseline justify-between">
              <span className="text-lg font-semibold text-gred tabular-nums">
                {kpis.modelArmorBlockedCount}
              </span>
              <Badge variant="info">Text-Only Scan</Badge>
            </div>
          </div>

          <div className="p-2.5 rounded bg-surface border border-slate-200">
            <span className="text-xs font-semibold uppercase tracking-wider text-ink-muted block">
              Stuck Disbursements
            </span>
            <div className="mt-1 flex items-baseline justify-between">
              <span className="text-lg font-semibold text-gamber tabular-nums">
                {kpis.stuckDisbursementCount}
              </span>
              <span className="text-xs text-ink-muted tabular-nums">
                Pending CBS Replay
              </span>
            </div>
          </div>

          <div className="p-2.5 rounded bg-surface border border-slate-200">
            <span className="text-xs font-semibold uppercase tracking-wider text-ink-muted block">
              Disbursement ₹ At Risk
            </span>
            <div className="mt-1 flex items-baseline justify-between">
              <span className="text-lg font-semibold text-gred tabular-nums">
                {formatInr(kpis.stuckDisbursementRupees)}
              </span>
              <span className="text-xs text-ink-muted">Utility 5 CBS</span>
            </div>
          </div>

          <div className="p-2.5 rounded bg-surface border border-slate-200">
            <span className="text-xs font-semibold uppercase tracking-wider text-ink-muted block">
              Mean Saga Latency
            </span>
            <div className="mt-1 flex items-baseline justify-between">
              <span className="text-lg font-semibold text-ink-heading tabular-nums">
                {formatLatencyMs(kpis.avgLatencyMs)}
              </span>
              <span className="text-xs text-ink-muted font-mono">
                KYC&rarr;LOS&rarr;INS&rarr;CBS
              </span>
            </div>
          </div>
        </div>
      </section>

      {/* Main Content Area */}
      <main className="flex-1 max-w-[1440px] w-full mx-auto px-6 py-5 space-y-5">
        {loansError ? (
          <div
            role="alert"
            data-testid="los-error-banner"
            className="bg-gred-tint border border-gred-border rounded p-3.5 flex items-center justify-between gap-4"
          >
            <div className="flex items-center gap-2.5">
              <AlertTriangle size={16} className="text-gred shrink-0" />
              <span className="text-xs font-medium text-ink-heading">
                {loansError}
              </span>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void loadAllData()}
            >
              Retry
            </Button>
          </div>
        ) : null}

        {iapUser.header_source !== 'session-expired' && activeTab === 'queue' && (
          <QueueView
            loans={loans}
            visibleQueue={visibleQueue}
            loansLoading={loansLoading}
            selectedLoan={selectedLoan}
            filterCriteria={filterCriteria}
            telemetryEvents={telemetry.events}
            onFilterChange={setFilterCriteria}
            onSortColumn={handleSortColumn}
            onSelectLoan={setSelectedLoanId}
            onRefresh={() => void loadAllData()}
            onInspectVault={() => navigateTab('vault')}
            onInspectTraces={() => navigateTab('topology')}
            onQueueKeyDown={handleQueueKeyDown}
          />
        )}

        {iapUser.header_source !== 'session-expired' && activeTab === 'vault' && (
          <VaultView
            loans={loans}
            selectedLoan={selectedLoan}
            panDoc={panDoc}
            aadhaarDoc={aadhaarDoc}
            vaultLoading={vaultLoading}
            onSelectLoan={setSelectedLoanId}
          />
        )}

        {iapUser.header_source !== 'session-expired' && activeTab === 'ledgers' && (
          <LedgersView
            loans={loans}
            activeLedgerSection={activeLedgerSection}
            onSelectSection={setActiveLedgerSection}
          />
        )}

        {iapUser.header_source !== 'session-expired' && activeTab === 'replay' && (
          <ReplayView replayQueueLoans={replayQueueLoans} />
        )}

        {iapUser.header_source !== 'session-expired' && activeTab === 'topology' && (
          <TopologyView
            topology={topology}
            selectedTrace={selectedTrace}
            loans={loans}
            selectedLoanId={selectedLoan?.loan_id ?? ''}
            onSelectLoan={setSelectedLoanId}
          />
        )}
      </main>
    </div>
  );
};

export default App;
