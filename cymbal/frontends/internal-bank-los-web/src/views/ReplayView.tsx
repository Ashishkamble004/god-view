import React from 'react';
import { Lock, RotateCcw } from 'lucide-react';
import type { LoanApplication } from '../api/types';
import { Badge, mapStatusToBadgeVariant } from '../components/Badge';
import { formatEnumLabel, formatInr } from '../utils/queueUtils';

export interface ReplayViewProps {
  replayQueueLoans: LoanApplication[];
}

export const ReplayView: React.FC<ReplayViewProps> = ({ replayQueueLoans }) => {
  return (
    <div className="space-y-4">
      <div className="bg-white border border-slate-200 rounded p-4 shadow-sm flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-start gap-3">
          <RotateCcw
            size={18}
            strokeWidth={1.75}
            className="text-gblue mt-0.5 shrink-0"
          />
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold text-ink-heading">
                Saga Replay Queue — Stuck &amp; Replayed CBS Disbursements
              </h2>
              <Badge variant="outline">Read-Only Inspector</Badge>
            </div>
            <p className="text-xs text-ink-muted mt-1">
              Monitors loans in <code>DISBURSEMENT_PENDING</code> or{' '}
              <code>FAILED</code> state caused by downstream CBS VM timeouts (
              <code>504 Gateway Timeout</code>). Mutating idempotent replay
              execution (
              <code>POST /admin/v1/disbursements/{'{loan_id}'}:replay</code>
              ) is strictly governed by the God-View Sovereign Control Plane via
              the <code>biz.replay_disbursement</code> capability.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 px-3 py-2 rounded bg-surface border border-slate-200 text-xs shrink-0">
          <Lock size={14} className="text-ink-muted" />
          <span className="font-mono text-ink-heading whitespace-nowrap">
            Idempotency Key: loan_id
          </span>
        </div>
      </div>

      <div className="bg-white border border-slate-200 rounded shadow-sm overflow-hidden">
        <table className="w-full text-left border-collapse text-xs">
          <thead className="bg-surface border-b border-slate-200 text-xs font-semibold uppercase text-ink-muted">
            <tr>
              <th className="py-2.5 px-3 whitespace-nowrap">
                Loan ID (Idempotency Key)
              </th>
              <th className="py-2.5 px-3 whitespace-nowrap">
                Applicant &amp; Vehicle
              </th>
              <th className="py-2.5 px-3 whitespace-nowrap">
                Sanction &amp; NACH Mandate
              </th>
              <th className="py-2.5 px-3 text-right whitespace-nowrap">
                Disbursement ₹ At Risk
              </th>
              <th className="py-2.5 px-3 text-right whitespace-nowrap">
                CBS Attempts
              </th>
              <th className="py-2.5 px-3 whitespace-nowrap">CBS Status</th>
              <th className="py-2.5 px-3 whitespace-nowrap">
                UTR / Replay Lineage
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200">
            {replayQueueLoans.map((l) => {
              const d = l.disbursement;
              const statusRaw = d?.status ?? l.status;
              return (
                <tr key={l.loan_id} className="h-10">
                  <td className="py-2 px-3 font-mono font-semibold text-ink-heading whitespace-nowrap">
                    {l.loan_id}
                  </td>
                  <td className="py-2 px-3 whitespace-nowrap">
                    <div className="font-semibold text-ink-heading">
                      {l.customer?.full_name}
                    </div>
                    <div className="text-xs text-ink-muted">
                      {l.vehicle?.make} {l.vehicle?.model}
                    </div>
                  </td>
                  <td className="py-2 px-3 font-mono whitespace-nowrap">
                    {d?.sanction_letter_no ?? 'Pending'} ·{' '}
                    {d?.nach_mandate_id ?? '—'}
                  </td>
                  <td className="py-2 px-3 text-right font-semibold tabular-nums text-ink-heading whitespace-nowrap">
                    {formatInr(d?.disbursed_amount_inr ?? l.loan_amount_inr)}
                  </td>
                  <td className="py-2 px-3 text-right font-mono tabular-nums whitespace-nowrap">
                    {d?.attempts ?? 1}
                  </td>
                  <td className="py-2 px-3 whitespace-nowrap">
                    <Badge variant={mapStatusToBadgeVariant(statusRaw)}>
                      {formatEnumLabel(statusRaw)}
                    </Badge>
                  </td>
                  <td className="py-2 px-3 font-mono text-xs whitespace-nowrap">
                    {d?.cbs_utr_number ? (
                      <span className="text-ggreen font-semibold">
                        {d.cbs_utr_number}
                        {d.replay_of ? ` (Replay of ${d.replay_of})` : ''}
                      </span>
                    ) : (
                      <span className="text-gred">
                        Awaiting Sovereign Replay (CBS 504 Timeout)
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};
