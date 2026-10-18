import { AlertTriangle, ArrowUpRight, Flame, Radar, RefreshCw, ShieldCheck } from 'lucide-react';
import React from 'react';
import type { CourtAgent, CourtAgentType, PerceptionReport } from '../api/types';
import {
  Badge,
  Button,
  Card,
  SectionHeader,
  TrustLevelBadge,
  formatEnumLabel,
  formatNumber,
  formatShortTime,
  formatUsd,
} from './ui';

const AGENT_TYPE_META: Record<
  CourtAgentType,
  { label: string; description: string; xCenter: number }
> = {
  application: {
    label: 'Application Agents',
    description: 'Code, contracts, routes & boundary probes per discovered service',
    xCenter: 150,
  },
  business_line: {
    label: 'Business-Line Agents',
    description: 'Cross-app process mining, conformance & L4 workflow replay',
    xCenter: 410,
  },
  data: {
    label: 'Data Agents',
    description: 'Read-only schema, lineage, lock trees & SQL integrity sentinels',
    xCenter: 670,
  },
  change: {
    label: 'Change Agents',
    description: 'Business-language PR risk reviews, deploy correlation & canaries',
    xCenter: 930,
  },
  platform: {
    label: 'Platform Agent',
    description: 'Golden-signal gates, tfstate drift diff & cost governance',
    xCenter: 1190,
  },
};

const AGENT_TYPES_ORDER: CourtAgentType[] = [
  'application',
  'business_line',
  'data',
  'change',
  'platform',
];

export interface OrganismTabProps {
  perceive: PerceptionReport;
  court: CourtAgent[];
  onSelectAgent: (agentId: string) => void;
  onControlAgent: (
    agentId: string,
    action: 'wake' | 'retire' | 'warm' | 'cool'
  ) => Promise<void>;
  onRePerceive: () => Promise<void>;
  busyKey: string | null;
}

