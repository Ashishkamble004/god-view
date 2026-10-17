import React from 'react';
import type { ChangeEvent } from '../api/types';
import {
  Badge,
  Card,
  EmptyState,
  SectionHeader,
  formatEnumLabel,
  formatShortTime,
} from './ui';

export interface ChangesTabProps {
  changes: ChangeEvent[];
  onSelectIncident: (incidentId: string) => void;
}

export const ChangesTab: React.FC<ChangesTabProps> = ({
  changes,
  onSelectIncident,
}) => {
  if (changes.length === 0) {
    return (
      <EmptyState
        title="No Change Events Recorded"
        description="GitHub webhooks and Cloud Audit Log sinks populate change_events with business-language PR reviews and canary verdicts."
      />
    );
  }

  return (
    <div className="space-y-6">
      <Card className="p-5">
        <SectionHeader
          title="Change Intelligence — PR Risk Reviews & Post-Deploy Business Canaries"
          subtitle="Change Agent translates commits and pull requests into business risk scores and evaluates KPI + invariant health inside the canary window."
        />

        <div className="mt-4 space-y-4">
          {changes.map((chg) => {
            const highRisk = chg.risk_score >= 0.7;
            return (
              <div
                key={chg.id}
                className="rounded border border-slate-200 bg-white p-4 text-xs"
              >
                <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 pb-3">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-sm font-semibold text-slate-900">
                        {chg.id}
                      </span>
                      <Badge variant="outline">{formatEnumLabel(chg.kind)}</Badge>
                      {chg.pr_number && (
                        <Badge variant="info">PR #{chg.pr_number}</Badge>
                      )}
                      <span className="font-mono text-slate-500">
                        sha:{chg.sha} · by {chg.author}
                      </span>
                    </div>
                    <p className="mt-2 text-sm font-semibold text-slate-900">
                      {chg.business_summary}
                    </p>
                  </div>

                  <div className="flex items-center gap-3 num-tabular">
                    <div className="text-right">
                      <span className="text-slate-500">Business Risk Score</span>
                      <div className="mt-0.5">
                        <Badge
                          variant={
                            highRisk
                              ? 'warning'
                              : chg.risk_score >= 0.4
                              ? 'info'
                              : 'success'
                          }
                          dot
                        >
                          {(chg.risk_score * 100).toFixed(0)} / 100 (
                          {chg.risk_score.toFixed(2)})
                        </Badge>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Canary Verdict & Scope */}
                <div className="mt-3 grid grid-cols-1 gap-3 rounded border border-slate-200 bg-slate-50 p-3 md:grid-cols-3">
                  <div>
                    <span className="text-slate-500">
                      Affected Services &amp; Workflows
                    </span>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {chg.affected_services.map((s) => (
                        <Badge key={s} variant="secondary" className="font-mono">
                          {s}
                        </Badge>
                      ))}
                      {chg.affected_journeys.map((j) => (
                        <Badge key={j} variant="outline" className="font-mono">
                          {j}
                        </Badge>
                      ))}
                    </div>
                  </div>

                  <div>
                    <span className="text-slate-500">
                      Business Canary Verdict
                    </span>
                    <div className="mt-1 flex items-center gap-2">
                      <Badge
                        variant={
                          chg.canary.verdict === 'passed'
                            ? 'success'
                            : chg.canary.verdict === 'failed'
                            ? 'warning'
                            : 'info'
                        }
                        dot
                      >
                        Canary: {chg.canary.verdict}
                      </Badge>
                    </div>
                    <p className="mt-1 text-slate-700">{chg.canary.details}</p>
                  </div>

                  <div>
                    <span className="text-slate-500">
                      Linked Incidents &amp; Rollback Recipe
                    </span>
                    <div className="mt-1 flex flex-wrap items-center gap-2">
                      {chg.linked_incident_ids.length === 0 ? (
                        <span className="text-slate-500">
                          0 linked incidents
                        </span>
                      ) : (
                        chg.linked_incident_ids.map((incId) => (
                          <button
                            key={incId}
                            type="button"
                            onClick={() => onSelectIncident(incId)}
                            className="rounded font-mono font-semibold text-blue-700 hover:underline focus:outline-none focus:ring-2 focus:ring-blue-600"
                          >
                            {incId}
                          </button>
                        ))
                      )}
                    </div>
                    <p className="mt-1 font-mono text-slate-500 num-tabular">
                      Occurred: {formatShortTime(chg.occurred_at)}
                    </p>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </Card>
    </div>
  );
};
