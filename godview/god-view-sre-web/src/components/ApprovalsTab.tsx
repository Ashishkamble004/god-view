import { AlertTriangle, Check, Clock, X } from 'lucide-react';
import React, { useState } from 'react';
import type { ApprovalCard, ApprovalDecisionStatus } from '../api/types';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  TrustLevelBadge,
  formatEnumLabel,
  formatExpiryCountdownIst,
  formatCurrency,
  formatShortTime,
} from './ui';

export interface ApprovalsTabProps {
  approvals: ApprovalCard[];
  currentUserEmail: string;
  approvalWindowLabel: string;
  referenceNowIso?: string;
  onDecideApproval: (
    approvalId: string,
    action: 'approve' | 'reject' | 'snooze'
  ) => Promise<void>;
  onSelectIncident: (incidentId: string) => void;
  onSelectAgent: (agentId: string) => void;
  busyKey: string | null;
}

export const ApprovalsTab: React.FC<ApprovalsTabProps> = ({
  approvals,
  currentUserEmail,
  approvalWindowLabel,
  referenceNowIso,
  onDecideApproval,
  onSelectIncident,
  onSelectAgent,
  busyKey,
}) => {
  const [filter, setFilter] = useState<'all' | ApprovalDecisionStatus>('all');

  if (!approvals) {
    return (
      <Card className="p-8 text-center">
        <AlertTriangle className="mx-auto h-8 w-8 text-amber-500" />
        <h3 className="mt-3 text-base font-semibold text-slate-900">
          Approvals Telemetry Not Measured Yet
        </h3>
        <p className="mt-1 text-xs text-slate-500 max-w-md mx-auto">
          The Sovereign control plane <code>/api/v1/approvals</code> endpoint returned no data or is currently unreachable.
        </p>
      </Card>
    );
  }

  const filtered =
    filter === 'all'
      ? approvals
      : approvals.filter((a) => a.decision === filter);

  const pendingCount = approvals.filter((a) => a.decision === 'pending').length;
  const pendingValue = approvals
    .filter((a) => a.decision === 'pending')
    .reduce((acc, a) => acc + (a.value_impact || 0), 0);

  const countFor = (st: 'all' | ApprovalDecisionStatus): number =>
    st === 'all'
      ? approvals.length
      : approvals.filter((a) => a.decision === st).length;

  return (
    <div className="space-y-6">
      {/* Top Summary Strip */}
      <div className="grid grid-cols-1 items-stretch gap-4 sm:grid-cols-3">
        <Card className="flex h-full flex-col justify-between p-4">
          <span className="text-xs font-semibold text-slate-600">
            Pending Human-in-the-Loop Approvals
          </span>
          <p className="mt-2 text-2xl font-semibold text-slate-900 num-tabular">
            {pendingCount} Pending
          </p>
          <p className="mt-1 text-xs text-slate-600">
            Mirrored across Cockpit IAP Inbox and Google Chat ({approvalWindowLabel})
          </p>
        </Card>

        <Card className="flex h-full flex-col justify-between p-4">
          <span className="text-xs font-semibold text-slate-600">
            Total Value Gated by Pending Approvals
          </span>
          <p className="mt-2 text-2xl font-semibold text-slate-900 num-tabular">
            {formatCurrency(pendingValue)}
          </p>
          <p className="mt-1 text-xs text-slate-600">
            Blast radius = 1 resource/cohort per action; reversal recipe mandatory
          </p>
        </Card>

        <Card className="flex h-full flex-col justify-between p-4">
          <span className="text-xs font-semibold text-slate-600">
            Approver Identity (IAP Header)
          </span>
          <p className="mt-2 font-mono text-sm font-semibold text-slate-900">
            {currentUserEmail}
          </p>
          <p className="mt-1 text-xs text-slate-600">
            Recorded as approver.principal in immutable audit records
          </p>
        </Card>
      </div>

      {/* Filter Pills with item counts */}
      <div className="flex flex-wrap items-center gap-1.5">
        {(
          ['all', 'pending', 'approved', 'rejected', 'snoozed', 'expired'] as const
        ).map((st) => (
          <button
            key={st}
            type="button"
            onClick={() => setFilter(st)}
            className={`rounded border px-3 py-1.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-blue-600 focus:ring-offset-1 num-tabular ${
              filter === st
                ? 'border-blue-600 bg-blue-50 text-blue-800'
                : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
            }`}
          >
            {formatEnumLabel(st)} ({countFor(st)})
          </button>
        ))}
      </div>

      {/* Approval Cards List */}
      {filtered.length === 0 ? (
        <EmptyState
          title="No Approval Cards Matching Filter"
          description="All L2/L4 actions in this filter state have been processed."
        />
      ) : (
        <div className="space-y-4">
          {filtered.map((card) => {
            const isPending = card.decision === 'pending';
            return (
              <Card
                key={card.approval_id}
                testId={`approval-card-${card.approval_id}`}
                className="p-5"
              >
                <div className="flex flex-wrap items-start justify-between gap-4 border-b border-slate-200 pb-3">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-sm font-semibold text-slate-900">
                        {card.approval_id}
                      </span>
                      <Badge
                        variant={
                          card.decision === 'pending'
                            ? 'warning'
                            : card.decision === 'approved'
                            ? 'success'
                            : card.decision === 'rejected'
                            ? 'danger'
                            : 'secondary'
                        }
                        dot
                      >
                        {formatEnumLabel(card.decision)}
                      </Badge>
                      {isPending ? (
                        <Badge variant="outline">
                          Requires owner approval (L{card.trust_level_required} gate)
                        </Badge>
                      ) : (
                        <TrustLevelBadge level={card.trust_level_required} />
                      )}
                      <Badge variant="outline" className="font-mono">
                        {card.capability}
                      </Badge>
                      <Badge variant="muted">Channel: {card.channel}</Badge>
                    </div>
                    <p className="mt-2 text-sm font-semibold text-slate-900">
                      {card.narrative}
                    </p>
                  </div>

                  <div className="text-right num-tabular">
                    <span className="text-xs font-semibold text-slate-600">
                      Value at Risk
                    </span>
                    <p className="text-xl font-semibold text-slate-900">
                      {formatCurrency(card.value_impact || 0)}
                    </p>
                    <span className="font-mono text-xs text-slate-500">
                      Evidence: {card.evidence_pack_id}
                    </span>
                  </div>
                </div>

                {/* Details Grid */}
                <div className="mt-3 grid grid-cols-1 gap-3 rounded border border-slate-200 bg-slate-50 p-3 text-xs md:grid-cols-3">
                  <div>
                    <span className="text-slate-500">Target Resource Scope</span>
                    <p className="mt-0.5 font-mono font-semibold text-slate-900">
                      {typeof card.target_resource === 'string'
                        ? card.target_resource
                        : JSON.stringify(card.target_resource ?? 'estate-resource')}
                    </p>
                  </div>
                  <div>
                    <span className="text-slate-500">
                      Requesting Agent &amp; Incident
                    </span>
                    <p className="mt-0.5 flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={() => onSelectAgent(card.agent_id)}
                        className="rounded font-mono font-semibold text-blue-700 hover:underline focus:outline-none focus:ring-2 focus:ring-blue-600"
                      >
                        {card.agent_id}
                      </button>
                      <span>·</span>
                      <button
                        type="button"
                        onClick={() => onSelectIncident(card.incident_id)}
                        className="rounded font-mono font-semibold text-blue-700 hover:underline focus:outline-none focus:ring-2 focus:ring-blue-600"
                      >
                        {card.incident_id}
                      </button>
                    </p>
                  </div>
                  <div>
                    <span className="text-slate-500">
                      Mandatory Reversal Recipe
                    </span>
                    <p className="mt-0.5 font-mono text-slate-800 break-all">
                      {card.reversal_recipe?.capability ?? 'cloudrun.rollback_revision'}:{' '}
                      {typeof card.reversal_recipe?.args === 'string'
                        ? card.reversal_recipe.args
                        : card.reversal_recipe?.args
                        ? JSON.stringify(card.reversal_recipe.args)
                        : '--to-previous-healthy'}
                    </p>
                  </div>
                </div>

                {/* Actions / Decision Footer */}
                <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                  <div className="text-xs text-slate-600 num-tabular">
                    Requested: {formatShortTime(card.requested_at)} ·{' '}
                    <span className="font-semibold text-slate-800">
                      {formatExpiryCountdownIst(card.expires_at, referenceNowIso)}
                    </span>
                    {card.decided_by && (
                      <span className="ml-2 font-semibold text-slate-800">
                        · Decided by {card.decided_by} at{' '}
                        {formatShortTime(card.decided_at)}
                      </span>
                    )}
                    {card.audit_id && (
                      <span className="ml-2 font-mono font-semibold text-green-800">
                        · Audit ID: {card.audit_id}
                      </span>
                    )}
                  </div>

                  {isPending && (
                    <div className="flex items-center gap-2">
                      <Button
                        variant="primary"
                        size="sm"
                        onClick={() =>
                          onDecideApproval(card.approval_id, 'approve')
                        }
                        disabled={busyKey === `${card.approval_id}:approve`}
                      >
                        <Check className="h-3.5 w-3.5" />
                        Approve &amp; Execute
                      </Button>
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() =>
                          onDecideApproval(card.approval_id, 'snooze')
                        }
                        disabled={busyKey === `${card.approval_id}:snooze`}
                      >
                        <Clock className="h-3.5 w-3.5" />
                        Snooze (30m)
                      </Button>
                      <Button
                        variant="danger"
                        size="sm"
                        onClick={() =>
                          onDecideApproval(card.approval_id, 'reject')
                        }
                        disabled={busyKey === `${card.approval_id}:reject`}
                      >
                        <X className="h-3.5 w-3.5" />
                        Reject
                      </Button>
                    </div>
                  )}
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
};
