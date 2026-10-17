import { PauseCircle, PlayCircle } from 'lucide-react';
import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import type {
  InvariantDocument,
  InvariantStatus,
  SentinelDefinition,
} from '../api/types';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  SectionHeader,
  SeverityBadge,
  formatEnumLabel,
  formatNumber,
  formatShortTime,
} from './ui';

export interface InvariantsTabProps {
  invariants: InvariantDocument[];
  sentinels: SentinelDefinition[];
  onSelectAgent: (agentId: string) => void;
  onUpdateSentinel: (
    sentinelId: string,
    state: SentinelDefinition['state']
  ) => Promise<void>;
  onCompileSentinel: (invariantId: string) => Promise<void>;
  busyKey: string | null;
}

const STATUS_COLUMNS: Array<{
  key: 'all' | InvariantStatus;
  label: string;
}> = [
  { key: 'all', label: 'All Invariants' },
  { key: 'enforced', label: 'Enforced' },
  { key: 'shadow', label: 'Shadow (24h Window)' },
  { key: 'proposed', label: 'Proposed' },
  { key: 'retired', label: 'Retired' },
];

export const InvariantsTab: React.FC<InvariantsTabProps> = ({
  invariants,
  sentinels,
  onSelectAgent,
  onUpdateSentinel,
  onCompileSentinel,
  busyKey,
}) => {
  const [statusFilter, setStatusFilter] = useState<'all' | InvariantStatus>(
    'all'
  );
  const [selectedId, setSelectedId] = useState<string>(
    invariants[0]?.id ?? ''
  );

  const filtered =
    statusFilter === 'all'
      ? invariants
      : invariants.filter((inv) => inv.status === statusFilter);

  const activeInvariant =
    invariants.find((i) => i.id === selectedId) ?? filtered[0] ?? invariants[0];

  const linkedSentinel = activeInvariant
    ? sentinels.find(
        (s) =>
          s.sentinel_id === activeInvariant.sentinel_id ||
          s.source_ref === activeInvariant.id
      )
    : undefined;

  const enforcedCount = invariants.filter(
    (i) => i.status === 'enforced'
  ).length;
  const shadowCount = invariants.filter((i) => i.status === 'shadow').length;
  const totalBreaches = invariants.reduce(
    (sum, i) => sum + i.shadow_stats.breaches,
    0
  );
  const avgFpRate =
    invariants.length > 0
      ? (invariants.reduce(
          (sum, i) => sum + i.shadow_stats.false_positive_rate,
          0
        ) /
          invariants.length) *
        100
      : 0;

  return (
    <div className="space-y-6">
      {/* KPI Ribbon */}
      <div className="grid grid-cols-1 items-stretch gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Card className="flex h-full flex-col justify-between p-4">
          <span className="text-xs text-slate-500">
            Total Authored Invariants
          </span>
          <p className="mt-2 text-xl font-semibold text-slate-900 num-tabular">
            {invariants.length} Invariants
          </p>
          <p className="mt-1 text-xs text-slate-600">
            Git-versioned INVARIANT.md files indexed in Firestore
          </p>
        </Card>

        <Card className="flex h-full flex-col justify-between p-4">
          <span className="text-xs text-slate-500">
            Enforced / Shadow Sentinels
          </span>
          <p className="mt-2 text-xl font-semibold text-slate-900 num-tabular">
            {enforcedCount} Enforced · {shadowCount} Shadow
          </p>
          <p className="mt-1 text-xs text-slate-600">
            10s cadence · 0 LLM tokens at rest · 24h shadow gate
          </p>
        </Card>

        <Card className="flex h-full flex-col justify-between p-4">
          <span className="text-xs text-slate-500">
            Observed Breaches (Window)
          </span>
          <p className="mt-2 text-xl font-semibold text-slate-900 num-tabular">
            {formatNumber(totalBreaches)} breaches
          </p>
          <p className="mt-1 text-xs text-slate-600">
            Triggers A2A wake with EvidencePack on threshold breach
          </p>
        </Card>

        <Card className="flex h-full flex-col justify-between p-4">
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-500">
              False-Positive Business Alert Rate
            </span>
            <Badge variant="success">SLO &lt; 5.0%</Badge>
          </div>
          <p className="mt-2 text-xl font-semibold text-slate-900 num-tabular">
            {avgFpRate.toFixed(1)}%
          </p>
          <p className="mt-1 text-xs text-slate-600">
            Auto-retires after 30d with 0 breaches + owner confirmation
          </p>
        </Card>
      </div>

      {/* Filter Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-1.5">
          {STATUS_COLUMNS.map((col) => {
            const count =
              col.key === 'all'
                ? invariants.length
                : invariants.filter((i) => i.status === col.key).length;
            const isSelected = statusFilter === col.key;
            return (
              <button
                key={col.key}
                type="button"
                onClick={() => setStatusFilter(col.key)}
                className={`inline-flex items-center gap-1.5 rounded border px-3 py-1.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-blue-600 ${
                  isSelected
                    ? 'border-blue-600 bg-blue-50 text-blue-800'
                    : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
                }`}
              >
                <span>{col.label}</span>
                <span className="rounded bg-slate-100 px-1.5 py-0.2 font-mono text-slate-800 num-tabular">
                  {count}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Master-Detail Board */}
      <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-12">
        {/* Left: Invariants Table */}
        <Card className="p-4 xl:col-span-7">
          <SectionHeader
            title="Business Invariants Registry"
            subtitle="Status lifecycle: Proposed → Shadow (24h) → Enforced → Retired"
          />
          {filtered.length === 0 ? (
            <div className="mt-4">
              <EmptyState
                title="No Invariants in Selected Status"
                description="Switch the filter above to inspect other lifecycle stages."
              />
            </div>
          ) : (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full border-collapse text-left text-xs">
                <thead>
                  <tr className="border-b border-slate-200 bg-slate-50 text-slate-600">
                    <th className="px-3 py-2 font-semibold">Invariant Name</th>
                    <th className="px-3 py-2 font-semibold">Status</th>
                    <th className="px-3 py-2 font-semibold">Sev</th>
                    <th className="px-3 py-2 font-semibold">Provenance</th>
                    <th className="whitespace-nowrap px-3 py-2 text-right font-semibold">
                      Shadow Stats (Runs / Breaches / FP)
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-200">
                  {filtered.map((inv) => {
                    const selected = activeInvariant?.id === inv.id;
                    const fpPct = (
                      inv.shadow_stats.false_positive_rate * 100
                    ).toFixed(1);
                    return (
                      <tr
                        key={inv.id}
                        onClick={() => setSelectedId(inv.id)}
                        className={`cursor-pointer transition-colors ${
                          selected ? 'bg-blue-50/70' : 'hover:bg-slate-50'
                        }`}
                      >
                        <td className="px-3 py-2.5">
                          <div className="font-mono font-semibold text-slate-900">
                            {inv.name}
                          </div>
                          <div className="mt-0.5 font-mono text-slate-500">
                            {inv.scope.service} · v{inv.version}
                          </div>
                        </td>
                        <td className="px-3 py-2.5">
                          <Badge
                            variant={
                              inv.status === 'enforced'
                                ? 'success'
                                : inv.status === 'shadow'
                                ? 'info'
                                : inv.status === 'proposed'
                                ? 'warning'
                                : 'muted'
                            }
                            dot
                          >
                            {formatEnumLabel(inv.status)}
                          </Badge>
                        </td>
                        <td className="whitespace-nowrap px-3 py-2.5">
                          <SeverityBadge severity={inv.severity} />
                        </td>
                        <td className="px-3 py-2.5">
                          <Badge variant="outline">
                            {formatEnumLabel(inv.provenance)}
                          </Badge>
                          <div className="mt-0.5 max-w-[175px] truncate font-mono text-slate-500">
                            {inv.provenance_ref}
                          </div>
                        </td>
                        <td className="whitespace-nowrap px-3 py-2.5 text-right text-slate-900 num-tabular">
                          <div>
                            {formatNumber(inv.shadow_stats.runs)} runs ·{' '}
                            <span
                              className={
                                inv.shadow_stats.breaches > 0
                                  ? 'font-semibold text-amber-800'
                                  : 'text-slate-700'
                              }
                            >
                              {inv.shadow_stats.breaches} breaches
                            </span>
                          </div>
                          <div className="text-slate-500">FP {fpPct}%</div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        {/* Right: Selected Invariant Detail & Markdown Body */}
        <div className="space-y-4 xl:col-span-5">
          {activeInvariant && (
            <Card className="p-5">
              <SectionHeader
                title={activeInvariant.name}
                subtitle={`ID: ${activeInvariant.id} · Dialect: ${activeInvariant.check.dialect.toUpperCase()} · Window: ${activeInvariant.check.window_s}s`}
                right={
                  <SeverityBadge severity={activeInvariant.severity} />
                }
              />

              <div className="mt-3 grid grid-cols-2 gap-2.5 rounded border border-slate-200 bg-slate-50 p-3 text-xs">
                <div>
                  <span className="text-slate-500">Provenance Ref</span>
                  <p className="mt-0.5 break-all font-mono font-semibold text-slate-900">
                    {activeInvariant.provenance_ref}
                  </p>
                </div>
                <div>
                  <span className="text-slate-500">Created By</span>
                  <p className="mt-0.5 font-mono text-slate-900">
                    {activeInvariant.created_by}
                  </p>
                </div>
                <div>
                  <span className="text-slate-500">Git Vault Path</span>
                  <p className="mt-0.5 break-all font-mono text-slate-700">
                    {activeInvariant.git_path}@{activeInvariant.git_sha}
                  </p>
                </div>
                <div>
                  <span className="text-slate-500">Wake Target Agent</span>
                  <p className="mt-0.5">
                    <button
                      type="button"
                      onClick={() =>
                        onSelectAgent(
                          activeInvariant.wake_condition.target_agent_id
                        )
                      }
                      className="rounded font-mono font-semibold text-blue-700 hover:underline focus:outline-none focus:ring-2 focus:ring-blue-600"
                    >
                      {activeInvariant.wake_condition.target_agent_id}
                    </button>
                  </p>
                </div>
              </div>

              {/* Compiled Check Statement */}
              <div className="mt-4">
                <div className="flex items-center justify-between text-xs font-semibold text-slate-700">
                  <span>
                    Compiled {activeInvariant.check.dialect.toUpperCase()} Check
                    Statement
                  </span>
                  <span className="font-mono font-normal text-slate-500 num-tabular">
                    Wake: {activeInvariant.wake_condition.operator}{' '}
                    {activeInvariant.wake_condition.threshold} (
                    {activeInvariant.wake_condition.consecutive}x consecutive)
                  </span>
                </div>
                <pre className="mt-1.5 overflow-x-auto rounded border border-slate-200 bg-slate-900 p-3 font-mono text-xs text-slate-100">
                  {activeInvariant.check.statement}
                </pre>
              </div>

              {/* Linked Sentinel Controls */}
              <div className="mt-4 rounded border border-slate-200 p-3 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <span className="font-semibold text-slate-900">
                      Sentinel: {activeInvariant.sentinel_id}
                    </span>
                    <p className="mt-0.5 text-slate-600 num-tabular">
                      {linkedSentinel
                        ? `State: ${formatEnumLabel(linkedSentinel.state)} · Cadence: ${linkedSentinel.cadence_s}s · Last count: ${linkedSentinel.last_result.count}`
                        : 'Ready to compile into 0-token Sentinel Engine'}
                    </p>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {linkedSentinel ? (
                      linkedSentinel.state === 'paused' ? (
                        <Button
                          variant="primary"
                          size="sm"
                          onClick={() =>
                            onUpdateSentinel(
                              linkedSentinel.sentinel_id,
                              'enforced'
                            )
                          }
                          disabled={
                            busyKey === `sentinel:${linkedSentinel.sentinel_id}`
                          }
                        >
                          <PlayCircle className="h-3.5 w-3.5" />
                          Resume Sentinel
                        </Button>
                      ) : (
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() =>
                            onUpdateSentinel(
                              linkedSentinel.sentinel_id,
                              'paused'
                            )
                          }
                          disabled={
                            busyKey === `sentinel:${linkedSentinel.sentinel_id}`
                          }
                        >
                          <PauseCircle className="h-3.5 w-3.5" />
                          Pause Sentinel
                        </Button>
                      )
                    ) : (
                      <Button
                        variant="primary"
                        size="sm"
                        onClick={() => onCompileSentinel(activeInvariant.id)}
                        disabled={busyKey === `compile:${activeInvariant.id}`}
                      >
                        Compile Sentinel
                      </Button>
                    )}
                  </div>
                </div>
              </div>

              {/* INVARIANT.md Body rendered with react-markdown */}
              <div className="mt-4">
                <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  INVARIANT.md Body (Rendered Markdown)
                </p>
                <div className="mt-1.5 space-y-2 rounded border border-slate-200 bg-slate-50 p-3.5 text-xs leading-relaxed text-slate-800">
                  <ReactMarkdown>{activeInvariant.body_md}</ReactMarkdown>
                </div>
              </div>

              {/* Status Transition History */}
              <div className="mt-4">
                <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Promotion &amp; Audit History
                </p>
                {activeInvariant.history.length === 0 ? (
                  <p className="mt-1.5 text-xs text-slate-500">
                    No status transitions recorded yet.
                  </p>
                ) : (
                  <div className="mt-2 space-y-2">
                    {activeInvariant.history.map((h, i) => (
                      <div
                        key={i}
                        className="rounded border border-slate-200 bg-white p-2.5 text-xs"
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-mono font-semibold text-slate-900">
                            {formatEnumLabel(h.from_status)} →{' '}
                            {formatEnumLabel(h.to_status)}
                          </span>
                          <span className="font-mono text-slate-500 num-tabular">
                            {formatShortTime(h.at)}
                          </span>
                        </div>
                        <p className="mt-1 text-slate-700">{h.note}</p>
                        <p className="mt-0.5 font-mono text-slate-500">
                          Actor: {h.actor}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
};
