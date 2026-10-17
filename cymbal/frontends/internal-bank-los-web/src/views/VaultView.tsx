import React from 'react';
import { CheckCircle2, ShieldAlert, ShieldCheck } from 'lucide-react';
import type { KycSignedUrlResponse, LoanApplication } from '../api/types';
import { Badge, mapStatusToBadgeVariant } from '../components/Badge';
import { formatEnumLabel } from '../utils/queueUtils';

export interface VaultViewProps {
  loans: LoanApplication[];
  selectedLoan: LoanApplication | null;
  panDoc: KycSignedUrlResponse | null;
  aadhaarDoc: KycSignedUrlResponse | null;
  vaultLoading: boolean;
  onSelectLoan: (loanId: string) => void;
}

export const VaultView: React.FC<VaultViewProps> = ({
  loans,
  selectedLoan,
  panDoc,
  aadhaarDoc,
  vaultLoading,
  onSelectLoan,
}) => {
  if (!selectedLoan) {
    return (
      <div className="bg-white border border-slate-200 rounded p-8 text-center text-xs text-ink-muted">
        Select a loan application to inspect its KYC Vault documents and Model
        Armor verdict.
      </div>
    );
  }

  const panGcsUri =
    panDoc?.gcs_uri || selectedLoan.kyc_document?.pan_gcs_uri || '—';
  const aadhaarGcsUri =
    aadhaarDoc?.gcs_uri || selectedLoan.kyc_document?.aadhaar_gcs_uri || '—';
  const imageModel =
    panDoc?.image_model || selectedLoan.kyc_document?.pan_image_model || '—';
  const templateId =
    selectedLoan.kyc_document?.model_armor_findings_json?.template_id || '—';

  return (
    <div className="space-y-5">
      {/* Text-Only Model Armor Guardrail Banner */}
      <div className="bg-gblue-tint border border-gblue-border rounded p-4 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-start gap-3">
          <ShieldCheck
            size={20}
            strokeWidth={1.75}
            className="text-gblue mt-0.5 shrink-0"
          />
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold text-ink-heading">
                Text-Only Model Armor Guardrail
              </h2>
              <Badge variant="info">Images Never Scanned</Badge>
            </div>
            <p className="text-xs text-ink-body mt-1">
              Specimen images are never scanned by Model Armor — only text
              fields (<code>loan_purpose_text</code>,{' '}
              <code>employment_remarks</code>, <code>applicant_notes</code>) are
              inspected by the active regional Model Armor template. Synthetic
              PAN and Aadhaar specimen cards are verified via deterministic
              non-issuable identifier checks.
            </p>
          </div>
        </div>

        {/* Loan Switcher for Vault Inspection */}
        <div className="flex items-center gap-2 shrink-0">
          <label
            htmlFor="vault-loan-selector"
            className="text-xs font-semibold text-ink-heading whitespace-nowrap"
          >
            Inspect Applicant:
          </label>
          <select
            id="vault-loan-selector"
            value={selectedLoan.loan_id}
            onChange={(e) => onSelectLoan(e.target.value)}
            className="px-3 py-1.5 text-xs bg-white border border-slate-300 rounded text-ink-heading font-mono focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue"
          >
            {loans.map((l) => (
              <option key={l.loan_id} value={l.loan_id}>
                {l.loan_id} — {l.customer?.full_name} (
                {formatEnumLabel(l.status)})
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Specimen Cards + Model Armor Text Scan Findings */}
      <div className="grid grid-cols-1 lg:grid-cols-12 items-start gap-5">
        {/* Left 7 cols: PAN & Aadhaar Specimen Cards */}
        <div className="lg:col-span-7 space-y-4">
          <div className="bg-white border border-slate-200 rounded p-4 shadow-sm space-y-3">
            <div className="flex items-start justify-between gap-3 border-b border-slate-200 pb-2.5">
              <div>
                <h3 className="text-sm font-semibold text-ink-heading">
                  Synthetic KYC Specimen Cards (Cloud Storage Signed URLs)
                </h3>
                <p className="text-xs text-ink-muted">
                  Watermarked synthetic specimen cards (Generator:{' '}
                  <span className="font-mono text-ink-heading">
                    {imageModel}
                  </span>
                  ) with non-issuable PAN and Aadhaar identifiers.
                </p>
              </div>
              <Badge
                variant={mapStatusToBadgeVariant(
                  selectedLoan.kyc_document?.kyc_mode ?? 'IMAGE'
                )}
                className="shrink-0 whitespace-nowrap"
              >
                Mode: {formatEnumLabel(selectedLoan.kyc_document?.kyc_mode ?? 'IMAGE')}
              </Badge>
            </div>

            {vaultLoading ? (
              <div className="py-12 text-center text-xs text-ink-muted">
                Fetching signed specimen URLs from KYC Vault...
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 items-start gap-4">
                {/* PAN Specimen Card */}
                <div className="border border-slate-200 rounded p-3 bg-surface space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-semibold text-ink-heading whitespace-nowrap">
                      PAN Specimen Card
                    </span>
                    <Badge variant="success">Verified · No Image Scan</Badge>
                  </div>
                  {panDoc ? (
                    <img
                      src={panDoc.signed_url}
                      alt={`Synthetic PAN Specimen for ${selectedLoan.customer?.full_name}`}
                      className="w-full rounded border border-slate-300 bg-white"
                    />
                  ) : null}
                  <div className="text-xs font-mono text-ink-muted space-y-0.5 break-all">
                    <div>URI: {panGcsUri}</div>
                    <div>
                      Watermark:{' '}
                      <strong className="text-gred">
                        {panDoc?.watermark_text ||
                          'SPECIMEN · NOT A GOVERNMENT DOCUMENT'}
                      </strong>
                    </div>
                  </div>
                </div>

                {/* Aadhaar Specimen Card */}
                <div className="border border-slate-200 rounded p-3 bg-surface space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-semibold text-ink-heading whitespace-nowrap">
                      Aadhaar Specimen Card
                    </span>
                    <Badge variant="success">Verified · No Image Scan</Badge>
                  </div>
                  {aadhaarDoc ? (
                    <img
                      src={aadhaarDoc.signed_url}
                      alt={`Synthetic Aadhaar Specimen for ${selectedLoan.customer?.full_name}`}
                      className="w-full rounded border border-slate-300 bg-white"
                    />
                  ) : null}
                  <div className="text-xs font-mono text-ink-muted space-y-0.5 break-all">
                    <div>URI: {aadhaarGcsUri}</div>
                    <div>
                      Watermark:{' '}
                      <strong className="text-gred">
                        {aadhaarDoc?.watermark_text ||
                          'SPECIMEN · NOT A GOVERNMENT DOCUMENT'}
                      </strong>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Right 5 cols: Text-Only Model Armor Verdict & Scanned Fields */}
        <div className="lg:col-span-5 bg-white border border-slate-200 rounded p-4 shadow-sm space-y-4">
          <div className="flex items-center justify-between border-b border-slate-200 pb-3">
            <div>
              <span className="text-xs font-semibold uppercase tracking-wider text-ink-muted">
                Utility 1 · kyc-model-armor-svc
              </span>
              <h3 className="text-sm font-semibold text-ink-heading mt-0.5">
                Model Armor Text Inspection Verdict
              </h3>
            </div>
            <Badge
              variant={mapStatusToBadgeVariant(
                selectedLoan.kyc_document?.model_armor_text_status ?? 'CLEAN'
              )}
            >
              {formatEnumLabel(
                selectedLoan.kyc_document?.model_armor_text_status ?? 'CLEAN'
              )}
            </Badge>
          </div>

          <div className="p-3 rounded bg-surface border border-slate-200 text-xs space-y-1.5">
            <div className="flex justify-between gap-3">
              <span className="text-ink-muted shrink-0">Template ID:</span>
              <span className="font-mono text-ink-heading text-right break-all">
                {templateId}
              </span>
            </div>
            <div className="flex justify-between gap-3">
              <span className="text-ink-muted">Specimen Images Scanned:</span>
              <span className="font-mono font-semibold text-ggreen whitespace-nowrap">
                false (Text Fields Only)
              </span>
            </div>
          </div>

          {/* Scanned Text Payloads */}
          <div className="space-y-2.5 text-xs">
            <h4 className="font-semibold text-ink-heading uppercase tracking-wider text-xs">
              Inspected Free-Text Fields
            </h4>

            <div className="p-2.5 rounded border border-slate-200 bg-surface">
              <span className="font-mono text-xs font-semibold text-gblue block">
                1. loan_purpose_text
              </span>
              <p className="text-ink-heading mt-1">
                {selectedLoan.loan_purpose_text}
              </p>
            </div>

            <div className="p-2.5 rounded border border-slate-200 bg-surface">
              <span className="font-mono text-xs font-semibold text-gblue block">
                2. employment_remarks
              </span>
              <p className="text-ink-heading mt-1">
                {selectedLoan.employment_remarks}
              </p>
            </div>

            <div className="p-2.5 rounded border border-slate-200 bg-surface">
              <span className="font-mono text-xs font-semibold text-gblue block">
                3. applicant_notes
              </span>
              <p className="text-ink-heading mt-1">
                {selectedLoan.applicant_notes}
              </p>
            </div>
          </div>

          {/* Findings List */}
          <div className="space-y-2">
            <h4 className="font-semibold text-ink-heading uppercase tracking-wider text-xs">
              Guardrail Findings
            </h4>
            {selectedLoan.kyc_document?.model_armor_findings_json?.findings
              ?.length ? (
              selectedLoan.kyc_document.model_armor_findings_json.findings.map(
                (finding, idx) => (
                  <div
                    key={idx}
                    className="p-3 rounded bg-gred-tint border border-gred-border text-xs space-y-1"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-semibold text-gred flex items-center gap-1.5">
                        <ShieldAlert size={14} className="text-gred shrink-0" />
                        {formatEnumLabel(finding.category)}
                      </span>
                      <Badge variant="danger">
                        {formatEnumLabel(finding.severity)}
                      </Badge>
                    </div>
                    <p className="text-ink-heading">
                      Field: <code>{finding.field}</code> —{' '}
                      {finding.description}
                    </p>
                  </div>
                )
              )
            ) : (
              <div className="p-3 rounded bg-ggreen-tint border border-ggreen-border text-xs flex items-center gap-2 text-ggreen">
                <CheckCircle2 size={15} className="text-ggreen shrink-0" />
                <span>
                  Zero prompt-injection, jailbreak, or PII leakage findings
                  across all 3 text fields.
                </span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
