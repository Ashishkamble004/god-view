import { AlertTriangle } from 'lucide-react';
import React from 'react';
import type { CostAndDriftOverview, CourtAgent } from '../api/types';
import {
  Badge,
  Button,
  Card,
  SectionHeader,
  formatEnumLabel,
  formatNumber,
  formatShortTime,
  formatUsd,
} from './ui';

export interface CostTabProps {
  cost: CostAndDriftOverview;
  court: CourtAgent[];
  onRemediateDrift: (
    driftId: string,
    mode: 'restore_attribute' | 'apply_dispatch'
  ) => Promise<void>;
  onSelectAgent: (agentId: string) => void;
  busyKey: string | null;
}

export const CostTab: React.FC<CostTabProps> = ({
  cost,
  court,
  onRemediateDrift,
  onSelectAgent,
  busyKey,
}) => {
  if (!cost) {
    return (
      <Card className="p-8 text-center">
        <AlertTriangle className="mx-auto h-8 w-8 text-amber-500" />
        <h3 className="mt-3 text-base font-semibold text-slate-900">
          Cost &amp; Drift Telemetry Not Measured Yet
        </h3>
        <p className="mt-1 text-xs text-slate-500 max-w-md mx-auto">
          The Sovereign control plane <code>/api/v1/cost</code> endpoint returned no data or is currently unreachable.
        </p>
      </Card>
    );
  }

  const totalSavingsMonthly = (cost.cost_findings ?? [])
    .filter((f) => f.status === 'open')
    .reduce((acc, f) => acc + (f.monthly_usd_estimate ?? 0), 0);

  return (
    <div className="space-y-6">
      {/* Economics & Budget KPI Strip */}
      <div className="grid grid-cols-1 items-stretch gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Card className="flex h-full flex-col justify-between p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-semibold text-slate-600">
              All-In Monthly Budget Ceiling
            </span>
            <Badge variant="success">Alerts 50 / 80 / 100%</Badge>
          </div>
          <p className="mt-2 text-2xl font-semibold text-slate-900 num-tabular">
            {formatUsd(cost.current_month_spend_usd)} /{' '}
            {formatUsd(cost.monthly_ceiling_usd)}
          </p>
          <p className="mt-1 text-xs text-slate-600 num-tabular">
            Active proj: {formatUsd(cost.projected_active_month_usd)} ·
            Hibernate floor: {formatUsd(cost.projected_hibernated_month_usd)}
          </p>
        </Card>

        <Card className="flex h-full flex-col justify-between p-4">
          <span className="text-xs font-semibold text-slate-600">
            Daily Court Token Governor Ceiling
          </span>
          <p className="mt-2 text-2xl font-semibold text-slate-900 num-tabular">
            {formatUsd(cost.daily_court_spent_usd)} /{' '}
            {formatUsd(cost.daily_court_ceiling_usd)}
          </p>
          <p className="mt-1 text-xs text-slate-600 num-tabular">
            {cost.escalation_reserve_pct}% escalation reserve · Degrades to L1 on
            exhaustion
          </p>
        </Card>

        <Card className="flex h-full flex-col justify-between p-4">
          <span className="text-xs font-semibold text-slate-600">
            Open Cost Findings Savings Potential
          </span>
          <p className="mt-2 text-2xl font-semibold text-slate-900 num-tabular">
            {formatUsd(totalSavingsMonthly)} / mo
          </p>
          <p className="mt-1 text-xs text-slate-600">
            Identified by Platform Agent during daily reflection
          </p>
        </Card>

        <Card className="flex h-full flex-col justify-between p-4">
          <span className="text-xs font-semibold text-slate-600">
            Terraform State Drift Engine (tf.drift_diff)
          </span>
          <p className="mt-2 font-mono text-xs font-semibold text-slate-900 break-all">
            {cost.tfstate_uri}
          </p>
          <p className="mt-1 text-xs text-slate-600 num-tabular">
            Last scan: {formatShortTime(cost.last_drift_scan_at)}
          </p>
        </Card>
      </div>

      {/* Cost Findings + Per-Agent Budget Governor */}
      <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-12">
        <Card className="p-4 xl:col-span-6">
          <SectionHeader
            title="Platform Agent Cost Intelligence Findings (cost_findings)"
            subtitle={cost.cost_findings_subtitle}
          />

          <div className="mt-3 space-y-3">
            {(cost.cost_findings ?? []).map((finding) => (
              <div
                key={finding.id}
                className="rounded border border-slate-200 bg-slate-50 p-3.5 text-xs"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono font-semibold text-slate-900">
                      {finding.id}
                    </span>
                    <Badge variant="outline">
                      {formatEnumLabel(finding.finding_type)}
                    </Badge>
                    <Badge
                      variant={finding.status === 'open' ? 'warning' : 'success'}
                      dot
                    >
                      {formatEnumLabel(finding.status)}
                    </Badge>
                  </div>
                  <span className="text-sm font-semibold text-slate-900 num-tabular whitespace-nowrap">
                    Est. {formatUsd(finding.monthly_usd_estimate)} / mo
                  </span>
                </div>

                <p className="mt-1.5 font-mono text-slate-700">
                  Resource: {finding.resource}
                </p>
                <p className="mt-1 font-semibold text-slate-900">
                  {finding.recommendation}
                </p>
                <p className="mt-1 text-slate-600 num-tabular">
                  Found by{' '}
                  <button
                    type="button"
                    onClick={() => onSelectAgent(finding.found_by)}
                    className="rounded font-mono font-semibold text-blue-700 hover:underline focus:outline-none focus:ring-2 focus:ring-blue-600"
                  >
                    {finding.found_by}
                  </button>{' '}
                  at {formatShortTime(finding.found_at)}
                </p>
              </div>
            ))}
          </div>
        </Card>

        <Card className="p-4 xl:col-span-6">
          <SectionHeader
            title="Per-Agent Daily Token Budget Ledger"
            subtitle={cost.tier_budgets_summary}
          />
          <div className="mt-3 overflow-x-auto">
            <table className="w-full border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50 text-slate-600">
                  <th className="px-3 py-2 font-semibold whitespace-nowrap">
                    Agent ID
                  </th>
                  <th className="px-3 py-2 text-right font-semibold whitespace-nowrap">
                    Tokens Today
                  </th>
                  <th className="px-3 py-2 text-right font-semibold whitespace-nowrap">
                    USD / Budget
                  </th>
                  <th className="px-3 py-2 font-semibold whitespace-nowrap">
                    Governor
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 num-tabular">
                {(court || []).map((a) => (
                  <tr key={a.id} className="hover:bg-slate-50">
                    <td className="px-3 py-2 font-mono font-semibold text-slate-900 whitespace-nowrap">
                      {a.id}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-slate-700 whitespace-nowrap">
                      {formatNumber(a.tokens_spent_today)}
                    </td>
                    <td className="px-3 py-2 text-right font-semibold text-slate-900 whitespace-nowrap">
                      {formatUsd(a.tokens_spent_today_usd)} /{' '}
                      {formatUsd(a.token_budget_daily_usd)}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {a.budget_exhausted ? (
                        <Badge variant="warning">Degraded L1</Badge>
                      ) : (
                        <Badge variant="success">Active</Badge>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      {/* Terraform Drift Diff Panel */}
      <Card className="p-4">
        <SectionHeader
          title="Terraform State vs Live API Drift Diff (tf.drift_diff)"
          subtitle="Custom adapter comparing gs://...-tfstate attributes against live Cloud Run v2, GKE, Compute & Cloud SQL APIs (not terraform-mcp)"
        />
        <div className="mt-3 overflow-x-auto">
          <table className="w-full border-collapse text-left text-xs">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50 text-slate-600">
                <th className="px-3 py-2 font-semibold">Terraform Address</th>
                <th className="px-3 py-2 font-semibold">Attribute</th>
                <th className="px-3 py-2 font-semibold whitespace-nowrap">
                  Expected (tfstate)
                </th>
                <th className="px-3 py-2 font-semibold whitespace-nowrap">
                  Live Value
                </th>
                <th className="px-3 py-2 font-semibold">Status</th>
                <th className="px-3 py-2 text-right font-semibold whitespace-nowrap">
                  Remediation Capabilities
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 num-tabular">
              {(cost.drift_diffs ?? []).map((item) => (
                <tr key={item.id} className="hover:bg-slate-50">
                  <td className="px-3 py-2.5">
                    <div className="font-mono font-semibold text-slate-900">
                      {item.resource_address}
                    </div>
                    <div className="font-mono text-slate-500">
                      {item.service_name} ({item.kind})
                    </div>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-slate-800 whitespace-nowrap">
                    {item.attribute}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-slate-700 whitespace-nowrap">
                    {item.expected_tfstate}
                  </td>
                  <td className="px-3 py-2.5 whitespace-nowrap">
                    {item.status === 'drifted' ? (
                      <span className="rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 font-mono font-semibold text-amber-900">
                        {item.live_value}
                      </span>
                    ) : (
                      <span className="font-mono font-semibold text-slate-900">
                        {item.live_value}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 whitespace-nowrap">
                    <Badge
                      variant={
                        item.status === 'restored'
                          ? 'success'
                          : item.status === 'reconciling'
                          ? 'info'
                          : 'warning'
                      }
                      dot
                    >
                      {formatEnumLabel(item.status)}
                    </Badge>
                  </td>
                  <td className="px-3 py-2.5 text-right whitespace-nowrap">
                    {item.status === 'drifted' ? (
                      <div className="inline-flex items-center gap-1.5 whitespace-nowrap">
                        <Button
                          variant="primary"
                          size="sm"
                          onClick={() =>
                            onRemediateDrift(item.id, 'restore_attribute')
                          }
                          disabled={
                            busyKey === `drift:${item.id}:restore_attribute`
                          }
                          className="whitespace-nowrap"
                        >
                          Fast Restore (L3)
                        </Button>
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() =>
                            onRemediateDrift(item.id, 'apply_dispatch')
                          }
                          disabled={
                            busyKey === `drift:${item.id}:apply_dispatch`
                          }
                          className="whitespace-nowrap"
                        >
                          IaC Reconcile (L2)
                        </Button>
                      </div>
                    ) : (
                      <span className="font-mono text-slate-500">
                        {item.fast_restore_capability}
                      </span>
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
