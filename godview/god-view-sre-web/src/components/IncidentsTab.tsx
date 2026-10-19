import { AlertTriangle, ArrowLeft, ArrowUpRight } from 'lucide-react';
import React from 'react';
import ReactMarkdown from 'react-markdown';
import type { EpisodicIncident } from '../api/types';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  SectionHeader,
  SeverityBadge,
  TrustLevelBadge,
  formatEnumLabel,
  formatCurrency,
  formatNumber,
  formatShortTime,
} from './ui';

export interface IncidentsTabProps {
  incidents: EpisodicIncident[];
  selectedIncidentId: string | null;
  onSelectIncident: (incidentId: string) => void;
  onSelectAgent: (agentId: string) => void;
}

export const IncidentsTab: React.FC<IncidentsTabProps> = ({
  incidents,
  selectedIncidentId,
  onSelectIncident,
  onSelectAgent,
}) => {
  if (!incidents) {
    return (
      <Card className="p-8 text-center">
        <AlertTriangle className="mx-auto h-8 w-8 text-amber-500" />
        <h3 className="mt-3 text-base font-semibold text-slate-900">
          Incident Telemetry Not Measured Yet
        </h3>
        <p className="mt-1 text-xs text-slate-500 max-w-md mx-auto">
          The Sovereign control plane <code>/api/v1/incidents</code> endpoint returned no data or is currently unreachable.
        </p>
      </Card>
    );
  }

  const activeIncident =
    incidents.find((i) => i.incident_id === selectedIncidentId) ??
    incidents[0] ??
    null;

  if (!activeIncident) {
    return (
      <EmptyState
        title="No Episodic Incidents Recorded"
        description="The Sentinel Engine is evaluating all invariants and golden-signal gates every 10 seconds at 0 tokens."
      />
    );
  }

  const isDetailRoute = Boolean(selectedIncidentId);
  const evp = activeIncident.evidence_pack;
  const isGreenButBroken = evp?.golden_signals?.status === 'green';

  const totalExposure = incidents.reduce((sum, i) => sum + (i.value_impact || 0), 0);
  const avgMtti =
    incidents.length > 0
      ? Math.round(
          incidents.reduce((sum, i) => sum + (i.convergence?.seconds ?? 0), 0) /
            incidents.length
        )
      : 0;
  const avgConsensus =
    incidents.length > 0
      ? Math.round(
          (incidents.reduce((sum, i) => sum + (i.convergence?.consensus ?? 0), 0) /
            incidents.length) *
            100
        )
      : 0;

  return (
    <div className="space-y-6">
      {isDetailRoute ? (
        /* Dedicated #incident/<id> Breadcrumb & Switcher Bar */
        <div
          data-testid="incident-focused-header"
          className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-3"
        >
          <div className="flex items-center gap-2 text-xs">
            <button
              type="button"
              onClick={() => {
                window.location.hash = '#incidents';
              }}
              className="inline-flex items-center gap-1 rounded font-semibold text-blue-700 hover:underline focus:outline-none focus:ring-2 focus:ring-blue-600"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              All Episodic Incidents
            </button>
            <span className="text-slate-500">/</span>
            <span className="font-mono font-semibold text-slate-900">
              {activeIncident.incident_id}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            {incidents.map((inc) => (
              <button
                key={inc.incident_id}
                type="button"
                onClick={() => onSelectIncident(inc.incident_id)}
                className={`rounded border px-2.5 py-1 font-mono text-xs transition-colors focus:outline-none focus:ring-2 focus:ring-blue-600 ${
                  inc.incident_id === activeIncident.incident_id
                    ? 'border-blue-600 bg-blue-50 font-semibold text-blue-800'
                    : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
                }`}
              >
                {inc.incident_id}
              </button>
            ))}
          </div>
        </div>
      ) : (
        /* #incidents Overview Mode: KPI Ribbon + Incident Registry Table */
        <>
          <div
            data-testid="incidents-list-overview"
            className="grid grid-cols-1 items-stretch gap-4 sm:grid-cols-2 xl:grid-cols-4"
          >
            <Card className="flex h-full flex-col justify-between p-4">
              <span className="text-xs text-slate-500">
                Episodic Incidents (Window)
              </span>
              <p className="mt-2 text-2xl font-semibold text-slate-900 num-tabular">
                {incidents.length} Incidents
              </p>
              <p className="mt-1 text-xs text-slate-600">
                Symptom embeddings indexed for precedent recall
              </p>
            </Card>

            <Card className="flex h-full flex-col justify-between p-4">
              <span className="text-xs text-slate-500">
                Quantified Business Exposure Protected
              </span>
              <p className="mt-2 text-2xl font-semibold text-slate-900 num-tabular">
                {formatCurrency(totalExposure)}
              </p>
              <p className="mt-1 text-xs text-slate-600">
                Value at risk across detected invariant &amp; infra breaches
              </p>
            </Card>

            <Card className="flex h-full flex-col justify-between p-4">
              <div className="flex items-center justify-between">
                <span className="text-xs text-slate-500">
                  Mean Time to Investigate (MTTI)
                </span>
                <Badge variant="success">Automated</Badge>
              </div>
              <p className="mt-2 text-2xl font-semibold text-slate-900 num-tabular">
                {avgMtti}s
              </p>
              <p className="mt-1 text-xs text-slate-600">
                Multi-round agent swarm convergence protocol
              </p>
            </Card>

            <Card className="flex h-full flex-col justify-between p-4">
              <div className="flex items-center justify-between">
                <span className="text-xs text-slate-500">
                  Mean Swarm Consensus Score
                </span>
                <Badge variant="info">Consensus</Badge>
              </div>
              <p className="mt-2 text-2xl font-semibold text-slate-900 num-tabular">
                {avgConsensus}%
              </p>
              <p className="mt-1 text-xs text-slate-600">
                Weighted consensus across specialist agents and probes
              </p>
            </Card>
          </div>

          <Card className="p-4">
            <SectionHeader
              title="Episodic Incidents & Swarm Investigations"
              subtitle="Stored in Firestore episodic_incidents. Select any incident row to open its full EvidencePack and A2A Swarm Transcript."
            />
            <div className="mt-3 overflow-x-auto">
              <table className="w-full border-collapse text-left text-xs">
                <thead>
                  <tr className="border-b border-slate-200 bg-slate-50 text-slate-600">
                    <th className="px-3 py-2 font-semibold">Incident ID</th>
                    <th className="px-3 py-2 font-semibold">Severity</th>
                    <th className="px-3 py-2 font-semibold">Symptom Summary</th>
                    <th className="px-3 py-2 font-semibold">Lead Agent</th>
                    <th className="px-3 py-2 text-right font-semibold">
                      Value Impact
                    </th>
                    <th className="px-3 py-2 font-semibold">Convergence</th>
                    <th className="px-3 py-2 font-semibold">Outcome</th>
                    <th className="px-3 py-2 text-right font-semibold">
                      Investigation
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-200 num-tabular">
                  {incidents.map((inc) => {
                    const selected =
                      inc.incident_id === activeIncident.incident_id;
                    return (
                      <tr
                        key={inc.incident_id}
                        onClick={() => onSelectIncident(inc.incident_id)}
                        className={`cursor-pointer transition-colors ${
                          selected ? 'bg-blue-50/70' : 'hover:bg-slate-50'
                        }`}
                      >
                        <td className="whitespace-nowrap px-3 py-2.5 font-mono font-semibold text-blue-700">
                          {inc.incident_id}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2.5">
                          <SeverityBadge severity={inc.severity} />
                        </td>
                        <td className="max-w-md px-3 py-2.5 text-slate-800">
                          {inc.symptom_summary}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2.5 font-mono text-slate-700">
                          {inc.lead_agent_id}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2.5 text-right font-semibold text-slate-900">
                          {formatCurrency(inc.value_impact || 0)}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2.5 font-mono text-slate-700">
                          {((inc.convergence?.consensus ?? 0) * 100).toFixed(0)}% ·{' '}
                          {inc.convergence?.rounds ?? 0}r · {inc.convergence?.seconds ?? 0}s
                        </td>
                        <td className="whitespace-nowrap px-3 py-2.5">
                          <Badge
                            variant={
                              inc.outcome === 'resolved'
                                ? 'success'
                                : inc.outcome === 'mitigated'
                                ? 'info'
                                : 'warning'
                            }
                            dot
                          >
                            {formatEnumLabel(inc.outcome)}
                          </Badge>
                        </td>
                        <td className="whitespace-nowrap px-3 py-2.5 text-right">
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              onSelectIncident(inc.incident_id);
                            }}
                          >
                            Open drill-down
                            <ArrowUpRight className="h-3.5 w-3.5" />
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}

      {/* Active Incident Header & Green-But-Broken Callout */}
      <Card className="p-5">
        <div className="flex flex-col justify-between gap-4 border-b border-slate-200 pb-4 lg:flex-row lg:items-start">
          <div className="space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-lg font-semibold text-slate-900">
                {activeIncident.incident_id}
              </span>
              <SeverityBadge severity={activeIncident.severity} />
              <Badge
                variant={
                  activeIncident.outcome === 'resolved' ? 'success' : 'info'
                }
                dot
              >
                {formatEnumLabel(activeIncident.outcome)}
              </Badge>
              {isGreenButBroken && (
                <Badge variant="success" dot>
                  Infra golden signals green (P95 {evp?.golden_signals?.p95_ms ?? 0}ms ·
                  5xx {evp?.golden_signals?.error_rate_pct ?? 0}%)
                </Badge>
              )}
            </div>
            <p className="text-sm font-semibold text-slate-900">
              {activeIncident.symptom_summary}
            </p>
            <p className="text-xs text-slate-600">
              Root Cause:{' '}
              <span className="font-semibold text-slate-900">
                {activeIncident.root_cause || (activeIncident as any).diagnoses?.[0]?.hypothesis || 'Investigation in progress'}
              </span>
            </p>
          </div>

          <div className="shrink-0 rounded border border-slate-200 bg-slate-50 px-4 py-2.5 text-left num-tabular">
            <span className="text-xs text-slate-500">
              Quantified Business Exposure
            </span>
            <p className="mt-0.5 text-2xl font-semibold text-slate-900">
              {formatCurrency(activeIncident.value_impact || 0)}
            </p>
            <p className="text-xs text-slate-500">
              {evp?.affected?.rows ?? 0} affected records · Opened{' '}
              {formatShortTime(activeIncident.opened_at)}
            </p>
          </div>
        </div>

        {/* Convergence Record Strip */}
        <div className="mt-4 grid grid-cols-2 gap-3 rounded border border-slate-200 bg-slate-50 p-3 text-xs sm:grid-cols-5 num-tabular">
          <div>
            <span className="text-slate-500">Convergence Score</span>
            <p className="mt-0.5 text-sm font-semibold text-slate-900">
              {(activeIncident.convergence?.consensus ?? 0).toFixed(2)} (
              {Math.round((activeIncident.convergence?.consensus ?? 0) * 100)}%)
            </p>
          </div>
          <div>
            <span className="text-slate-500">Swarm Rounds</span>
            <p className="mt-0.5 text-sm font-semibold text-slate-900">
              {activeIncident.convergence?.rounds ?? 0} rounds
            </p>
          </div>
          <div>
            <span className="text-slate-500">Time to Converge (MTTI)</span>
            <p className="mt-0.5 text-sm font-semibold text-slate-900">
              {activeIncident.convergence?.seconds ?? 0}s
            </p>
          </div>
          <div>
            <span className="text-slate-500">Exit Condition</span>
            <p className="mt-0.5 font-semibold text-green-800">
              {formatEnumLabel(activeIncident.convergence?.exit_reason ?? 'investigating')}
            </p>
          </div>
          <div>
            <span className="text-slate-500">Escalated to Argon</span>
            <p className="mt-0.5 font-semibold text-slate-900">
              {activeIncident.convergence?.escalated_to_argon
                ? `Yes (${activeIncident.convergence?.escalation_model ?? 'gemini-4-argon'})`
                : 'No (Flash sufficient)'}
            </p>
          </div>
        </div>
      </Card>

      {/* Main Two-Column Investigation Grid */}
      <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-12">
        {/* Left: Evidence Pack + Timeline + Causal Chain */}
        <div className="space-y-6 xl:col-span-5">
          <Card className="p-4">
            <SectionHeader
              title="Evidence Pack"
              subtitle={`Schema v${evp?.schema_version ?? '5.0'} · Dispatched at ${formatShortTime(evp?.fired_at ?? activeIncident.opened_at)}`}
              right={
                <Badge variant="outline" className="font-mono">
                  {evp?.evidence_pack_id ?? (evp as any)?.pack_id ?? activeIncident.incident_id}
                </Badge>
              }
            />

            <div className="mt-3 space-y-3 text-xs">
              <div className="rounded border border-slate-200 bg-slate-50 p-3">
                <div className="flex items-center justify-between">
                  <span className="font-mono font-semibold text-slate-900">
                    {evp?.sentinel?.id ?? (evp as any)?.sentinel_id ?? 'sentinel'}
                  </span>
                  <Badge variant="outline">{evp?.sentinel?.class ?? (evp as any)?.sentinel_class ?? 'probe'}</Badge>
                </div>
                <pre className="mt-2 overflow-x-auto rounded border border-slate-200 bg-white p-2 font-mono text-xs text-slate-900">
                  {evp?.breach?.statement ?? activeIncident.symptom_summary}
                </pre>
                <div className="mt-2 flex flex-wrap items-center justify-between text-slate-600 num-tabular">
                  <span>
                    Observed: {evp?.breach?.observed?.count ?? (evp as any)?.observed_value ?? 1} in{' '}
                    {evp?.breach?.observed?.window_s ?? 60}s ({evp?.breach?.consecutive ?? (activeIncident as any).consecutive_breaches ?? 1}x
                    consecutive)
                  </span>
                  <span>
                    Baseline ({evp?.baseline?.model ?? 'ewma'}): {evp?.baseline?.expected ?? 0} ±{' '}
                    {evp?.baseline?.stddev ?? 0}
                  </span>
                </div>
              </div>

              {/* Correlated Changes */}
              <div>
                <p className="font-semibold text-slate-800">
                  Correlated Change Events Preceding Breach
                </p>
                {(evp?.correlated_changes ?? []).length === 0 ? (
                  <p className="mt-1 text-slate-500">
                    No correlated code or config deploys in window.
                  </p>
                ) : (
                  <div className="mt-1.5 space-y-1.5">
                    {(evp?.correlated_changes ?? []).map((chg, cIdx) => (
                      <div
                        key={chg.change_event_id || cIdx}
                        className="rounded border border-amber-200 bg-amber-50 p-2.5"
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-mono font-semibold text-amber-900">
                            {chg.change_event_id} ({formatEnumLabel(chg.kind)})
                          </span>
                          <span className="font-mono text-amber-800 num-tabular">
                            {formatShortTime(chg.occurred_at)}
                          </span>
                        </div>
                        <p className="mt-1 text-amber-950">
                          {chg.business_summary}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* KNN Vector Recall */}
              <div className="grid grid-cols-2 gap-2.5">
                <div className="rounded border border-slate-200 p-2.5">
                  <span className="text-slate-500">Episodic KNN Recall</span>
                  {(evp?.recall?.episodic ?? []).length === 0 ? (
                    <p className="mt-1 text-slate-500">Novel symptom</p>
                  ) : (
                    (evp?.recall?.episodic ?? []).map((ep) => (
                      <p
                        key={ep.incident_id}
                        className="mt-1 font-mono font-semibold text-slate-900 num-tabular"
                      >
                        {ep.incident_id} (sim {((ep.similarity ?? 0) * 100).toFixed(0)}
                        %)
                      </p>
                    ))
                  )}
                </div>
                <div className="rounded border border-slate-200 p-2.5">
                  <span className="text-slate-500">Procedural Skill Recall</span>
                  {(evp?.recall?.skills ?? []).map((sk) => (
                    <p
                      key={sk.skill_slug}
                      className="mt-1 font-mono font-semibold text-blue-700 num-tabular"
                    >
                      {sk.skill_slug} ({((sk.similarity ?? 0) * 100).toFixed(0)}%)
                    </p>
                  ))}
                </div>
              </div>

              {/* Trust Snapshot */}
              <div className="rounded border border-slate-200 bg-slate-50 p-2.5">
                <span className="font-semibold text-slate-700">
                  Wake-Time Trust Snapshot &amp; Token Budget
                </span>
                <div className="mt-1.5 flex flex-wrap items-center gap-2">
                  {Object.entries(evp?.trust_snapshot ?? {}).map(([cap, lvl]) => (
                    <span
                      key={cap}
                      className="inline-flex items-center gap-1 rounded border border-slate-200 bg-white px-2 py-0.5 font-mono"
                    >
                      {cap}: <strong>L{lvl}</strong>
                    </span>
                  ))}
                  <span className="text-slate-600 num-tabular">
                    Remaining: {formatNumber(evp?.budget?.agent_tokens_remaining ?? 50000)}{' '}
                    tok
                  </span>
                </div>
              </div>
            </div>
          </Card>

          {/* Incident Lifecycle Timeline */}
          <Card className="p-4">
            <SectionHeader
              title="Incident Lifecycle Timeline & Causal Chain"
              subtitle="Detection → Wake → Swarm Convergence → Trust-Gated Remediation"
            />
            <div className="mt-3 space-y-3">
              {(activeIncident.timeline ?? []).map((step, idx) => (
                <div
                  key={idx}
                  className="flex items-start gap-3 border-l-2 border-blue-600 pl-3 text-xs"
                >
                  <div className="flex-1">
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-slate-900">
                        {step.label}
                      </span>
                      <span className="font-mono text-slate-500 num-tabular">
                        {formatShortTime(step.at)}
                      </span>
                    </div>
                    <p className="mt-0.5 text-slate-600">{step.detail}</p>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </div>

        {/* Right: Swarm Transcript (Rounds, Critiques, Handoffs) & Executed Audit Actions */}
        <div className="space-y-6 xl:col-span-7">
          <Card className="p-4">
            <SectionHeader
              title="A2A Swarm Transcript (Rounds, Interrogations, Critiques & Handoffs)"
              subtitle={`Lead: ${activeIncident.lead_agent_id} · Participants: ${(activeIncident.participants ?? []).length} court agents`}
            />

            <div className="mt-3 space-y-3">
              {(activeIncident.transcript ?? []).map((msg) => (
                <div
                  key={msg.seq}
                  className="rounded border border-slate-200 bg-white p-3.5 text-xs"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="default" className="num-tabular">
                        Round {msg.round}
                      </Badge>
                      <Badge
                        variant={
                          msg.type === 'critique'
                            ? 'warning'
                            : msg.type === 'handoff'
                            ? 'info'
                            : msg.type === 'report'
                            ? 'success'
                            : 'secondary'
                        }
                      >
                        A2A {formatEnumLabel(msg.type)}
                      </Badge>
                      <button
                        type="button"
                        onClick={() => onSelectAgent(msg.from_agent_id)}
                        className="rounded font-mono font-semibold text-blue-700 hover:underline focus:outline-none focus:ring-2 focus:ring-blue-600"
                      >
                        {msg.from_agent_id}
                      </button>
                      <span className="text-slate-500">→</span>
                      <span className="font-mono font-semibold text-slate-800">
                        {msg.to_agent_id}
                      </span>
                    </div>
                    <span className="font-mono text-slate-500 num-tabular">
                      #{msg.seq} · {formatShortTime(msg.sent_at)}
                    </span>
                  </div>

                  <p className="mt-2 font-semibold text-slate-900">
                    {msg.summary}
                  </p>
                  <pre className="mt-2 overflow-x-auto rounded border border-slate-200 bg-slate-50 p-2.5 font-mono text-xs text-slate-700">
                    {JSON.stringify(msg.payload, null, 2)}
                  </pre>
                </div>
              ))}
            </div>
          </Card>

          {/* Executed Actions with Audit IDs */}
          <Card className="p-4">
            <SectionHeader
              title="Trust-Gated Remediation Actions & Audit Trail"
              subtitle="Every mutating call executes through POST /v1/capabilities/{id}:invoke as godview-act-sa with a reversal recipe"
            />
            <div className="mt-3 space-y-3">
              {(activeIncident.actions ?? []).map((act) => (
                <div
                  key={act.audit_id}
                  className="rounded border border-slate-200 bg-slate-50 p-3.5 text-xs"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono font-semibold text-slate-900">
                        {act.audit_id}
                      </span>
                      <Badge variant="outline" className="font-mono">
                        {act.capability}
                      </Badge>
                      <TrustLevelBadge level={act.trust_level} />
                      <Badge
                        variant={
                          act.result === 'success' ? 'success' : 'warning'
                        }
                        dot
                      >
                        {formatEnumLabel(act.result)}
                      </Badge>
                    </div>
                    <span className="font-mono text-slate-500 num-tabular">
                      {formatShortTime(act.executed_at)} · SA: {act.sa_used}
                    </span>
                  </div>

                  <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <div>
                      <span className="text-slate-500">Target Resource:</span>{' '}
                      <span className="font-mono font-semibold text-slate-900">
                        {act.target_resource}
                      </span>
                    </div>
                    <div>
                      <span className="text-slate-500">Reversal Recipe:</span>{' '}
                      <span className="font-mono text-slate-800">
                        {act.reversal_recipe?.capability ?? 'manual'} (
                        {act.reversal_recipe?.target ??
                          act.reversal_recipe?.args ??
                          'auto'}
                        )
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </Card>

          {/* Synthesized Procedural Skills (SKILL.md) */}
          {(activeIncident.skill_details ?? []).length > 0 && (
            <Card className="p-4">
              <SectionHeader
                title="Procedural Skill Synthesized / Executed (SKILL.md)"
                subtitle="Mirrored to gs://...-agent-skills and proposed to godview/agent-skills/ via git.propose_files"
              />
              {(activeIncident.skill_details ?? []).map((sk) => (
                <div key={sk.skill_slug} className="mt-3 space-y-2 text-xs">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-mono font-semibold text-slate-900">
                      {sk.skill_slug} (v{sk.version})
                    </span>
                    <div className="flex items-center gap-2 num-tabular">
                      <Badge variant="info">
                        Conf. {Math.round((sk.confidence_score ?? 0) * 100)}%
                      </Badge>
                      <Badge variant="secondary">
                        {sk.executions_count} runs · avg MTTR{' '}
                        {sk.avg_mttr_seconds}s
                      </Badge>
                    </div>
                  </div>
                  <div className="space-y-1.5 rounded border border-slate-200 bg-slate-50 p-3 text-slate-800">
                    <ReactMarkdown>{sk.body_md}</ReactMarkdown>
                  </div>
                </div>
              ))}
            </Card>
          )}
        </div>
      </div>
    </div>
  );
};
