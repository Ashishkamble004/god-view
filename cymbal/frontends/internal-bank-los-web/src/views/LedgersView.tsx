import React from 'react';
import { Building2, Database, FileSearch } from 'lucide-react';
import type { LoanApplication } from '../api/types';
import { Badge, mapStatusToBadgeVariant } from '../components/Badge';
import { Button } from '../components/Button';
import { formatEnumLabel, formatInr } from '../utils/queueUtils';

export type LedgerSection =
  | 'ALL'
  | 'UNDERWRITING'
  | 'FLEET'
  | 'INSURANCE'
  | 'DISBURSEMENTS';

const LEDGER_SECTION_LABELS: Record<LedgerSection, string> = {
  ALL: 'All Ledgers',
  UNDERWRITING: 'Underwriting',
  FLEET: 'Fleet Credit',
  INSURANCE: 'Motor Insurance',
  DISBURSEMENTS: 'CBS Disbursements',
};

export interface LedgersViewProps {
  loans: LoanApplication[];
  activeLedgerSection: LedgerSection;
  onSelectSection: (sec: LedgerSection) => void;
}

export const LedgersView: React.FC<LedgersViewProps> = ({
  loans,
  activeLedgerSection,
  onSelectSection,
}) => {
  return (
    <div className="space-y-5">
      <div className="bg-white border border-slate-200 rounded p-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-ink-heading">
            Multi-Utility Cloud SQL (godview_lms) Operational Ledgers
          </h2>
          <p className="text-xs text-ink-muted">
            Direct read-only projection of <code>underwriting_audits</code>,{' '}
            <code>commercial_credit_lines</code>,{' '}
            <code>insurance_policies</code>, and <code>disbursements</code>.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {(
            [
              'ALL',
              'UNDERWRITING',
              'FLEET',
              'INSURANCE',
              'DISBURSEMENTS',
            ] as const
          ).map((sec) => (
            <Button
              key={sec}
              variant={activeLedgerSection === sec ? 'primary' : 'secondary'}
              size="sm"
              onClick={() => onSelectSection(sec)}
            >
              {LEDGER_SECTION_LABELS[sec]}
            </Button>
          ))}
        </div>
      </div>

      {/* Utility 2: Underwriting Audits Ledger */}
      {activeLedgerSection === 'ALL' ||
      activeLedgerSection === 'UNDERWRITING' ? (
        <div className="bg-white border border-slate-200 rounded shadow-sm overflow-hidden">
          <div className="p-3.5 border-b border-slate-200 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Database size={15} className="text-gblue shrink-0" />
              <h3 className="text-xs font-semibold uppercase tracking-wider text-ink-heading">
                Utility 2 · Retail Auto Underwriting Audits (underwriting_audits)
              </h3>
            </div>
            <Badge variant="info">Policy: CIBIL ≥ 680 ∧ FOIR ≤ 60.0%</Badge>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs">
              <thead className="bg-surface border-b border-slate-200 text-xs font-semibold uppercase text-ink-muted">
                <tr>
                  <th className="py-2 px-3 whitespace-nowrap">
                    Audit ID / Loan ID
                  </th>
                  <th className="py-2 px-3 whitespace-nowrap">Applicant</th>
                  <th className="py-2 px-3 text-right whitespace-nowrap">
                    CIBIL Score
                  </th>
                  <th className="py-2 px-3 text-right whitespace-nowrap">
                    FOIR %
                  </th>
                  <th className="py-2 px-3 text-right whitespace-nowrap">
                    LTV %
                  </th>
                  <th className="py-2 px-3 whitespace-nowrap">
                    Rules Version &amp; Compute
                  </th>
                  <th className="py-2 px-3 whitespace-nowrap">
                    Decision &amp; Reason
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {loans
                  .filter((l) => l.underwriting_audit)
                  .map((l) => {
                    const aud = l.underwriting_audit!;
                    return (
                      <tr key={aud.audit_id} className="h-10">
                        <td className="py-2 px-3 font-mono whitespace-nowrap">
                          <span className="font-semibold text-ink-heading">
                            {aud.audit_id}
                          </span>{' '}
                          · {l.loan_id}
                        </td>
                        <td className="py-2 px-3 font-medium text-ink-heading whitespace-nowrap">
                          {l.customer?.full_name}
                        </td>
                        <td className="py-2 px-3 text-right tabular-nums whitespace-nowrap">
                          <div className="font-mono font-semibold text-ink-heading">
                            {aud.cibil_score}
                          </div>
                          <div className="font-mono text-xs text-ink-muted">
                            {aud.cibil_bureau_ref}
                          </div>
                        </td>
                        <td className="py-2 px-3 text-right font-mono tabular-nums whitespace-nowrap">
                          {aud.foir_pct.toFixed(1)}%
                        </td>
                        <td className="py-2 px-3 text-right font-mono tabular-nums whitespace-nowrap">
                          {aud.ltv_pct.toFixed(1)}%
                        </td>
                        <td className="py-2 px-3 font-mono text-xs text-ink-muted whitespace-nowrap">
                          {aud.rules_version} · {aud.gke_pod_name}
                        </td>
                        <td className="py-2 px-3">
                          <div className="flex items-center gap-2 flex-wrap">
                            <Badge
                              variant={mapStatusToBadgeVariant(aud.decision)}
                            >
                              {formatEnumLabel(aud.decision)}
                            </Badge>
                            {aud.rejection_reason ? (
                              <span className="text-xs text-gred font-medium">
                                {formatEnumLabel(aud.rejection_reason)}
                              </span>
                            ) : null}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {/* Utility 3: Commercial Fleet Credit Lines */}
      {activeLedgerSection === 'ALL' || activeLedgerSection === 'FLEET' ? (
        <div className="bg-white border border-slate-200 rounded shadow-sm overflow-hidden">
          <div className="p-3.5 border-b border-slate-200 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Building2 size={15} className="text-gblue shrink-0" />
              <h3 className="text-xs font-semibold uppercase tracking-wider text-ink-heading">
                Utility 3 · Commercial Fleet Credit Lines
                (commercial_credit_lines)
              </h3>
            </div>
            <Badge variant="outline">
              Cloud Run · commercial-fleet-credit-svc
            </Badge>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs">
              <thead className="bg-surface border-b border-slate-200 text-xs font-semibold uppercase text-ink-muted">
                <tr>
                  <th className="py-2 px-3 whitespace-nowrap">
                    Credit Line ID
                  </th>
                  <th className="py-2 px-3 whitespace-nowrap">
                    Operator &amp; Loan ID
                  </th>
                  <th className="py-2 px-3 whitespace-nowrap">
                    Category &amp; Fleet Size
                  </th>
                  <th className="py-2 px-3 text-right whitespace-nowrap">
                    Sanctioned Limit ₹
                  </th>
                  <th className="py-2 px-3 text-right whitespace-nowrap">
                    Utilisation %
                  </th>
                  <th className="py-2 px-3 whitespace-nowrap">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {loans
                  .filter((l) => l.commercial_credit_line)
                  .map((l) => {
                    const cl = l.commercial_credit_line!;
                    return (
                      <tr key={cl.credit_line_id} className="h-10">
                        <td className="py-2 px-3 font-mono font-semibold text-ink-heading whitespace-nowrap">
                          {cl.credit_line_id}
                        </td>
                        <td className="py-2 px-3 whitespace-nowrap">
                          <span className="font-medium text-ink-heading">
                            {l.customer?.full_name}
                          </span>{' '}
                          ·{' '}
                          <span className="font-mono text-ink-muted">
                            {l.loan_id}
                          </span>
                        </td>
                        <td className="py-2 px-3 whitespace-nowrap">
                          <Badge
                            variant={mapStatusToBadgeVariant(
                              cl.vehicle_category
                            )}
                          >
                            {formatEnumLabel(cl.vehicle_category)}
                          </Badge>{' '}
                          <span className="ml-1 tabular-nums">
                            ({cl.fleet_size} units)
                          </span>
                        </td>
                        <td className="py-2 px-3 text-right font-semibold tabular-nums text-ink-heading whitespace-nowrap">
                          {formatInr(cl.sanctioned_limit_inr)}
                        </td>
                        <td className="py-2 px-3 text-right font-mono tabular-nums whitespace-nowrap">
                          {cl.utilisation_pct.toFixed(1)}%
                        </td>
                        <td className="py-2 px-3 whitespace-nowrap">
                          <Badge variant={mapStatusToBadgeVariant(cl.status)}>
                            {formatEnumLabel(cl.status)}
                          </Badge>
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {/* Utility 4: Motor Insurance & FASTag Ledger */}
      {activeLedgerSection === 'ALL' || activeLedgerSection === 'INSURANCE' ? (
        <div className="bg-white border border-slate-200 rounded shadow-sm overflow-hidden">
          <div className="p-3.5 border-b border-slate-200 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <FileSearch size={15} className="text-gblue shrink-0" />
              <h3 className="text-xs font-semibold uppercase tracking-wider text-ink-heading">
                Utility 4 · Motor Insurance &amp; FASTag Policies
                (insurance_policies)
              </h3>
            </div>
            <Badge variant="success">
              Guard: Loan Status in Approved / Disbursed
            </Badge>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs">
              <thead className="bg-surface border-b border-slate-200 text-xs font-semibold uppercase text-ink-muted">
                <tr>
                  <th className="py-2 px-3 whitespace-nowrap">
                    Policy ID / FASTag
                  </th>
                  <th className="py-2 px-3 whitespace-nowrap">
                    Loan ID &amp; Applicant
                  </th>
                  <th className="py-2 px-3 whitespace-nowrap">Policy Type</th>
                  <th className="py-2 px-3 text-right whitespace-nowrap">
                    Premium ₹
                  </th>
                  <th className="py-2 px-3 text-right whitespace-nowrap">
                    IDV Coverage ₹
                  </th>
                  <th className="py-2 px-3 whitespace-nowrap">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {loans
                  .filter((l) => l.insurance_policy)
                  .map((l) => {
                    const pol = l.insurance_policy!;
                    return (
                      <tr key={pol.policy_id} className="h-10">
                        <td className="py-2 px-3 font-mono whitespace-nowrap">
                          <span className="font-semibold text-ink-heading">
                            {pol.policy_id}
                          </span>{' '}
                          · {pol.fasttag_id}
                        </td>
                        <td className="py-2 px-3 whitespace-nowrap">
                          <span className="font-mono">{l.loan_id}</span> ·{' '}
                          <span className="font-medium text-ink-heading">
                            {l.customer?.full_name}
                          </span>
                        </td>
                        <td className="py-2 px-3 text-xs text-ink-heading whitespace-nowrap">
                          {formatEnumLabel(pol.policy_type)}
                        </td>
                        <td className="py-2 px-3 text-right tabular-nums whitespace-nowrap">
                          {formatInr(pol.premium_inr)}
                        </td>
                        <td className="py-2 px-3 text-right font-semibold tabular-nums text-ink-heading whitespace-nowrap">
                          {formatInr(pol.coverage_inr)}
                        </td>
                        <td className="py-2 px-3 whitespace-nowrap">
                          <Badge variant={mapStatusToBadgeVariant(pol.status)}>
                            {formatEnumLabel(pol.status)}
                          </Badge>
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {/* Utility 5: Core Banking CBS Disbursements */}
      {activeLedgerSection === 'ALL' ||
      activeLedgerSection === 'DISBURSEMENTS' ? (
        <div className="bg-white border border-slate-200 rounded shadow-sm overflow-hidden">
          <div className="p-3.5 border-b border-slate-200 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Database size={15} className="text-gblue shrink-0" />
              <h3 className="text-xs font-semibold uppercase tracking-wider text-ink-heading">
                Utility 5 · Core Banking CBS Disbursements (disbursements)
              </h3>
            </div>
            <Badge variant="outline">GCE VM · core-banking-cibil-vm:8085</Badge>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs">
              <thead className="bg-surface border-b border-slate-200 text-xs font-semibold uppercase text-ink-muted">
                <tr>
                  <th className="py-2 px-3 whitespace-nowrap">
                    Disbursement / Sanction
                  </th>
                  <th className="py-2 px-3 whitespace-nowrap">
                    Loan ID &amp; Dealer Account
                  </th>
                  <th className="py-2 px-3 whitespace-nowrap">
                    CBS UTR &amp; NACH Mandate
                  </th>
                  <th className="py-2 px-3 text-right whitespace-nowrap">
                    Amount ₹
                  </th>
                  <th className="py-2 px-3 text-right whitespace-nowrap">
                    Attempts
                  </th>
                  <th className="py-2 px-3 whitespace-nowrap">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {loans
                  .filter((l) => l.disbursement)
                  .map((l) => {
                    const d = l.disbursement!;
                    return (
                      <tr key={d.disbursement_id} className="h-10">
                        <td className="py-2 px-3 font-mono whitespace-nowrap">
                          <span className="font-semibold text-ink-heading">
                            {d.disbursement_id}
                          </span>{' '}
                          · {d.sanction_letter_no}
                        </td>
                        <td className="py-2 px-3 font-mono whitespace-nowrap">
                          {l.loan_id} · {d.dealer_account_no}
                        </td>
                        <td className="py-2 px-3 font-mono whitespace-nowrap">
                          {d.cbs_utr_number ?? 'Awaiting UTR'} ·{' '}
                          {d.nach_mandate_id}
                        </td>
                        <td className="py-2 px-3 text-right font-semibold tabular-nums text-ink-heading whitespace-nowrap">
                          {formatInr(d.disbursed_amount_inr)}
                        </td>
                        <td className="py-2 px-3 text-right font-mono tabular-nums whitespace-nowrap">
                          {d.attempts}
                        </td>
                        <td className="py-2 px-3 whitespace-nowrap">
                          <Badge variant={mapStatusToBadgeVariant(d.status)}>
                            {formatEnumLabel(d.status)}
                          </Badge>
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
};
