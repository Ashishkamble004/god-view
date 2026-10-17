import React from 'react';
import {
  Activity,
  ArrowUpDown,
  Eye,
  Radio,
  RefreshCw,
  Search,
} from 'lucide-react';
import type {
  LoanApplication,
  LoanStatus,
  TelemetryStreamEvent,
  UtilityType,
} from '../api/types';
import { Badge, mapStatusToBadgeVariant } from '../components/Badge';
import { Button } from '../components/Button';
import {
  formatEnumLabel,
  formatInr,
  formatLatencyMs,
  type QueueFilterCriteria,
  type SortField,
} from '../utils/queueUtils';

export interface QueueViewProps {
  loans: LoanApplication[];
  visibleQueue: LoanApplication[];
  loansLoading: boolean;
  selectedLoan: LoanApplication | null;
  filterCriteria: QueueFilterCriteria;
  telemetryEvents: TelemetryStreamEvent[];
  onFilterChange: React.Dispatch<React.SetStateAction<QueueFilterCriteria>>;
  onSortColumn: (field: SortField) => void;
  onSelectLoan: (loanId: string) => void;
  onRefresh: () => void;
  onInspectVault: () => void;
  onInspectTraces: () => void;
  onQueueKeyDown: (e: React.KeyboardEvent<HTMLDivElement>) => void;
}

