import { ArrowLeft, Shield } from 'lucide-react';
import React from 'react';
import type { CourtAgent } from '../api/types';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  SectionHeader,
  TrustLevelBadge,
  formatEnumLabel,
  formatNumber,
  formatShortTime,
  formatUsd,
} from './ui';

export interface AgentDetailTabProps {
  agents: CourtAgent[];
  selectedAgentId: string | null;
  onSelectAgent: (agentId: string) => void;
  onBackToOrganism: () => void;
  onToggleWhitelist: (recordId: string, whitelisted: boolean) => Promise<void>;
  onControlAgent: (
    agentId: string,
    action: 'wake' | 'retire' | 'warm' | 'cool'
  ) => Promise<void>;
  busyKey: string | null;
}

export const AgentDetailTab: React.FC<AgentDetailTabProps> = ({
  agents,
  selectedAgentId,
  onSelectAgent,
  onBackToOrganism,
  onToggleWhitelist,
  onControlAgent,
  busyKey,
}) => {
  const activeAgent =
    agents.find((a) => a.id === selectedAgentId) ?? agents[0] ?? null;

  if (!activeAgent) {
    return (
      <EmptyState
        title="No Court Agent Selected"
        description="Run the self-awareness protocol to discover services and spawn court agents."
      />
    );
  }

  const lm = activeAgent.living_model;
  const firstThresholds = activeAgent.trust_records[0]?.thresholds;

  return (
    <div className="space-y-6">
      {/* Breadcrumb + Agent Switcher Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-3">
        <div className="flex items-center gap-2 text-xs">
          <button
            type="button"
            onClick={onBackToOrganism}
            className="inline-flex items-center gap-1 rounded font-semibold text-blue-700 hover:underline focus:outline-none focus:ring-2 focus:ring-blue-600"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Organism Court
          </button>
          <span className="text-slate-500">/</span>
          <span className="font-mono font-semibold text-slate-900">
            {activeAgent.id}
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          {agents.map((a) => (
            <button
              key={a.id}
              type="button"
              onClick={() => onSelectAgent(a.id)}
              className={`rounded border px-2.5 py-1 font-mono text-xs transition-colors focus:outline-none focus:ring-2 focus:ring-blue-600 ${
                a.id === activeAgent.id
                  ? 'border-blue-600 bg-blue-50 font-semibold text-blue-800'
                  : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
              }`}
            >
              {a.id}
            </button>
          ))}
        </div>
      </div>

      {/* Agent Identity & Runtime Card */}
      <Card className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-mono text-lg font-semibold text-slate-900">
                {activeAgent.id}
              </h1>
              <Badge variant="info">
                {formatEnumLabel(activeAgent.agent_type)}
              </Badge>
              <Badge
                variant={activeAgent.state === 'awake' ? 'success' : 'secondary'}
                dot
              >
                {formatEnumLabel(activeAgent.state)}
              </Badge>
              {activeAgent.warmth_min_instances === 1 ? (
                <Badge variant="success" dot>
                  Earned warmth (min_instances=1)
                </Badge>
              ) : (
                <Badge variant="muted" dot>
                  Scale-to-zero (min_instances=0)
                </Badge>
              )}
            </div>
            <p className="mt-1.5 text-xs text-slate-600">
              Subject:{' '}
              <span className="font-mono font-semibold text-slate-900">
                {activeAgent.subject_ref}
              </span>{' '}
              · Reasoning Model:{' '}
              <span className="font-mono font-semibold text-slate-900">
                {activeAgent.model_id}
              </span>{' '}
              · Memory Bank:{' '}
              <span className="font-mono text-slate-800">
                {activeAgent.memory_bank_id}
              </span>
            </p>
            <p className="mt-1 font-mono text-xs text-slate-500">
              Engine: {activeAgent.reasoning_engine} · A2A Card:{' '}
              {activeAgent.a2a_card_url}
            </p>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="primary"
              onClick={() => onControlAgent(activeAgent.id, 'wake')}
              disabled={
                activeAgent.state === 'awake' ||
                busyKey === `${activeAgent.id}:wake`
              }
            >
              {activeAgent.state === 'awake' ? 'Agent Awake' : 'Wake Agent'}
            </Button>
            {activeAgent.warmth_min_instances === 0 ? (
              <Button
                variant="secondary"
                onClick={() => onControlAgent(activeAgent.id, 'warm')}
                disabled={busyKey === `${activeAgent.id}:warm`}
              >
                Promote Warmth (min=1)
              </Button>
            ) : (
              <Button
                variant="secondary"
                onClick={() => onControlAgent(activeAgent.id, 'cool')}
                disabled={busyKey === `${activeAgent.id}:cool`}
              >
                Cool Agent (min=0)
              </Button>
            )}
          </div>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-4 border-t border-slate-200 pt-4 text-xs md:grid-cols-4">
          <div>
            <span className="text-slate-500">Tokens Spent Today</span>
            <p className="mt-0.5 font-semibold text-slate-900 num-tabular">
              {formatNumber(activeAgent.tokens_spent_today)} /{' '}
              {formatNumber(activeAgent.token_budget_daily)} (
              {formatUsd(activeAgent.tokens_spent_today_usd)} /{' '}
              {formatUsd(activeAgent.token_budget_daily_usd)})
            </p>
          </div>
          <div>
            <span className="text-slate-500">Last Wake</span>
            <p className="mt-0.5 font-mono text-slate-900 num-tabular">
              {formatShortTime(activeAgent.last_wake_at)}
            </p>
          </div>
          <div>
            <span className="text-slate-500">Last Daily Reflection</span>
            <p className="mt-0.5 font-mono text-slate-900 num-tabular">
              {formatShortTime(activeAgent.last_reflection_at)}
            </p>
          </div>
          <div>
            <span className="text-slate-500">7-Day Wake Frequency</span>
            <p className="mt-0.5 font-semibold text-slate-900 num-tabular">
              {activeAgent.wakes_7d} wakes
            </p>
          </div>
        </div>
      </Card>

      {/* Earned-Autonomy Trust Records & Owner L4 Whitelist */}
      <Card className="p-5">
        <SectionHeader
          title="Trust Ladder Records (trust_records)"
          subtitle={
            firstThresholds
              ? `N1=${firstThresholds.n1} → L2 · N2=${firstThresholds.n2} (0 regressions) → L3 · N3=${firstThresholds.n3} + Owner IAP Whitelist → L4. Any bad outcome resets that action type to L1 immediately.`
              : 'Earned-autonomy progression per action type with explicit Owner IAP Whitelist gate for L4.'
          }
        />
        <div className="mt-3 space-y-3">
          {activeAgent.trust_records.map((rec) => (
            <div
              key={rec.id}
              className="rounded border border-slate-200 bg-slate-50 p-3.5 text-xs"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Shield className="h-4 w-4 text-blue-700" />
                  <span className="font-mono text-sm font-semibold text-slate-900">
                    {rec.action_type}
                  </span>
                  <TrustLevelBadge level={rec.level} />
                  {rec.whitelisted ? (
                    <Badge variant="success">
                      Whitelisted by {rec.whitelisted_by ?? 'IAP Owner'}
                    </Badge>
                  ) : (
                    <Badge variant="outline">Not whitelisted for L4</Badge>
                  )}
                </div>

                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => onToggleWhitelist(rec.id, !rec.whitelisted)}
                  disabled={busyKey === `whitelist:${rec.id}`}
                >
                  {rec.whitelisted ? 'Revoke L4 Whitelist' : 'Whitelist for L4'}
                </Button>
              </div>

              <div className="mt-2.5 grid grid-cols-2 gap-3 sm:grid-cols-5 num-tabular">
                <div className="rounded border border-slate-200 bg-white p-2">
                  <span className="text-slate-500">
                    Recommendations (N1={rec.thresholds.n1})
                  </span>
                  <p className="mt-0.5 font-semibold text-slate-900">
                    {rec.counters.recommendations_correct} / {rec.thresholds.n1}
                  </p>
                </div>
                <div className="rounded border border-slate-200 bg-white p-2">
                  <span className="text-slate-500">
                    Approved Execs (N2={rec.thresholds.n2})
                  </span>
                  <p className="mt-0.5 font-semibold text-slate-900">
                    {rec.counters.approved_executions} / {rec.thresholds.n2}
                  </p>
                </div>
                <div className="rounded border border-slate-200 bg-white p-2">
                  <span className="text-slate-500">Verified Outcomes</span>
                  <p className="mt-0.5 font-semibold text-slate-900">
                    {rec.counters.verified_outcomes}
                  </p>
                </div>
                <div className="rounded border border-slate-200 bg-white p-2">
                  <span className="text-slate-500">
                    Business Successes (N3={rec.thresholds.n3})
                  </span>
                  <p className="mt-0.5 font-semibold text-slate-900">
                    {rec.counters.business_successes} / {rec.thresholds.n3}
                  </p>
                </div>
                <div className="rounded border border-slate-200 bg-white p-2">
                  <span className="text-slate-500">Regressions</span>
                  <p className="mt-0.5 font-semibold text-green-800">
                    {rec.counters.regressions} (0 required)
                  </p>
                </div>
              </div>

              {rec.history.length > 0 && (
                <div className="mt-2.5 space-y-1 border-t border-slate-200 pt-2">
                  {rec.history.map((h, idx) => (
                    <div
                      key={idx}
                      className="flex items-center justify-between text-slate-600"
                    >
                      <span>
                        L{h.from} → L{h.to}: {h.reason}
                      </span>
                      <span className="font-mono text-slate-500 num-tabular">
                        {formatShortTime(h.at)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </Card>

      {/* Living Semantic Model Grid */}
      <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-12">
        {/* Left Column: Entities, Rules, State Machines */}
        <div className="space-y-6 xl:col-span-6">
          <Card className="p-4">
            <SectionHeader
              title="1. Discovered Business Entities"
              subtitle="court_agents/{id}/living_model/entities"
            />
            {lm.entities.length === 0 ? (
              <p className="mt-3 text-xs text-slate-500">
                No direct domain entities owned by this agent type.
              </p>
            ) : (
              <div className="mt-3 space-y-3">
                {lm.entities.map((ent) => (
                  <div
                    key={ent.name}
                    className="rounded border border-slate-200 bg-slate-50 p-3 text-xs"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono font-semibold text-slate-900">
                        {ent.name}
                      </span>
                      <span className="font-mono text-slate-500">
                        {ent.provenance}
                      </span>
                    </div>
                    <p className="mt-1.5 text-slate-600">
                      Correlation Keys:{' '}
                      <span className="font-mono text-slate-800">
                        {ent.correlation_keys.join(', ')}
                      </span>
                    </p>
                    <p className="mt-1 text-slate-600">
                      Tables:{' '}
                      <span className="font-mono text-slate-800">
                        {ent.tables.join(', ')}
                      </span>
                    </p>
                    <div className="mt-2 flex flex-wrap gap-1">
                      {ent.fields.map((f) => (
                        <Badge key={f} variant="outline" className="font-mono">
                          {f}
                        </Badge>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>

          <Card className="p-4">
            <SectionHeader
              title="2. Extracted Business Rules"
              subtitle="court_agents/{id}/living_model/rules (with SHA & line provenance)"
            />
            {lm.rules.length === 0 ? (
              <p className="mt-3 text-xs text-slate-500">
                No code-extracted rules in this namespace.
              </p>
            ) : (
              <div className="mt-3 space-y-3">
                {lm.rules.map((rule) => (
                  <div
                    key={rule.id}
                    className="rounded border border-slate-200 p-3 text-xs"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono font-semibold text-slate-900">
                        {rule.id}
                      </span>
                      <Badge variant="info" className="num-tabular">
                        Conf. {Math.round(rule.confidence * 100)}%
                      </Badge>
                    </div>
                    <pre className="mt-2 overflow-x-auto rounded border border-slate-200 bg-slate-50 p-2 font-mono text-xs text-slate-900">
                      {rule.expression}
                    </pre>
                    <p className="mt-1.5 text-slate-700">{rule.description}</p>
                    <p className="mt-1 font-mono text-slate-500">
                      Provenance: {rule.source_path}@{rule.source_sha}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </Card>

          <Card className="p-4">
            <SectionHeader
              title="3. State Machines"
              subtitle="court_agents/{id}/living_model/state_machines"
            />
            {lm.state_machines.length === 0 ? (
              <p className="mt-3 text-xs text-slate-500">
                No state machines registered in this agent model.
              </p>
            ) : (
              <div className="mt-3 space-y-4">
                {lm.state_machines.map((sm) => (
                  <div key={sm.entity} className="space-y-2 text-xs">
                    <div className="font-mono font-semibold text-slate-900">
                      Entity: {sm.entity}
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {sm.states.map((st) => (
                        <Badge key={st} variant="secondary" className="font-mono">
                          {st}
                        </Badge>
                      ))}
                    </div>
                    <div className="divide-y divide-slate-200 rounded border border-slate-200">
                      {sm.transitions.map((tr, i) => (
                        <div
                          key={i}
                          className="flex flex-wrap items-center justify-between gap-2 px-3 py-1.5"
                        >
                          <span className="font-mono text-slate-900">
                            {tr.from} → {tr.to}
                          </span>
                          <span className="font-mono text-slate-500">
                            {tr.trigger} ({tr.route})
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>

        {/* Right Column: Decision Boundaries, Routes, Dependencies, Commits */}
        <div className="space-y-6 xl:col-span-6">
          <Card className="p-4">
            <SectionHeader
              title="4. Empirical Decision Boundaries & Feature Flags"
              subtitle="Learned via synthetic bisection probes (X-Synthetic: true on x-godview-probe-safe routes)"
            />
            {lm.decision_boundaries.length === 0 ? (
              <p className="mt-3 text-xs text-slate-500">
                No synthetic probe decision boundaries configured for this agent.
              </p>
            ) : (
              <div className="mt-3 space-y-2.5">
                {lm.decision_boundaries.map((db) => (
                  <div
                    key={db.input}
                    className="flex flex-wrap items-center justify-between gap-2 rounded border border-slate-200 bg-slate-50 p-3 text-xs"
                  >
                    <div>
                      <span className="font-mono font-semibold text-slate-900">
                        {db.input}
                      </span>
                      <p className="mt-0.5 text-slate-600">
                        Observed:{' '}
                        <span className="font-mono font-semibold text-slate-900">
                          {db.boundary}
                        </span>{' '}
                        · Expected:{' '}
                        <span className="font-mono text-slate-800">
                          {db.expected}
                        </span>
                      </p>
                      <p className="mt-0.5 text-slate-500 num-tabular">
                        Method: {db.method} · Confidence{' '}
                        {Math.round(db.confidence * 100)}%
                      </p>
                    </div>
                    <Badge
                      variant={db.status === 'aligned' ? 'success' : 'warning'}
                      dot
                    >
                      {formatEnumLabel(db.status)}
                    </Badge>
                  </div>
                ))}
              </div>
            )}

            {lm.feature_flags.length > 0 && (
              <div className="mt-3 border-t border-slate-200 pt-3">
                <p className="text-xs font-semibold text-slate-700">
                  Discovered Feature Flags
                </p>
                <div className="mt-1.5 space-y-1">
                  {lm.feature_flags.map((ff) => (
                    <div
                      key={ff.name}
                      className="flex items-center justify-between font-mono text-xs"
                    >
                      <span className="text-slate-900">
                        {ff.name} = {ff.default_value}
                      </span>
                      <span className="text-slate-500">{ff.source_path}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </Card>

          <Card className="p-4">
            <SectionHeader
              title="5. Discovered Routes & Contract Extensions"
              subtitle="court_agents/{id}/living_model/routes (x-godview-probe-safe & x-godview-admin-action)"
            />
            {lm.routes.length === 0 ? (
              <p className="mt-3 text-xs text-slate-500">
                No HTTP/RPC routes exposed directly by this subject.
              </p>
            ) : (
              <div className="mt-3 space-y-2.5">
                {lm.routes.map((rt) => (
                  <div
                    key={`${rt.method}-${rt.path}`}
                    className="rounded border border-slate-200 p-3 text-xs"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-1.5 font-mono font-semibold text-slate-900">
                        <span className="rounded bg-slate-200 px-1.5 py-0.5 text-[10px]">
                          {rt.method}
                        </span>
                        <span>{rt.path}</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        {rt.probe_safe && (
                          <Badge variant="success">x-godview-probe-safe</Badge>
                        )}
                        {rt.admin && (
                          <Badge variant="warning">
                            Admin: {rt.admin_capability}
                          </Badge>
                        )}
                      </div>
                    </div>
                    <p className="mt-1 text-slate-600">
                      Auth: {rt.auth} · Writes: {rt.writes.join(', ')}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </Card>

          <Card className="p-4">
            <SectionHeader
              title="6. Outbound Dependencies & 7. Recent Commits"
              subtitle="court_agents/{id}/living_model/{dependencies, commit_diffs}"
            />
            {lm.dependencies.length > 0 && (
              <div className="mt-3 space-y-1.5">
                {lm.dependencies.map((dep) => (
                  <div
                    key={dep.target}
                    className="flex flex-wrap items-center justify-between rounded border border-slate-200 bg-slate-50 px-3 py-2 text-xs"
                  >
                    <span className="font-mono font-semibold text-slate-900">
                      → {dep.target} ({dep.kind})
                    </span>
                    <span className="text-slate-600">
                      {dep.protocol} · {dep.evidence}
                    </span>
                  </div>
                ))}
              </div>
            )}

            <div className="mt-4 space-y-2.5">
              {lm.commit_diffs.length === 0 ? (
                <p className="text-xs text-slate-500">
                  No recent commit diffs recorded for this agent.
                </p>
              ) : (
                lm.commit_diffs.map((cd) => (
                  <div
                    key={cd.sha}
                    className="rounded border border-slate-200 p-3 text-xs"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono font-semibold text-blue-700">
                        commit {cd.sha}
                      </span>
                      <span className="text-slate-500 num-tabular">
                        {cd.author} · {formatShortTime(cd.occurred_at)}
                      </span>
                    </div>
                    <p className="mt-1 font-semibold text-slate-900">
                      {cd.business_summary}
                    </p>
                    <p className="mt-1 font-mono text-slate-500">
                      Files: {cd.files_changed.join(', ')}
                    </p>
                  </div>
                ))
              )}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
};
