import { Globe } from 'lucide-react';
import React from 'react';
import type { JourneyConformanceStatus, JourneyModel } from '../api/types';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  SectionHeader,
  formatEnumLabel,
  formatCurrency,
  formatNumber,
} from './ui';

export interface JourneysTabProps {
  journeys: JourneyModel[];
  onSelectAgent: (agentId: string) => void;
}

function conformanceVariant(status: JourneyConformanceStatus) {
  switch (status) {
    case 'CONFORMANT':
      return 'success' as const;
    case 'SKIPPED':
    case 'REORDERED':
      return 'warning' as const;
    case 'STALLED':
    case 'LOOPED':
      return 'danger' as const;
  }
}

function truncateRoute(op: string, maxLen = 35): string {
  if (op.length <= maxLen) return op;
  return `${op.slice(0, maxLen - 1)}…`;
}

export const JourneysTab: React.FC<JourneysTabProps> = ({
  journeys,
  onSelectAgent,
}) => {
  const journey = journeys[0];

  if (!journey) {
    return (
      <EmptyState
        title="No Workflows Discovered Yet"
        description="Once OTel spans or CDC state transitions land in BigQuery godview_ledger, the Business-Line Agent mines the directly-follows graph."
      />
    );
  }

  const NODE_WIDTH = 228;
  const stepPositions: Record<string, { x: number; y: number }> = {};
  journey.steps.forEach((step, idx) => {
    stepPositions[step.id] = {
      x: 24 + idx * 272,
      y: 95,
    };
  });

  return (
    <div className="space-y-6">
      {/* Top KPI Ribbon */}
      <div className="grid grid-cols-1 items-stretch gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Card className="flex h-full flex-col justify-between p-4">
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-500">
              Total Value at Risk
            </span>
            <Badge variant="warning" dot>
              Active exposure
            </Badge>
          </div>
          <p className="mt-2 text-2xl font-semibold text-slate-900 num-tabular">
            {formatCurrency(journey.impact_model.total_value_at_risk)}
          </p>
          <p className="mt-1 text-xs text-slate-600 num-tabular">
            Avg ticket {formatCurrency(journey.impact_model.avg_ticket_value)} ·{' '}
            {journey.impact_model.violated_journeys_count} affected cohorts
          </p>
        </Card>

        <Card className="flex h-full flex-col justify-between p-4">
          <span className="text-xs text-slate-500">
            Mined Workflow &amp; Owner Agent
          </span>
          <p className="mt-2 font-mono text-sm font-semibold text-slate-900">
            {journey.journey_key}
          </p>
          <p className="mt-1 text-xs text-slate-600">
            Owner:{' '}
            <button
              type="button"
              onClick={() => onSelectAgent(journey.owner_agent_id)}
              className="rounded font-mono font-semibold text-blue-700 hover:underline focus:outline-none focus:ring-2 focus:ring-blue-600"
            >
              {journey.owner_agent_id}
            </button>
          </p>
        </Card>

        <Card className="flex h-full flex-col justify-between p-4">
          <span className="text-xs text-slate-500">
            Conformance Violations (24h)
          </span>
          <p className="mt-2 text-xl font-semibold text-slate-900 num-tabular">
            {journey.conformance_rules.reduce(
              (acc, r) => acc + r.violations_24h,
              0
            )}{' '}
            Violations
          </p>
          <p className="mt-1 text-xs text-slate-600 num-tabular">
            {journey.impact_model.stuck_journeys_count} stuck journeys in
            replay queue
          </p>
        </Card>

        <Card className="flex h-full flex-col justify-between p-4">
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-500">
              Informational Tenant P95 Latency
            </span>
            <Badge variant="outline">Observed P95</Badge>
          </div>
          <p className="mt-2 text-xl font-semibold text-slate-900 num-tabular">
            {journey.kpi_rollups.find((k) => k.kpi === 'journey_p95_latency_ms')?.value != null
              ? `${formatNumber(journey.kpi_rollups.find((k) => k.kpi === 'journey_p95_latency_ms')!.value)} ms`
              : 'Not measured yet'}
          </p>
          <p className="mt-1 text-xs text-slate-600">
            Measured from span duration telemetry
          </p>
        </Card>
      </div>

      {/* Directly-Follows DAG Process Map */}
      <Card className="p-5">
        <SectionHeader
          title="Process Map — Directly-Follows Graph (DFG) & Conformance Overlay"
          subtitle={`Intended order mined from ${journey.intended_order_source}. Solid blue/slate edges show conformant transitions; dashed arcs highlight Skipped and Reordered conformance anomalies.`}
          right={
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                window.location.hash = `#globe/journey/${encodeURIComponent(journey.name || journey.journey_key)}`;
              }}
              className="flex items-center gap-1.5"
            >
              <Globe className="h-3.5 w-3.5" />
              <span>View Path on 3D Globe</span>
            </Button>
          }
        />

        <div className="mt-4 overflow-x-auto">
          <div className="min-w-[1280px]">
            <svg
              data-testid="journey-dag-svg"
              viewBox="0 0 1365 265"
              className="w-full rounded border border-slate-200 bg-slate-50"
              role="img"
              aria-label="Directly-follows process map DAG"
            >
              <defs>
                <marker
                  id="dag-arrow-ok"
                  viewBox="0 0 10 10"
                  refX="8"
                  refY="5"
                  markerWidth="6"
                  markerHeight="6"
                  orient="auto-start-reverse"
                >
                  <path d="M 0 1 L 8 5 L 0 9 z" fill="#1A73E8" />
                </marker>
                <marker
                  id="dag-arrow-warn"
                  viewBox="0 0 10 10"
                  refX="8"
                  refY="5"
                  markerWidth="6"
                  markerHeight="6"
                  orient="auto-start-reverse"
                >
                  <path d="M 0 1 L 8 5 L 0 9 z" fill="#D93025" />
                </marker>
              </defs>

              {/* Edges from directly_follows */}
              {Object.entries(journey.directly_follows).map(([edgeKey, edge]) => {
                const src = stepPositions[edge.from];
                const dst = stepPositions[edge.to];
                if (!src || !dst) return null;

                if (edge.kind === 'conformant') {
                  const x1 = src.x + NODE_WIDTH;
                  const y1 = src.y + 42;
                  const x2 = dst.x;
                  const y2 = dst.y + 42;
                  const midX = (x1 + x2) / 2;
                  return (
                    <g key={edgeKey}>
                      <path
                        d={`M ${x1} ${y1} C ${midX} ${y1}, ${midX} ${y2}, ${x2} ${y2}`}
                        fill="none"
                        stroke="#1A73E8"
                        strokeWidth={edge.count > 150 ? '2.5' : '1.75'}
                        markerEnd="url(#dag-arrow-ok)"
                      />
                      <rect
                        x={midX - 22}
                        y={y1 - 18}
                        width="44"
                        height="15"
                        rx="3"
                        fill="#FFFFFF"
                        stroke="#CBD5E1"
                      />
                      <text
                        x={midX}
                        y={y1 - 8}
                        textAnchor="middle"
                        fill="#0F172A"
                        fontSize="9"
                        fontWeight="600"
                      >
                        n={edge.count}
                      </text>
                    </g>
                  );
                }

                // Non-conformant arc (Skipped or Reordered)
                const isSkipped = edge.kind === 'skipped';
                const startX = src.x + NODE_WIDTH / 2;
                const endX = dst.x + NODE_WIDTH / 2;
                const arcY = isSkipped ? 24 : 238;
                const startY = isSkipped ? src.y : src.y + 84;
                const endY = isSkipped ? dst.y : dst.y + 84;
                const midX = (startX + endX) / 2;

                return (
                  <g key={edgeKey}>
                    <path
                      d={`M ${startX} ${startY} Q ${midX} ${arcY} ${endX} ${endY}`}
                      fill="none"
                      stroke="#D93025"
                      strokeWidth="1.75"
                      strokeDasharray="5 4"
                      markerEnd="url(#dag-arrow-warn)"
                    />
                    <rect
                      x={midX - 88}
                      y={isSkipped ? 26 : 212}
                      width="176"
                      height="20"
                      rx="4"
                      fill="#FEF2F2"
                      stroke="#FECACA"
                    />
                    <text
                      x={midX}
                      y={isSkipped ? 39 : 225}
                      textAnchor="middle"
                      fill="#991B1B"
                      fontSize="10"
                      fontWeight="600"
                    >
                      {formatEnumLabel(edge.kind)} violation (n={edge.count} ·{' '}
                      {edge.p95_ms}ms)
                    </text>
                  </g>
                );
              })}

              {/* Step Nodes */}
              {journey.steps.map((step) => {
                const pos = stepPositions[step.id];
                return (
                  <g key={step.id}>
                    <title>{`${step.label} — ${step.service} (${step.operation})`}</title>
                    <rect
                      x={pos.x}
                      y={pos.y}
                      width={NODE_WIDTH}
                      height="84"
                      rx="6"
                      fill="#FFFFFF"
                      stroke="#0F172A"
                      strokeWidth="1.25"
                    />
                    <rect
                      x={pos.x}
                      y={pos.y}
                      width={NODE_WIDTH}
                      height="4"
                      rx="2"
                      fill="#1A73E8"
                    />
                    <text
                      x={pos.x + 10}
                      y={pos.y + 20}
                      fill="#0F172A"
                      fontSize="11"
                      fontWeight="600"
                    >
                      {step.label}
                    </text>
                    <text
                      x={pos.x + 10}
                      y={pos.y + 38}
                      fill="#1A73E8"
                      fontSize="10"
                      fontFamily="monospace"
                    >
                      {step.service}
                    </text>
                    <text
                      x={pos.x + 10}
                      y={pos.y + 54}
                      fill="#64748B"
                      fontSize="9"
                      fontFamily="monospace"
                    >
                      {truncateRoute(step.operation, 34)}
                    </text>
                    <text
                      x={pos.x + 10}
                      y={pos.y + 72}
                      fill="#334155"
                      fontSize="10"
                      fontWeight="600"
                    >
                      Conv: {step.completion_rate_pct}% · P95: {step.p95_dwell_ms}ms
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>
        </div>
      </Card>

      {/* Funnel KPIs + Change-Point KPI Rollups */}
      <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-12">
        <Card className="p-4 xl:col-span-5">
          <SectionHeader
            title="Funnel Conversion & Drop-Off Value Exposure"
            subtitle="Step-by-step cohort progression and value at risk"
          />
          <div className="mt-3 overflow-x-auto">
            <table className="w-full border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50 text-slate-600">
                  <th className="px-3 py-2 font-semibold">Workflow Stage</th>
                  <th className="px-3 py-2 text-right font-semibold">
                    Entered / Done
                  </th>
                  <th className="px-3 py-2 text-right font-semibold">Conv %</th>
                  <th className="px-3 py-2 text-right font-semibold">
                    Drop-off Value Risk
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 num-tabular">
                {journey.funnel_kpis.map((stage) => (
                  <tr key={stage.step_id} className="hover:bg-slate-50">
                    <td className="px-3 py-2.5 font-semibold text-slate-900">
                      {stage.step_name}
                    </td>
                    <td className="px-3 py-2.5 text-right text-slate-700">
                      {stage.entered} → {stage.completed}
                    </td>
                    <td className="px-3 py-2.5 text-right font-semibold text-slate-900">
                      {stage.conversion_pct.toFixed(1)}%
                    </td>
                    <td className="px-3 py-2.5 text-right font-semibold text-slate-900">
                      {formatCurrency(stage.dropoff_value)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card className="p-4 xl:col-span-7">
          <SectionHeader
            title="Business-KPI Change-Point Detection (kpi_rollups)"
            subtitle="0-token CUSUM and z-score tests against 7-day same-hour-of-day baselines"
          />
          <div className="mt-3 overflow-x-auto">
            <table className="w-full border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50 text-slate-600">
                  <th className="px-3 py-2 font-semibold">KPI Metric</th>
                  <th className="px-3 py-2 text-right font-semibold">
                    Observed Value
                  </th>
                  <th className="px-3 py-2 text-right font-semibold">
                    Same-Hour Baseline
                  </th>
                  <th className="px-3 py-2 text-right font-semibold">
                    Z-Score / CUSUM
                  </th>
                  <th className="px-3 py-2 font-semibold">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 num-tabular">
                {journey.kpi_rollups.map((rollup) => {
                  const fmtVal = (v: number) =>
                    rollup.unit === 'currency'
                      ? formatCurrency(v)
                      : rollup.unit === 'pct'
                      ? `${v.toFixed(1)}%`
                      : `${formatNumber(v)} ms`;
                  return (
                    <tr key={rollup.kpi} className="hover:bg-slate-50">
                      <td className="px-3 py-2.5">
                        <div className="font-semibold text-slate-900">
                          {rollup.label}
                        </div>
                        <div className="font-mono text-slate-500">
                          {rollup.kpi} (n={rollup.sample_n})
                        </div>
                      </td>
                      <td className="px-3 py-2.5 text-right font-semibold text-slate-900">
                        {fmtVal(rollup.value)}
                      </td>
                      <td className="px-3 py-2.5 text-right text-slate-600">
                        {fmtVal(rollup.baseline_value)}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-slate-800">
                        z={rollup.zscore.toFixed(2)} · c={rollup.cusum.toFixed(1)}
                      </td>
                      <td className="px-3 py-2.5">
                        {rollup.breach ? (
                          <Badge variant="warning" dot>
                            Breach
                          </Badge>
                        ) : (
                          <Badge variant="success" dot>
                            Normal
                          </Badge>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      {/* Conformance Violations & Recent Journey Instances */}
      <Card className="p-4">
        <SectionHeader
          title="Recent Journey Traces & Conformance Verdicts"
          subtitle="Stitched by journey.id across all discovered services in BigQuery godview_ledger.journeys"
        />
        <div className="mt-3 overflow-x-auto">
          <table className="w-full border-collapse text-left text-xs">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50 text-slate-600">
                <th className="px-3 py-2 font-semibold">Journey ID</th>
                <th className="px-3 py-2 font-semibold">Conformance</th>
                <th className="px-3 py-2 font-semibold">Observed Step Sequence</th>
                <th className="px-3 py-2 font-semibold">Final Status</th>
                <th className="px-3 py-2 text-right font-semibold">
                  Principal Value
                </th>
                <th className="px-3 py-2 text-right font-semibold">Duration</th>
                <th className="px-3 py-2 font-semibold">Traffic Source</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 num-tabular">
              {journey.recent_instances.map((row) => (
                <tr key={row.journey_id} className="hover:bg-slate-50">
                  <td className="px-3 py-2.5 font-mono font-semibold text-slate-900">
                    {row.journey_id}
                  </td>
                  <td className="px-3 py-2.5">
                    <Badge variant={conformanceVariant(row.conformance)} dot>
                      {formatEnumLabel(row.conformance)}
                    </Badge>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-slate-700">
                    {row.observed_sequence}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-slate-800">
                    {row.final_status}
                  </td>
                  <td className="px-3 py-2.5 text-right font-semibold text-slate-900">
                    {formatCurrency(row.value_at_risk)}
                  </td>
                  <td className="px-3 py-2.5 text-right text-slate-700">
                    {formatNumber(row.total_duration_ms)} ms
                  </td>
                  <td className="px-3 py-2.5">
                    {row.is_synthetic ? (
                      <Badge variant="outline">X-Synthetic: true</Badge>
                    ) : (
                      <Badge variant="info">Human session</Badge>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
};