export const QueueView: React.FC<QueueViewProps> = ({
  loans,
  visibleQueue,
  loansLoading,
  selectedLoan,
  filterCriteria,
  telemetryEvents,
  onFilterChange,
  onSortColumn,
  onSelectLoan,
  onRefresh,
  onInspectVault,
  onInspectTraces,
  onQueueKeyDown,
}) => {
  return (
    <div className="grid grid-cols-1 xl:grid-cols-12 items-start gap-4">
      {/* Left 9 cols: Dense Filterable & Sortable Queue Table */}
      <div
        tabIndex={0}
        onKeyDown={onQueueKeyDown}
        aria-label="Loan Origination Queue Table (Use Up/Down arrows to navigate rows, Enter to inspect KYC Vault)"
        className="xl:col-span-9 bg-white border border-slate-200 rounded shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue"
      >
        {/* Filter & Search Toolbar */}
        <div className="p-3.5 border-b border-slate-200 flex flex-wrap items-center justify-between gap-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <Search
                size={14}
                strokeWidth={1.75}
                className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-muted"
              />
              <input
                id="los-queue-search"
                type="text"
                value={filterCriteria.search}
                onChange={(e) =>
                  onFilterChange((prev) => ({
                    ...prev,
                    search: e.target.value,
                  }))
                }
                placeholder="Search loan ID, applicant, PAN, UTR..."
                aria-label="Search loan applications"
                className="pl-8 pr-2.5 py-1 text-xs bg-white border border-slate-300 rounded text-ink-heading placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue"
              />
            </div>

            <select
              id="los-filter-status"
              aria-label="Filter by loan status"
              value={filterCriteria.status}
              onChange={(e) =>
                onFilterChange((prev) => ({
                  ...prev,
                  status: e.target.value as 'ALL' | LoanStatus,
                }))
              }
              className="px-2.5 py-1 text-xs bg-white border border-slate-300 rounded text-ink-heading focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue"
            >
              <option value="ALL">All Statuses</option>
              <option value="DISBURSED">Disbursed</option>
              <option value="DISBURSEMENT_PENDING">Disbursement Pending</option>
              <option value="APPROVED">Approved</option>
              <option value="INSURED">Insured</option>
              <option value="REJECTED">Rejected</option>
              <option value="MODEL_ARMOR_BLOCKED">Model Armor Blocked</option>
              <option value="KYC_PENDING">KYC Pending</option>
            </select>

            <select
              id="los-filter-utility"
              aria-label="Filter by utility type"
              value={filterCriteria.utilityType}
              onChange={(e) =>
                onFilterChange((prev) => ({
                  ...prev,
                  utilityType: e.target.value as 'ALL' | UtilityType,
                }))
              }
              className="px-2.5 py-1 text-xs bg-white border border-slate-300 rounded text-ink-heading focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue"
            >
              <option value="ALL">All Utilities</option>
              <option value="RETAIL_AUTO_LOAN">Retail Auto Loan</option>
              <option value="COMMERCIAL_FLEET_TOPUP">
                Commercial Fleet Top-Up
              </option>
            </select>

            <select
              id="los-filter-synthetic"
              aria-label="Filter by customer origin"
              value={filterCriteria.syntheticMode}
              onChange={(e) =>
                onFilterChange((prev) => ({
                  ...prev,
                  syntheticMode: e.target.value as
                    | 'ALL'
                    | 'HUMAN'
                    | 'SYNTHETIC',
                }))
              }
              className="px-2.5 py-1 text-xs bg-white border border-slate-300 rounded text-ink-heading focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue"
            >
              <option value="ALL">All Origins (Human + AI)</option>
              <option value="HUMAN">Human Applicants Only</option>
              <option value="SYNTHETIC">AI Simulator Only</option>
            </select>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-xs text-ink-muted tabular-nums">
              Showing{' '}
              <strong className="text-ink-heading">
                {visibleQueue.length}
              </strong>{' '}
              of {loans.length}
            </span>
            <Button variant="secondary" size="sm" onClick={onRefresh}>
              <RefreshCw size={12} />
              Refresh
            </Button>
          </div>
        </div>

        {/* Dense Operations Table */}
        <div className="overflow-x-auto max-h-[520px] overflow-y-auto">
          <table className="w-full text-left border-collapse">
            <thead className="sticky top-0 z-10 bg-surface border-b border-slate-200 text-xs font-semibold uppercase tracking-wider text-ink-muted">
              <tr>
                <th className="py-2.5 px-2.5 whitespace-nowrap">
                  Loan / Trace
                </th>
                <th className="py-2.5 px-2.5 whitespace-nowrap">
                  Applicant &amp; Origin
                </th>
                <th className="py-2.5 px-2.5 whitespace-nowrap">
                  Vehicle &amp; Utility
                </th>
                <th className="py-2.5 px-2.5 text-right whitespace-nowrap">
                  <button
                    type="button"
                    onClick={() => onSortColumn('loan_amount_inr')}
                    className="inline-flex items-center gap-1 hover:text-ink-heading focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue rounded"
                  >
                    Principal ₹
                    <ArrowUpDown size={11} />
                  </button>
                </th>
                <th className="py-2.5 px-2.5 text-right whitespace-nowrap">
                  <button
                    type="button"
                    onClick={() => onSortColumn('cibil_score')}
                    className="inline-flex items-center gap-1 hover:text-ink-heading focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue rounded"
                  >
                    CIBIL
                    <ArrowUpDown size={11} />
                  </button>
                </th>
                <th className="py-2.5 px-2.5 text-right whitespace-nowrap">
                  <button
                    type="button"
                    onClick={() => onSortColumn('foir_pct')}
                    className="inline-flex items-center gap-1 hover:text-ink-heading focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue rounded"
                  >
                    FOIR
                    <ArrowUpDown size={11} />
                  </button>
                </th>
                <th className="py-2.5 px-2.5 whitespace-nowrap">Status</th>
                <th className="py-2.5 px-2.5 text-right whitespace-nowrap">
                  <button
                    type="button"
                    onClick={() => onSortColumn('total_latency_ms')}
                    className="inline-flex items-center gap-1 hover:text-ink-heading focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue rounded"
                  >
                    Latency
                    <ArrowUpDown size={11} />
                  </button>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 text-xs">
              {loansLoading && visibleQueue.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-8 text-center text-ink-muted">
                    Loading LOS application queue...
                  </td>
                </tr>
              ) : visibleQueue.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-8 text-center text-ink-muted">
                    No loan applications match the active filter criteria.
                  </td>
                </tr>
              ) : (
                visibleQueue.map((loan) => {
                  const isSelected = loan.loan_id === selectedLoan?.loan_id;
                  const cibil = loan.underwriting_audit?.cibil_score;
                  const foir = loan.underwriting_audit?.foir_pct;
                  const vehicleDisplayName = loan.vehicle
                    ? `${loan.vehicle.make} ${loan.vehicle.model}`
                    : loan.vehicle_id;
                  return (
                    <tr
                      key={loan.loan_id}
                      onClick={() => onSelectLoan(loan.loan_id)}
                      className={`h-10 cursor-pointer transition-colors ${
                        isSelected ? 'bg-gblue-tint/70' : 'hover:bg-surface'
                      }`}
                    >
                      <td className="py-2 px-2.5 whitespace-nowrap">
                        <div className="font-mono font-semibold text-ink-heading">
                          {loan.loan_id}
                        </div>
                        <div className="font-mono text-xs text-ink-muted">
                          trc:{loan.trace_id.slice(0, 8)}…
                        </div>
                      </td>
                      <td className="py-2 px-2.5 whitespace-nowrap">
                        <div className="flex items-center gap-1.5">
                          <span className="font-semibold text-ink-heading whitespace-nowrap">
                            {loan.customer?.full_name ?? loan.customer_id}
                          </span>
                          {loan.customer?.is_ai_generated ? (
                            <Badge variant="info">Synthetic</Badge>
                          ) : (
                            <Badge variant="secondary">Human</Badge>
                          )}
                        </div>
                        <div className="text-xs text-ink-muted font-mono whitespace-nowrap">
                          {loan.customer?.pan_number ?? '—'} ·{' '}
                          {loan.customer?.city ?? 'India'}
                        </div>
                      </td>
                      <td className="py-2 px-2.5">
                        <div
                          title={vehicleDisplayName}
                          className="font-medium text-ink-heading truncate max-w-[180px]"
                        >
                          {vehicleDisplayName}
                        </div>
                        <div className="flex items-center gap-1.5 mt-0.5 whitespace-nowrap">
                          <Badge
                            variant={mapStatusToBadgeVariant(loan.utility_type)}
                          >
                            {loan.utility_type === 'COMMERCIAL_FLEET_TOPUP'
                              ? 'Fleet Top-Up'
                              : 'Retail Auto'}
                          </Badge>
                          <span className="text-xs font-mono text-ink-muted">
                            {formatEnumLabel(
                              loan.kyc_document?.kyc_mode ?? 'TEXT_ONLY'
                            )}
                          </span>
                        </div>
                      </td>
                      <td className="py-2 px-2.5 text-right tabular-nums whitespace-nowrap">
                        <div className="font-semibold text-ink-heading">
                          {formatInr(loan.loan_amount_inr)}
                        </div>
                        <div className="text-xs text-ink-muted">
                          EMI {formatInr(loan.emi_inr)}
                        </div>
                      </td>
                      <td className="py-2 px-2.5 text-right tabular-nums whitespace-nowrap">
                        {cibil !== undefined ? (
                          <span
                            className={`font-semibold ${
                              cibil >= 680 ? 'text-ggreen' : 'text-gred'
                            }`}
                          >
                            {cibil}
                          </span>
                        ) : (
                          <span className="text-ink-muted">—</span>
                        )}
                      </td>
                      <td className="py-2 px-2.5 text-right tabular-nums whitespace-nowrap">
                        {foir !== undefined ? (
                          <span
                            className={`font-semibold ${
                              foir <= 60 ? 'text-ink-heading' : 'text-gred'
                            }`}
                          >
                            {foir.toFixed(1)}%
                          </span>
                        ) : (
                          <span className="text-ink-muted">—</span>
                        )}
                      </td>
                      <td className="py-2 px-2.5 whitespace-nowrap">
                        <Badge variant={mapStatusToBadgeVariant(loan.status)}>
                          {formatEnumLabel(loan.status)}
                        </Badge>
                      </td>
                      <td className="py-2 px-2.5 text-right font-mono tabular-nums whitespace-nowrap">
                        <span
                          className={
                            loan.total_latency_ms > 2000
                              ? 'text-gred font-semibold'
                              : 'text-ink-body'
                          }
                        >
                          {formatLatencyMs(loan.total_latency_ms)}
                        </span>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Right 3 cols: Selected Application Dossier & Live SSE Feed */}
      <div className="xl:col-span-3 space-y-4">
        {selectedLoan ? (
          <div className="bg-white border border-slate-200 rounded p-4 shadow-sm space-y-3">
            <div className="flex items-start justify-between gap-2 border-b border-slate-200 pb-3">
              <div>
                <span className="text-xs font-semibold uppercase tracking-wider text-ink-muted">
                  Selected Saga Dossier
                </span>
                <h2 className="text-sm font-semibold text-ink-heading font-mono mt-0.5 whitespace-nowrap">
                  {selectedLoan.loan_id}
                </h2>
                <p className="text-xs text-ink-muted">
                  {selectedLoan.customer?.full_name} ·{' '}
                  {selectedLoan.customer?.employer_name}
                </p>
              </div>
              <Badge variant={mapStatusToBadgeVariant(selectedLoan.status)}>
                {formatEnumLabel(selectedLoan.status)}
              </Badge>
            </div>

            <div className="grid grid-cols-2 items-start gap-2.5 text-xs">
              <div className="p-2 rounded bg-surface border border-slate-200">
                <span className="text-xs text-ink-muted block">
                  Principal / EMI
                </span>
                <span className="font-semibold text-ink-heading tabular-nums">
                  {formatInr(selectedLoan.loan_amount_inr)}
                </span>
                <span className="text-xs text-ink-muted block tabular-nums">
                  {formatInr(selectedLoan.emi_inr)}/mo ·{' '}
                  {selectedLoan.tenure_months}m
                </span>
              </div>
              <div className="p-2 rounded bg-surface border border-slate-200">
                <span className="text-xs text-ink-muted block">
                  Model Armor Status
                </span>
                <div className="mt-0.5">
                  <Badge
                    variant={mapStatusToBadgeVariant(
                      selectedLoan.kyc_document?.model_armor_text_status ??
                        'CLEAN'
                    )}
                  >
                    {formatEnumLabel(
                      selectedLoan.kyc_document?.model_armor_text_status ??
                        'CLEAN'
                    )}
                  </Badge>
                </div>
                <span className="text-xs text-ink-muted block mt-1">
                  Images Scanned: false
                </span>
              </div>
            </div>

            <div className="space-y-1.5 text-xs">
              <div className="flex justify-between gap-2">
                <span className="text-ink-muted">Journey ID:</span>
                <span className="font-mono text-ink-heading whitespace-nowrap">
                  {selectedLoan.journey_id}
                </span>
              </div>
              <div className="flex justify-between gap-2">
                <span className="text-ink-muted">W3C Trace ID:</span>
                <span className="font-mono text-ink-heading whitespace-nowrap">
                  {selectedLoan.trace_id.slice(0, 16)}…
                </span>
              </div>
              <div className="flex justify-between gap-2">
                <span className="text-ink-muted">GKE LOS Pod:</span>
                <span className="font-mono text-ink-heading truncate">
                  {selectedLoan.underwriting_audit?.gke_pod_name ?? '—'}
                </span>
              </div>
              <div className="flex justify-between gap-2">
                <span className="text-ink-muted">CBS UTR Ref:</span>
                <span className="font-mono text-ink-heading whitespace-nowrap">
                  {selectedLoan.disbursement?.cbs_utr_number ??
                    'Pending Settlement'}
                </span>
              </div>
            </div>

            <div className="pt-2 border-t border-slate-200 flex items-center gap-2">
              <Button
                variant="primary"
                size="sm"
                onClick={onInspectVault}
                className="flex-1"
              >
                <Eye size={13} />
                KYC Vault
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={onInspectTraces}
                className="flex-1"
              >
                <Activity size={13} />
                Trace Spans
              </Button>
            </div>
          </div>
        ) : (
          <div className="bg-white border border-slate-200 rounded p-4 shadow-sm text-xs text-ink-muted">
            Select a loan application from the queue to inspect its dossier.
          </div>
        )}

        {/* Live SSE Telemetry Feed Card */}
        <div className="bg-white border border-slate-200 rounded p-4 shadow-sm space-y-3">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-1.5 whitespace-nowrap">
              <Radio size={14} className="text-ggreen shrink-0" />
              <h3 className="text-xs font-semibold uppercase tracking-wider text-ink-heading whitespace-nowrap">
                Live SSE Event Stream
              </h3>
            </div>
            <span className="text-xs font-mono text-ink-muted whitespace-nowrap">
              /v1/telemetry/stream
            </span>
          </div>

          <div className="space-y-2 max-h-60 overflow-y-auto pr-1">
            {telemetryEvents.length === 0 ? (
              <div className="p-3 rounded bg-surface border border-slate-200 text-xs text-ink-muted">
                Listening for live telemetry events...
              </div>
            ) : (
              telemetryEvents.map((ev) => (
                <div
                  key={ev.event_id}
                  className="p-2 rounded bg-surface border border-slate-200 text-xs flex items-start justify-between gap-2"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="font-mono font-semibold text-ink-heading whitespace-nowrap">
                        {ev.event_type}
                      </span>
                      {ev.status_to ? (
                        <Badge variant={mapStatusToBadgeVariant(ev.status_to)}>
                          {formatEnumLabel(ev.status_to)}
                        </Badge>
                      ) : null}
                    </div>
                    <p className="text-xs text-ink-muted font-mono mt-0.5 whitespace-nowrap truncate">
                      {ev.loan_id} ·{' '}
                      <span className="whitespace-nowrap">{ev.service}</span>
                    </p>
                  </div>
                  <span className="text-xs font-mono text-ink-muted tabular-nums whitespace-nowrap shrink-0">
                    {formatLatencyMs(ev.duration_ms)}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