export const OrganismTab: React.FC<OrganismTabProps> = ({
  perceive,
  court,
  onSelectAgent,
  onControlAgent,
  onRePerceive,
  busyKey,
}) => {
  if (!perceive) {
    return (
      <Card className="p-8 text-center">
        <AlertTriangle className="mx-auto h-8 w-8 text-amber-500" />
        <h3 className="mt-3 text-base font-semibold text-slate-900">
          Perception Telemetry Not Measured Yet
        </h3>
        <p className="mt-1 text-xs text-slate-500 max-w-md mx-auto">
          The Sovereign control plane <code>/api/v1/perceive</code> endpoint returned no data or is currently unreachable.
        </p>
      </Card>
    );
  }

  if (!court) {
    return (
      <Card className="p-8 text-center">
        <AlertTriangle className="mx-auto h-8 w-8 text-amber-500" />
        <h3 className="mt-3 text-base font-semibold text-slate-900">
          Court Telemetry Not Measured Yet
        </h3>
        <p className="mt-1 text-xs text-slate-500 max-w-md mx-auto">
          The Sovereign control plane <code>/api/v1/court</code> endpoint returned no data or is currently unreachable.
        </p>
      </Card>
    );
  }

  const warmCount = court.filter((a) => a.warmth_min_instances === 1).length;
  const totalTokensToday = court.reduce((acc, a) => acc + a.tokens_spent_today, 0);
  const totalAgentUsdToday = court.reduce(
    (acc, a) => acc + a.tokens_spent_today_usd,
    0
  );

  return (
    <div className="space-y-6">
      {/* Top KPI Ribbon — Equal Height Cards */}
      <div className="grid grid-cols-1 items-stretch gap-4 md:grid-cols-2 xl:grid-cols-5">
        <Card className="flex h-full flex-col justify-between p-4">
          <div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs text-slate-500">Sovereign Operator</span>
              <Badge
                variant={
                  perceive.sovereign_status === 'healthy' ? 'success' : 'warning'
                }
                dot
              >
                {formatEnumLabel(perceive.sovereign_status)}
              </Badge>
            </div>
            <p className="mt-2 font-mono text-sm font-semibold text-slate-900">
              {perceive.sovereign_id}
            </p>
          </div>
          <p className="mt-2 text-xs text-slate-600 num-tabular">
            min_instances=1 · Sentinel loop {perceive.sentinel_cadence_s}s
          </p>
        </Card>

        <Card className="flex h-full flex-col justify-between p-4">
          <div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs text-slate-500">
                Resting LLM Spend
              </span>
              <Badge variant="info">{perceive.tokens_at_rest} tok at rest</Badge>
            </div>
            <p className="mt-2 text-xl font-semibold text-slate-900 num-tabular">
              {perceive.tokens_at_rest} tok/tick
            </p>
          </div>
          <p className="mt-2 text-xs text-slate-600">
            Compiled checks run in-process; wakes carry EvidencePack
          </p>
        </Card>

        <Card className="flex h-full flex-col justify-between p-4">
          <div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs text-slate-500">
                Spawned Court &amp; Warmth
              </span>
              <Badge variant="secondary">{court.length} agents</Badge>
            </div>
            <p className="mt-2 text-xl font-semibold text-slate-900 num-tabular">
              {warmCount} Warm / {court.length - warmCount} Cold
            </p>
          </div>
          <p className="mt-2 text-xs text-slate-600">
            Warm tier ({perceive.warm_tier_spec}) · {perceive.active_window_label}
          </p>
        </Card>

        <Card className="flex h-full flex-col justify-between p-4">
          <div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs text-slate-500">
                Court Token Budget Today
              </span>
              <Badge variant="outline" className="num-tabular">
                {formatUsd(totalAgentUsdToday + perceive.sovereign_spent_today_usd)}{' '}
                / {formatUsd(perceive.court_ceiling_daily_usd)}
              </Badge>
            </div>
            <p className="mt-2 text-xl font-semibold text-slate-900 num-tabular">
              {formatNumber(totalTokensToday)} tokens
            </p>
          </div>
          <p className="mt-2 text-xs text-slate-600 num-tabular">
            Sovereign {formatUsd(perceive.sovereign_spent_today_usd)} /{' '}
            {formatUsd(perceive.sovereign_budget_daily_usd)} · Reserve{' '}
            {perceive.escalation_reserve_pct}%
          </p>
        </Card>

        <Card className="flex h-full flex-col justify-between p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs text-slate-500">
              Configured Models (Live API)
            </span>
            <Badge variant="info">Global</Badge>
          </div>
          <div className="mt-2 space-y-1">
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-500">Reasoning:</span>
              <span className="font-mono font-semibold text-slate-900">
                {perceive.reasoning_model}
              </span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-500">Escalation:</span>
              <span className="font-mono font-semibold text-slate-900">
                {perceive.escalation_model}
              </span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-500">Embeddings:</span>
              <span className="font-mono font-semibold text-slate-700">
                {perceive.embedding_model}
              </span>
            </div>
          </div>
        </Card>
      </div>

      {/* Architectural SVG + CSS Organism Court Org-Chart */}
      <Card className="p-5">
        <SectionHeader
          title="Sovereign & Specialist Court Org-Chart"
          subtitle="Live A2A hierarchy discovered via zero-config identity perception. Click any court agent node to inspect its living semantic model."
          right={
            <div className="flex items-center gap-2">
              <Badge variant="outline">
                Scope: {perceive.managed_scope_label}
              </Badge>
              <Button
                variant="secondary"
                size="sm"
                onClick={onRePerceive}
                disabled={busyKey === 'perceive'}
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Re-run Self-Awareness Protocol
              </Button>
            </div>
          }
        />

        <div className="mt-4 overflow-x-auto">
          <div className="min-w-[1160px]">
            <svg
              data-testid="organism-org-chart-svg"
              viewBox="0 0 1340 310"
              className="w-full rounded border border-slate-200 bg-slate-50"
              role="img"
              aria-label="Sovereign and Specialist Court Org-Chart Diagram"
            >
              <defs>
                <marker
                  id="arrow-blue"
                  viewBox="0 0 10 10"
                  refX="7"
                  refY="5"
                  markerWidth="6"
                  markerHeight="6"
                  orient="auto-start-reverse"
                >
                  <path d="M 0 1 L 8 5 L 0 9 z" fill="#1A73E8" />
                </marker>
                <marker
                  id="arrow-slate"
                  viewBox="0 0 10 10"
                  refX="7"
                  refY="5"
                  markerWidth="6"
                  markerHeight="6"
                  orient="auto-start-reverse"
                >
                  <path d="M 0 1 L 8 5 L 0 9 z" fill="#64748B" />
                </marker>
              </defs>

              <line
                x1="0"
                y1="122"
                x2="1340"
                y2="122"
                stroke="#E2E8F0"
                strokeDasharray="4 4"
              />
              <text
                x="18"
                y="114"
                fill="#64748B"
                fontSize="10"
                fontWeight="600"
              >
                PLANE B · SOVEREIGN CONTROL &amp; 0-TOKEN SENTINEL ENGINE (Cloud Run min_instances=1)
              </text>
              <text
                x="18"
                y="142"
                fill="#64748B"
                fontSize="10"
                fontWeight="600"
              >
                A2A SWARM MESH &amp; SINGLE TRUSTGATE CAPABILITY ENDPOINT (POST /v1/capabilities/&#123;id&#125;:invoke)
              </text>

              {/* Sovereign Root Box */}
              <rect
                x="350"
                y="16"
                width="640"
                height="86"
                rx="6"
                fill="#FFFFFF"
                stroke="#0F172A"
                strokeWidth="1.5"
              />
              <rect
                x="350"
                y="16"
                width="640"
                height="4"
                rx="2"
                fill="#1A73E8"
              />
              <circle cx="372" cy="40" r="5" fill="#188038" />
              <text
                x="386"
                y="44"
                fill="#0F172A"
                fontSize="13"
                fontWeight="600"
              >
                {perceive.sovereign_id} (Sovereign Operator)
              </text>
              <text
                x="972"
                y="44"
                textAnchor="end"
                fill="#334155"
                fontSize="11"
                fontWeight="600"
              >
                {perceive.sentinel_cadence_s}s Cadence · {perceive.tokens_at_rest} Tokens at Rest
              </text>

              {/* 4 Organs inside Sovereign */}
              {[
                { x: 364, label: '1. Perception (6 Senses)' },
                { x: 518, label: '2. Sentinel Engine (10s)' },
                { x: 672, label: '3. Nervous System (Pub/Sub)' },
                { x: 826, label: '4. Court & TrustGate' },
              ].map((organ) => (
                <g key={organ.label}>
                  <rect
                    x={organ.x}
                    y="58"
                    width="144"
                    height="30"
                    rx="4"
                    fill="#F8FAFC"
                    stroke="#CBD5E1"
                  />
                  <text
                    x={organ.x + 72}
                    y="77"
                    textAnchor="middle"
                    fill="#334155"
                    fontSize="10"
                    fontWeight="600"
                  >
                    {organ.label}
                  </text>
                </g>
              ))}

              {/* Orthogonal Bus from Sovereign to 5 Specialist Court Pillars */}
              <line
                x1="670"
                y1="102"
                x2="670"
                y2="152"
                stroke="#1A73E8"
                strokeWidth="1.75"
              />
              <line
                x1="150"
                y1="152"
                x2="1190"
                y2="152"
                stroke="#64748B"
                strokeWidth="1.5"
              />

              {AGENT_TYPES_ORDER.map((typeKey) => {
                const meta = AGENT_TYPE_META[typeKey];
                const agentsOfType = court.filter((a) => a.agent_type === typeKey);
                const primaryAgent = agentsOfType[0];
                const isWarm = agentsOfType.some(
                  (a) => a.warmth_min_instances === 1
                );
                const isAwake = agentsOfType.some((a) => a.state === 'awake');
                const highestTrust = agentsOfType.reduce((max, a) => {
                  const agentMax = a.trust_records.reduce(
                    (m, r) => Math.max(m, r.level),
                    0
                  );
                  return Math.max(max, agentMax);
                }, 0);

                return (
                  <g
                    key={typeKey}
                    className="cursor-pointer"
                    onClick={() => {
                      if (primaryAgent) onSelectAgent(primaryAgent.id);
                    }}
                  >
                    <line
                      x1={meta.xCenter}
                      y1="152"
                      x2={meta.xCenter}
                      y2="184"
                      stroke={isAwake || isWarm ? '#1A73E8' : '#64748B'}
                      strokeWidth={isAwake || isWarm ? '1.75' : '1.25'}
                      markerEnd={
                        isAwake || isWarm ? 'url(#arrow-blue)' : 'url(#arrow-slate)'
                      }
                    />
                    <rect
                      x={meta.xCenter - 115}
                      y="186"
                      width="230"
                      height="106"
                      rx="6"
                      fill="#FFFFFF"
                      stroke={isAwake ? '#1A73E8' : '#CBD5E1'}
                      strokeWidth={isAwake ? '1.75' : '1.25'}
                    />
                    <rect
                      x={meta.xCenter - 115}
                      y="186"
                      width="230"
                      height="4"
                      rx="2"
                      fill={
                        isAwake
                          ? '#1A73E8'
                          : isWarm
                          ? '#34A853'
                          : '#94A3B8'
                      }
                    />
                    <text
                      x={meta.xCenter - 102}
                      y="209"
                      fill="#0F172A"
                      fontSize="12"
                      fontWeight="600"
                    >
                      {meta.label}
                    </text>
                    <text
                      x={meta.xCenter + 102}
                      y="209"
                      textAnchor="end"
                      fill={isWarm ? '#188038' : '#64748B'}
                      fontSize="10"
                      fontWeight="600"
                    >
                      {isWarm ? 'Warm (min=1)' : 'Cold (min=0)'}
                    </text>
                    <text
                      x={meta.xCenter - 102}
                      y="228"
                      fill="#334155"
                      fontSize="11"
                      fontFamily="monospace"
                    >
                      {primaryAgent ? primaryAgent.subject_ref : 'none'}
                    </text>
                    <text
                      x={meta.xCenter - 102}
                      y="247"
                      fill="#64748B"
                      fontSize="10"
                    >
                      State: {primaryAgent ? formatEnumLabel(primaryAgent.state) : '—'} · Max Trust L
                      {highestTrust}
                    </text>
                    <text
                      x={meta.xCenter - 102}
                      y="265"
                      fill="#334155"
                      fontSize="10"
                    >
                      Tokens today:{' '}
                      {primaryAgent
                        ? `${formatNumber(primaryAgent.tokens_spent_today)} (${formatUsd(
                            primaryAgent.tokens_spent_today_usd
                          )}/${formatUsd(primaryAgent.token_budget_daily_usd)})`
                        : '0'}
                    </text>
                    <text
                      x={meta.xCenter - 102}
                      y="282"
                      fill="#1A73E8"
                      fontSize="10"
                      fontWeight="600"
                    >
                      Inspect Living Semantic Model →
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>
        </div>
      </Card>

      {/* Court Agents Detailed Roster */}
      <div className="space-y-3">
        <SectionHeader
          title="Court Agents — Trust Ladder, Warmth & Token Governor"
          subtitle="Each agent runs on Vertex AI Agent Engine with private Memory Bank and shared Knowledge Graph namespace. Bad outcomes reset that action type to L1 immediately."
        />

        <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
          {court.length === 0 ? (
            <Card className="p-8 text-center lg:col-span-2">
              <h4 className="text-sm font-semibold text-slate-700">No Court Agents Configured</h4>
              <p className="mt-1 text-xs text-slate-500">The estate has no active court agents registered in the Sovereign control plane.</p>
            </Card>
          ) : (
            court.map((agent) => {
            const pctSpent = Math.min(
              100,
              Math.round(
                (agent.tokens_spent_today_usd /
                  Math.max(0.01, agent.token_budget_daily_usd)) *
                  100
              )
            );
            const firstThresholds = agent.trust_records[0]?.thresholds;
            return (
              <Card key={agent.id} className="p-4">
                <div className="border-b border-slate-200 pb-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={() => onSelectAgent(agent.id)}
                        className="rounded font-mono text-sm font-semibold text-blue-700 hover:underline focus:outline-none focus:ring-2 focus:ring-blue-600"
                      >
                        {agent.id}
                      </button>
                      <Badge variant="outline">
                        {formatEnumLabel(agent.agent_type)}
                      </Badge>
                      <Badge
                        variant={
                          agent.state === 'awake'
                            ? 'info'
                            : agent.state === 'sentinel_compiled'
                            ? 'success'
                            : 'secondary'
                        }
                        dot
                      >
                        {formatEnumLabel(agent.state)}
                      </Badge>
                    </div>
                    {agent.warmth_min_instances === 1 ? (
                      <Badge variant="success" dot>
                        Warm (min=1)
                      </Badge>
                    ) : (
                      <Badge variant="muted" dot>
                        Cold (min=0)
                      </Badge>
                    )}
                  </div>
                  <p className="mt-1 text-xs text-slate-600">
                    Subject:{' '}
                    <span className="font-mono font-semibold text-slate-900">
                      {agent.subject_ref}
                    </span>{' '}
                    · Model:{' '}
                    <span className="font-mono text-slate-800">
                      {agent.model_id}
                    </span>
                  </p>
                </div>

                {/* Metrics Grid */}
                <div className="mt-3 grid grid-cols-3 gap-3 rounded border border-slate-200 bg-slate-50 p-2.5 text-xs">
                  <div>
                    <span className="text-slate-500">Last Wake</span>
                    <p className="mt-0.5 font-mono font-semibold text-slate-900 num-tabular">
                      {formatShortTime(agent.last_wake_at)}
                    </p>
                  </div>
                  <div>
                    <span className="text-slate-500">Wakes (7d)</span>
                    <p className="mt-0.5 font-semibold text-slate-900 num-tabular">
                      {agent.wakes_7d} wakes
                    </p>
                  </div>
                  <div>
                    <span className="text-slate-500">Daily Token Spend</span>
                    <p className="mt-0.5 font-semibold text-slate-900 num-tabular">
                      {formatNumber(agent.tokens_spent_today)} /{' '}
                      {formatNumber(agent.token_budget_daily)} (
                      {formatUsd(agent.tokens_spent_today_usd)} /{' '}
                      {formatUsd(agent.token_budget_daily_usd)})
                    </p>
                    <div className="mt-1 h-1.5 w-full overflow-hidden rounded bg-slate-200">
                      <div
                        className={`h-full ${
                          agent.budget_exhausted ? 'bg-amber-600' : 'bg-blue-600'
                        }`}
                        style={{ width: `${pctSpent}%` }}
                      />
                    </div>
                  </div>
                </div>

                {/* Trust Records per Action Type */}
                <div className="mt-3">
                  <div className="flex items-center justify-between text-xs font-semibold text-slate-700">
                    <span>Trust Level per Capability Action Type</span>
                    {firstThresholds && (
                      <span className="font-normal text-slate-500 num-tabular">
                        Gates: N1={firstThresholds.n1} · N2={firstThresholds.n2} · N3=
                        {firstThresholds.n3}
                      </span>
                    )}
                  </div>
                  <div className="mt-1.5 divide-y divide-slate-200 rounded border border-slate-200">
                    {agent.trust_records.map((rec) => (
                      <div
                        key={rec.id}
                        className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-xs"
                      >
                        <div className="flex items-center gap-2">
                          <ShieldCheck className="h-3.5 w-3.5 text-blue-700" />
                          <span className="font-mono font-semibold text-slate-900">
                            {rec.action_type}
                          </span>
                          {rec.whitelisted && (
                            <Badge variant="success">L4 Whitelisted</Badge>
                          )}
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="text-slate-500 num-tabular">
                            Rec {rec.counters.recommendations_correct}/
                            {rec.thresholds.n1} · Exec{' '}
                            {rec.counters.approved_executions}/
                            {rec.thresholds.n2} · Biz{' '}
                            {rec.counters.business_successes}/
                            {rec.thresholds.n3}
                          </span>
                          <TrustLevelBadge level={rec.level} />
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Footer Controls */}
                <div className="mt-3 flex flex-wrap items-center justify-between gap-2 pt-2">
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={() => onSelectAgent(agent.id)}
                  >
                    Inspect Living Model
                    <ArrowUpRight className="h-3.5 w-3.5" />
                  </Button>

                  <div className="flex items-center gap-1.5">
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => onControlAgent(agent.id, 'wake')}
                      disabled={
                        agent.state === 'awake' || busyKey === `${agent.id}:wake`
                      }
                    >
                      {agent.state === 'awake' ? 'Agent Awake' : 'Wake Now'}
                    </Button>
                    {agent.warmth_min_instances === 0 ? (
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => onControlAgent(agent.id, 'warm')}
                        disabled={busyKey === `${agent.id}:warm`}
                      >
                        <Flame className="h-3.5 w-3.5 text-amber-600" />
                        Warm (min=1)
                      </Button>
                    ) : (
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => onControlAgent(agent.id, 'cool')}
                        disabled={busyKey === `${agent.id}:cool`}
                      >
                        Cool (min=0)
                      </Button>
                    )}
                  </div>
                </div>
              </Card>
            );
          }))}
        </div>
      </div>

      {/* Self-Awareness Sense Adapters & Discovered Services */}
      <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-12">
        <Card className="p-4 xl:col-span-5">
          <SectionHeader
            title="Discovered Sense Adapters"
            subtitle="Identity-only perception availability report (Available / Partial / Unavailable)"
          />
          <div className="mt-3 divide-y divide-slate-200">
            {perceive.senses.map((sense) => (
              <div key={sense.adapter_family} className="py-2.5 text-xs">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Radar className="h-3.5 w-3.5 text-blue-700" />
                    <span className="font-mono font-semibold text-slate-900">
                      {sense.adapter_family}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-slate-500 num-tabular">
                      {sense.discovered_count} targets
                    </span>
                    <Badge
                      variant={
                        sense.availability === 'available'
                          ? 'success'
                          : sense.availability === 'partial'
                          ? 'warning'
                          : 'muted'
                      }
                      dot
                    >
                      {formatEnumLabel(sense.availability)}
                    </Badge>
                  </div>
                </div>
                <p className="mt-1 text-slate-600">{sense.evidence}</p>
              </div>
            ))}
          </div>
        </Card>

        <Card className="p-4 xl:col-span-7">
          <SectionHeader
            title="Discovered Topology & Inferred Business Roles"
            subtitle={`Persisted in Firestore (${perceive.memory_db_name}) with 768-dim purpose_embedding`}
          />
          <div className="mt-3 overflow-x-auto">
            <table className="w-full border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50 text-slate-600">
                  <th className="px-3 py-2 font-semibold">Service / Resource</th>
                  <th className="px-3 py-2 font-semibold">Kind</th>
                  <th className="px-3 py-2 font-semibold">Inferred Role</th>
                  <th className="px-3 py-2 text-right font-semibold">Conf.</th>
                  <th className="px-3 py-2 font-semibold">Owner Agent</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {perceive.discovered_services.map((svc) => (
                  <tr key={svc.id} className="hover:bg-slate-50">
                    <td className="px-3 py-2 font-mono font-semibold text-slate-900">
                      {svc.name}
                    </td>
                    <td className="px-3 py-2">
                      <Badge variant="outline">
                        {formatEnumLabel(svc.kind)}
                      </Badge>
                    </td>
                    <td className="px-3 py-2 text-slate-700">
                      {svc.inferred_role}
                    </td>
                    <td className="px-3 py-2 text-right font-semibold text-slate-900 num-tabular">
                      {Math.round(svc.role_confidence * 100)}%
                    </td>
                    <td className="px-3 py-2">
                      <button
                        type="button"
                        onClick={() => onSelectAgent(svc.owner_agent_id)}
                        className="rounded font-mono font-semibold text-blue-700 hover:underline focus:outline-none focus:ring-2 focus:ring-blue-600"
                      >
                        {svc.owner_agent_id}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </div>
  );
};
