import React from 'react';
import { FileCheck2, RefreshCw } from 'lucide-react';
import type { LoanApplication } from '../api/types';
import { Badge, mapStatusToBadgeVariant } from '../components/Badge';
import { Button } from '../components/Button';
import { formatInr, maskPan } from '../utils/calculator';

export interface ApplicationsViewProps {
  loans: LoanApplication[];
  loansLoading: boolean;
  onRefresh: () => void;
  onStartApplication: () => void;
  onExploreVehicles: () => void;
}

function formatUtilityLabel(utilityType: string): string {
  if (utilityType === 'COMMERCIAL_FLEET_TOPUP') {
    return 'Commercial Fleet + Top-Up';
  }
  return 'Personal / EV Loan';
}

function formatStatusLabel(status: string): string {
  switch (status) {
    case 'DISBURSED':
      return 'Disbursed';
    case 'APPROVED':
      return 'Approved';
    case 'INSURED':
      return 'Insurance Issued';
    case 'KYC_VERIFIED':
      return 'Identity Verified';
    case 'KYC_PENDING':
      return 'Identity Pending';
    case 'DISBURSEMENT_PENDING':
      return 'Payout In Progress';
    case 'MODEL_ARMOR_BLOCKED':
      return 'Content Review Needed';
    case 'REJECTED':
      return 'Not Eligible';
    default:
      return status;
  }
}

export const ApplicationsView: React.FC<ApplicationsViewProps> = ({
  loans,
  loansLoading,
  onRefresh,
  onStartApplication,
  onExploreVehicles,
}) => {
  return (
    <div className="space-y-6">
      <div className="bg-white border border-slate-200 rounded p-5 shadow-sm flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-ink-heading">
            My Loan Applications &amp; Sanction Letters
          </h1>
          <p className="text-xs text-ink-muted mt-0.5">
            Track loan applications submitted during this session, including
            sanction references, insurance policies, and bank payout details.
          </p>
        </div>
        <Button variant="outline" size="md" onClick={onRefresh}>
          <RefreshCw size={14} />
          Refresh Status
        </Button>
      </div>

      {loansLoading ? (
        <div className="bg-white border border-slate-200 rounded p-8 text-center text-xs text-ink-muted">
          Checking your session applications...
        </div>
      ) : loans.length === 0 ? (
        <div
          data-testid="applications-empty-state"
          className="bg-white border border-slate-200 rounded p-10 shadow-sm text-center max-w-xl mx-auto my-4 space-y-3"
        >
          <div className="h-10 w-10 rounded-full bg-gblue-tint border border-gblue-border text-gblue flex items-center justify-center mx-auto">
            <FileCheck2 size={20} />
          </div>
          <h2 className="text-base font-semibold text-ink-heading">
            No loan applications in this session yet
          </h2>
          <p className="text-xs text-ink-muted max-w-md mx-auto">
            For your privacy, only loan applications submitted from your current
            browser session appear here. Configure a vehicle and submit an
            application to receive your sanction letter and bank payout
            reference.
          </p>
          <div className="flex items-center justify-center gap-3 pt-2">
            <Button variant="outline" size="md" onClick={onExploreVehicles}>
              Explore Vehicles
            </Button>
            <Button
              id="empty-start-application-btn"
              variant="primary"
              size="md"
              onClick={onStartApplication}
            >
              Start Loan Application
            </Button>
          </div>
        </div>
      ) : (
        <div className="bg-white border border-slate-200 rounded shadow-sm overflow-hidden">
          <table className="w-full text-left text-xs">
            <thead className="bg-surface border-b border-slate-200 text-ink-muted">
              <tr>
                <th className="py-2.5 px-4 font-semibold">
                  Application Reference
                </th>
                <th className="py-2.5 px-4 font-semibold">Applicant</th>
                <th className="py-2.5 px-4 font-semibold">
                  Vehicle &amp; Loan Type
                </th>
                <th className="py-2.5 px-4 font-semibold text-right">
                  Loan Amount
                </th>
                <th className="py-2.5 px-4 font-semibold text-right">
                  Monthly EMI
                </th>
                <th className="py-2.5 px-4 font-semibold">
                  Sanction &amp; Payout Reference
                </th>
                <th className="py-2.5 px-4 font-semibold">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {loans.map((loan) => (
                <tr key={loan.loan_id} className="hover:bg-surface">
                  <td className="py-2.5 px-4 font-mono text-ink-heading whitespace-nowrap">
                    <div>{loan.journey_id}</div>
                    <div className="text-xs text-ink-muted">
                      {loan.loan_id.slice(0, 18)}
                    </div>
                  </td>
                  <td className="py-2.5 px-4 whitespace-nowrap">
                    <div className="font-semibold text-ink-heading">
                      {loan.customer?.full_name || 'Applicant'}
                    </div>
                    <div className="text-xs text-ink-muted font-mono">
                      PAN {maskPan(loan.customer?.pan_number)}
                    </div>
                  </td>
                  <td className="py-2.5 px-4">
                    <div className="font-medium text-ink-heading">
                      {loan.vehicle?.model || loan.vehicle_id}
                    </div>
                    <div className="text-xs text-ink-muted">
                      {formatUtilityLabel(loan.utility_type)}
                    </div>
                  </td>
                  <td className="py-2.5 px-4 text-right font-semibold text-ink-heading tabular-nums whitespace-nowrap">
                    {formatInr(loan.loan_amount_inr)}
                  </td>
                  <td className="py-2.5 px-4 text-right font-semibold text-gblue tabular-nums whitespace-nowrap">
                    {formatInr(loan.emi_inr)}
                  </td>
                  <td className="py-2.5 px-4 font-mono text-xs whitespace-nowrap">
                    <div className="text-ink-heading">
                      {loan.disbursement?.sanction_letter_no || '—'}
                    </div>
                    <div className="text-ink-muted">
                      UTR: {loan.disbursement?.cbs_utr_number || '—'}
                    </div>
                  </td>
                  <td className="py-2.5 px-4 whitespace-nowrap">
                    <Badge variant={mapStatusToBadgeVariant(loan.status)}>
                      {formatStatusLabel(loan.status)}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
