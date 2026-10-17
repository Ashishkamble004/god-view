import React from 'react';
import { Network } from 'lucide-react';
import type {
  LoanApplication,
  ObservabilityTopology,
  TraceRecord,
} from '../api/types';
import { Badge, mapStatusToBadgeVariant } from '../components/Badge';
import { formatEnumLabel, formatLatencyMs } from '../utils/queueUtils';

export interface TopologyViewProps {
  topology: ObservabilityTopology | null;
  selectedTrace: TraceRecord | null;
  loans: LoanApplication[];
  selectedLoanId: string;
  onSelectLoan: (loanId: string) => void;
}

export const TopologyView: React.FC<TopologyViewProps> = ({
  topology,
  selectedTrace,
  loans,
  selectedLoanId,
  onSelectLoan,
}) => {
  return (
    <div className="space-y-5">
      {/* Topology Nodes Grid */}
      <div className="bg-white border border-slate-200 rounded p-4 shadow-sm space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 pb-3">
          <div className="flex items-center gap-2">
            <Network size={16} className="text-gblue shrink-0" />
            <div>
              <h2 className="text-sm font-semibold text-ink-heading">
                Five-Utility Hybrid Service Topology (/api/v1/observability/topology)
              </h2>
              <p className="text-xs text-ink-muted">
                Intended Saga Sequence:{' '}
                <code className="font-mono font-semibold text-ink-heading">
                  {topology?.intended_sequence ??
                    'KYC < LOS < [FLEET] < INS < CBS'}
                </code>
              </p>
            </div>
          </div>
          <Badge variant="info">W3C traceparent + baggage</Badge>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 items-start gap-3">
          {(topology?.nodes ?? []).map((node) => (
            <div
              key={node.id}
              className="p-3 rounded bg-surface border border-slate-200 space-y-2"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <span className="text-xs font-semibold text-ink-muted block truncate">
                    {node.utility_label}
                  </span>
                  <h3 className="text-xs font-semibold font-mono text-ink-heading whitespace-nowrap">
                    {node.service}
                  </h3>
                </div>
                <Badge variant={mapStatusToBadgeVariant(node.status)}>
                  {formatEnumLabel(node.status)}
                </Badge>
              </div>
              <div
                title={`${node.compute_tier} · ${node.endpoint}`}
                className="text-xs font-mono text-ink-muted truncate"
              >
                {node.compute_tier} · {node.endpoint}
              </div>
              <div className="grid grid-cols-3 items-start gap-2 pt-1 border-t border-slate-200 text-xs tabular-nums">
                <div>
                  <span className="text-xs text-ink-muted block">
                    p95 Latency
                  </span>
                  <span className="font-semibold text-ink-heading">
                    {formatLatencyMs(node.p95_ms)}
                  </span>
                </div>
                <div>
                  <span className="text-xs text-ink-muted block">
                    Throughput
                  </span>
                  <span className="font-semibold text-ink-heading">
                    {node.rps} rps
                  </span>
                </div>
                <div>
                  <span className="text-xs text-ink-muted block">
                    Error Rate
                  </span>
                  <span
                    className={`font-semibold ${
                      node.error_rate_pct > 1 ? 'text-gred' : 'text-ggreen'
                    }`}
                  >
                    {node.error_rate_pct}%
                  </span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* W3C Trace Span Waterfall Inspector */}
      <div className="bg-white border border-slate-200 rounded p-4 shadow-sm space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-3">
          <div>
            <h3 className="text-sm font-semibold text-ink-heading">
              W3C Distributed Trace Span Waterfall
              {selectedTrace
                ? ` (/api/v1/observability/traces/${selectedTrace.trace_id})`
                : ''}
            </h3>
            {selectedTrace && (
              <p className="text-xs text-ink-muted font-mono">
                journey_id={selectedTrace.journey_id} · loan_id=
                {selectedTrace.loan_id} · total_duration=
                {formatLatencyMs(selectedTrace.total_duration_ms)}
              </p>
            )}
          </div>

          <select
            aria-label="Select trace by loan application"
            value={selectedLoanId}
            onChange={(e) => onSelectLoan(e.target.value)}
            className="px-3 py-1.5 text-xs bg-white border border-slate-300 rounded font-mono text-ink-heading focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue"
          >
            {loans.map((l) => (
              <option key={l.loan_id} value={l.loan_id}>
                Trace {l.trace_id.slice(0, 12)}… ({l.loan_id})
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-2">
          {(selectedTrace?.spans ?? []).map((sp) => {
            const total = Math.max(selectedTrace?.total_duration_ms ?? 1, 1);
            const leftPct = Math.min(
              85,
              Math.round((sp.start_offset_ms / total) * 100)
            );
            const widthPct = Math.max(
              6,
              Math.min(
                100 - leftPct,
                Math.round((sp.duration_ms / total) * 100)
              )
            );
            return (
              <div
                key={sp.span_id}
                className="p-2.5 rounded bg-surface border border-slate-200 text-xs space-y-1.5"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="font-mono font-semibold text-ink-heading whitespace-nowrap">
                      {sp.service}
                    </span>
                    <span className="font-mono text-ink-muted truncate">
                      {sp.operation}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 tabular-nums font-mono whitespace-nowrap shrink-0">
                    <Badge
                      variant={sp.http_status >= 400 ? 'danger' : 'success'}
                    >
                      HTTP {sp.http_status}
                    </Badge>
                    <span className="font-semibold text-ink-heading">
                      {formatLatencyMs(sp.duration_ms)}
                    </span>
                  </div>
                </div>
                <div className="w-full h-2 bg-slate-200 dark:bg-slate-800 rounded overflow-hidden relative">
                  <div
                    style={{
                      marginLeft: `${leftPct}%`,
                      width: `${widthPct}%`,
                    }}
                    className={`h-full rounded ${
                      sp.http_status >= 400 ? 'bg-gred' : 'bg-gblue'
                    }`}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};
