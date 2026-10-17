import React, { useState } from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Car,
  CheckCircle2,
  FileText,
  Info,
  Radio,
  ShieldCheck,
  Wallet,
} from 'lucide-react';
import type {
  EmploymentType,
  KycMode,
  LoanApplication,
  TelemetryStreamEvent,
  UtilityType,
  Vehicle,
} from '../api/types';
import { Badge, mapStatusToBadgeVariant } from '../components/Badge';
import { Button } from '../components/Button';
import {
  stepStatusLabel,
  type JourneyWizardState,
  type TelemetryState,
} from '../state/journeyState';
import {
  formatInr,
  maskPan,
  validateAadhaarNumber,
  validatePanNumber,
  type FinanceMetrics,
} from '../utils/calculator';

export interface SubmitErrorState {
  title: string;
  message: string;
  code: string;
  isPaused: boolean;
  isBlocked: boolean;
  isRetryable: boolean;
}

export interface JourneyViewProps {
  wizardState: JourneyWizardState;
  telemetry: TelemetryState;
  selectedVehicle: Vehicle;
  financeMetrics: FinanceMetrics;
  downPaymentInr: number;
  tenureMonths: number;
  interestRatePct: number;
  monthlyIncomeInr: number;
  existingObligationsInr: number;
  fullName: string;
  city: string;
  employmentType: EmploymentType;
  employerName: string;
  panNumber: string;
  aadhaarNumber: string;
  kycMode: KycMode;
  fleetSize: number;
  requestedLimitInr: number;
  loanPurposeText: string;
  employmentRemarks: string;
  applicantNotes: string;
  submitting: boolean;
  submitError: SubmitErrorState | null;
  fieldErrors: Record<string, string>;
  submittedLoan: LoanApplication | null;
  onSelectStep: (idx: number) => void;
  onUtilityTypeChange: (utilityType: UtilityType) => void;
  onFullNameChange: (v: string) => void;
  onCityChange: (v: string) => void;
  onEmploymentTypeChange: (v: EmploymentType) => void;
  onEmployerNameChange: (v: string) => void;
  onPanNumberChange: (v: string) => void;
  onAadhaarNumberChange: (v: string) => void;
  onKycModeChange: (v: KycMode) => void;
  onFleetSizeChange: (v: number) => void;
  onRequestedLimitChange: (v: number) => void;
  onLoanPurposeChange: (v: string) => void;
  onEmploymentRemarksChange: (v: string) => void;
  onApplicantNotesChange: (v: string) => void;
  onUseSampleData: () => void;
  onSubmit: (e?: React.FormEvent) => void;
  onOpenCalculator: () => void;
}

function formatTelemetryEventTitle(ev: TelemetryStreamEvent): string {
  switch (ev.event_type) {
    case 'kyc.verified':
      return 'Identity Check Completed';
    case 'armor.blocked':
      return 'Application Content Flagged';
    case 'loan.decided':
      return ev.decision === 'REJECTED'
        ? 'Eligibility Check Declined'
        : 'Loan Eligibility Approved';
    case 'credit_line.decided':
      return 'Fleet Top-Up Credit Line Approved';
    case 'insurance.issued':
      return 'Motor Insurance & FASTag Issued';
    case 'disbursement.settled':
      return 'Bank Payout Completed';
    default:
      return 'Application Step Updated';
  }
}

function formatLoanStatusCustomer(status: string): string {
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
      return 'Identity Check Pending';
    case 'DISBURSEMENT_PENDING':
      return 'Payout In Progress';
    case 'MODEL_ARMOR_BLOCKED':
      return 'Content Review Required';
    case 'REJECTED':
      return 'Not Eligible';
    case 'DRAFT':
      return 'Not Started';
    default:
      return status;
  }
}

export const JourneyView: React.FC<JourneyViewProps> = ({
  wizardState,
  telemetry,
  selectedVehicle,
  financeMetrics,
  downPaymentInr,
  tenureMonths,
  interestRatePct,
  monthlyIncomeInr,
  existingObligationsInr,
  fullName,
  city,
  employmentType,
  employerName,
  panNumber,
  aadhaarNumber,
  kycMode,
  fleetSize,
  requestedLimitInr,
  loanPurposeText,
  employmentRemarks,
  applicantNotes,
  submitting,
  submitError,
  fieldErrors,
  submittedLoan,
  onSelectStep,
  onUtilityTypeChange,
  onFullNameChange,
  onCityChange,
  onEmploymentTypeChange,
  onEmployerNameChange,
  onPanNumberChange,
  onAadhaarNumberChange,
  onKycModeChange,
  onFleetSizeChange,
  onRequestedLimitChange,
  onLoanPurposeChange,
  onEmploymentRemarksChange,
  onApplicantNotesChange,
  onUseSampleData,
  onSubmit,
  onOpenCalculator,
}) => {
  const [touched, setTouched] = useState<Record<string, boolean>>({});

  const markTouched = (field: string) => {
    setTouched((prev) => ({ ...prev, [field]: true }));
  };

  const fullNameError =
    fieldErrors.full_name ||
    (touched.full_name && !fullName.trim()
      ? 'Please enter your full legal name as shown on your PAN card.'
      : '');

  const panError =
    fieldErrors.pan_number ||
    (touched.pan_number && !validatePanNumber(panNumber)
      ? 'Please enter a valid 10-character PAN (for example, ABCPK7419D — 5 letters, 4 digits, and 1 letter).'
      : '');

  const aadhaarError =
    fieldErrors.aadhaar_number ||
    (touched.aadhaar_number && !validateAadhaarNumber(aadhaarNumber)
      ? 'Please enter a valid 12-digit Aadhaar number using digits only.'
      : '');

  const purposeError = fieldErrors.loan_purpose_text || '';
  const remarksError = fieldErrors.employment_remarks || '';
  const notesError = fieldErrors.applicant_notes || '';

  const activeStep = wizardState.steps[wizardState.activeStepIndex];

  return (
    <div className="space-y-6">
      {/* Journey Progress Strip — shown ONLY on #journey */}
      <section
        aria-label="Loan Application Steps"
        className="bg-white border border-slate-200 rounded p-4 shadow-sm"
      >
        <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
          <div>
            <h2 className="text-sm font-semibold text-ink-heading">
              Identity Check &rarr; Loan Approval &rarr; Fleet Top-Up (Optional)
              &rarr; Insurance &amp; FASTag &rarr; Bank Payout
            </h2>
            <p className="text-xs text-ink-muted">
              Complete each step below or use sample data to preview an instant
              end-to-end vehicle loan sanction.
            </p>
          </div>
          <Badge variant={mapStatusToBadgeVariant(wizardState.overallStatus)}>
            {formatLoanStatusCustomer(wizardState.overallStatus)}
          </Badge>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-5 items-start gap-2">
          {wizardState.steps.map((step, idx) => {
            const isCurrent = idx === wizardState.activeStepIndex;
            return (
              <button
                key={step.code}
                type="button"
                onClick={() => onSelectStep(idx)}
                className={`w-full text-left p-2.5 rounded border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue ${
                  isCurrent
                    ? 'bg-gblue-tint/40 border-gblue'
                    : 'bg-surface border-slate-200 hover:bg-white'
                }`}
              >
                <div className="flex items-center justify-between gap-1.5">
                  <span className="text-xs font-semibold text-ink-heading truncate">
                    {step.stepNumber}. {step.shortLabel}
                  </span>
                </div>
                <div className="mt-1.5">
                  <Badge variant={mapStatusToBadgeVariant(step.status)}>
                    {stepStatusLabel(step.status)}
                  </Badge>
                </div>
              </button>
            );
          })}
        </div>
      </section>

      {/* Honest Error / Paused / Guardrail Banner */}
      {submitError && (
        <div
          role="alert"
          data-testid="journey-error-banner"
          className={`rounded border p-4 flex flex-wrap items-start justify-between gap-4 ${
            submitError.isPaused
              ? 'bg-gamber-tint border-gamber-border'
              : 'bg-gred-tint border-gred-border'
          }`}
        >
          <div className="flex items-start gap-3">
            <AlertTriangle
              size={18}
              className={
                submitError.isPaused
                  ? 'text-gamber mt-0.5 shrink-0'
                  : 'text-gred mt-0.5 shrink-0'
              }
            />
            <div>
              <h2 className="text-sm font-semibold text-ink-heading">
                {submitError.title}
              </h2>
              <p className="text-xs text-ink-body mt-0.5">
                {submitError.message}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {submitError.isRetryable && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => onSubmit()}
                disabled={submitting}
              >
                Try Again
              </Button>
            )}
            <Badge variant={submitError.isPaused ? 'warning' : 'danger'}>
              {submitError.isPaused
                ? 'Temporarily Paused'
                : submitError.isBlocked
                  ? 'Content Review Needed'
                  : 'Attention Needed'}
            </Badge>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-12 items-start gap-6">
        {/* Left 4 Cols: Step Guide & Scoped Live Updates (only shown once a journey exists) */}
        <div className="lg:col-span-4 space-y-4">
          <div className="bg-white border border-slate-200 rounded p-4 shadow-sm space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-ink-heading">
                Your 5-Step Application Checklist
              </h2>
              <span className="text-xs text-ink-muted tabular-nums">
                Step {wizardState.activeStepIndex + 1} of 5
              </span>
            </div>

            <div className="space-y-2">
              {wizardState.steps.map((step, idx) => {
                const isCurrent = idx === wizardState.activeStepIndex;
                return (
                  <button
                    key={step.code}
                    type="button"
                    onClick={() => onSelectStep(idx)}
                    className={`w-full text-left p-3 rounded border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue ${
                      isCurrent
                        ? 'bg-gblue-tint/40 border-gblue'
                        : 'bg-surface border-slate-200 hover:bg-white'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold text-ink-heading">
                        {step.stepNumber}. {step.title}
                      </span>
                      <Badge variant={mapStatusToBadgeVariant(step.status)}>
                        {stepStatusLabel(step.status)}
                      </Badge>
                    </div>
                    <p className="text-xs text-ink-muted mt-1">{step.detail}</p>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Live Status Updates — hidden until the visitor's own journey exists */}
          {wizardState.journeyId && (
            <div
              data-testid="journey-live-updates-panel"
              className="bg-white border border-slate-200 rounded p-4 shadow-sm space-y-3"
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-ink-heading flex items-center gap-1.5">
                  <Radio size={14} className="text-ggreen" />
                  Live Application Progress
                </span>
                <span className="text-xs font-mono text-ink-muted">
                  {wizardState.journeyId}
                </span>
              </div>
              <div className="max-h-56 overflow-y-auto divide-y divide-slate-200 border border-slate-200 rounded">
                {telemetry.events.length === 0 ? (
                  <div className="p-3 text-xs text-ink-muted">
                    Processing your application steps...
                  </div>
                ) : (
                  telemetry.events.slice(0, 8).map((ev) => (
                    <div
                      key={ev.event_id}
                      className="p-2.5 bg-white hover:bg-surface text-xs space-y-1"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-semibold text-ink-heading">
                          {formatTelemetryEventTitle(ev)}
                        </span>
                        <Badge
                          variant={mapStatusToBadgeVariant(
                            ev.status_to || 'COMPLETED'
                          )}
                        >
                          {formatLoanStatusCustomer(
                            ev.status_to || 'COMPLETED'
                          )}
                        </Badge>
                      </div>
                      {ev.duration_ms != null && (
                        <div className="text-xs text-ink-muted tabular-nums">
                          Completed in{' '}
                          {Number(ev.duration_ms).toLocaleString('en-IN')} ms
                        </div>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </div>

        {/* Right 8 Cols: Interactive 5-Step Wizard Form & Visitor Sanction Receipt */}
        <div className="lg:col-span-8 space-y-4">
          <form
            noValidate
            onSubmit={(e) => onSubmit(e)}
            className="bg-white border border-slate-200 rounded p-5 shadow-sm space-y-5"
          >
            {/* Wizard Form Header with Loan Type & "Use sample data" Ghost Button */}
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-3">
              <div>
                <h1 className="text-base font-semibold text-ink-heading">
                  Vehicle Loan Application — Step {activeStep.stepNumber}:{' '}
                  {activeStep.title}
                </h1>
                <p className="text-xs text-ink-muted">
                  Complete your identity verification, loan preferences, and
                  insurance options for instant approval.
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  id="use-sample-data-btn"
                  data-testid="use-sample-data-btn"
                  variant="ghost"
                  size="sm"
                  onClick={onUseSampleData}
                  className="border border-slate-200"
                >
                  <FileText size={14} className="text-gblue" />
                  Use sample data
                </Button>
                <Button
                  variant={
                    wizardState.utilityType === 'RETAIL_AUTO_LOAN'
                      ? 'primary'
                      : 'outline'
                  }
                  size="sm"
                  onClick={() => onUtilityTypeChange('RETAIL_AUTO_LOAN')}
                >
                  Personal / EV Loan
                </Button>
                <Button
                  variant={
                    wizardState.utilityType === 'COMMERCIAL_FLEET_TOPUP'
                      ? 'primary'
                      : 'outline'
                  }
                  size="sm"
                  onClick={() => onUtilityTypeChange('COMMERCIAL_FLEET_TOPUP')}
                >
                  Commercial Fleet + Top-Up
                </Button>
              </div>
            </div>

            {/* Step 1: Identity Check */}
            <div
              data-testid="wizard-step-panel-0"
              className={`space-y-3 rounded border p-4 ${
                wizardState.activeStepIndex === 0
                  ? 'border-gblue bg-white'
                  : 'border-slate-200 bg-surface/50'
              }`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-xs font-semibold text-ink-heading uppercase tracking-wider flex items-center gap-1.5">
                  <ShieldCheck size={15} className="text-gblue" />
                  Step 1: Identity Check (PAN &amp; Aadhaar)
                </h3>
                <div className="flex items-center gap-2">
                  <Button
                    variant={kycMode === 'TEXT_ONLY' ? 'secondary' : 'outline'}
                    size="sm"
                    onClick={() => onKycModeChange('TEXT_ONLY')}
                  >
                    Enter Details (Text Only)
                  </Button>
                  <Button
                    variant={kycMode === 'IMAGE' ? 'secondary' : 'outline'}
                    size="sm"
                    onClick={() => onKycModeChange('IMAGE')}
                  >
                    Upload Specimen ID Card
                  </Button>
                </div>
              </div>

              <div className="p-3 rounded bg-surface border border-slate-200 flex items-start gap-2.5 text-xs">
                <Info size={15} className="text-gblue shrink-0 mt-0.5" />
                <div>
                  <span className="font-semibold text-ink-heading">
                    Privacy &amp; Document Protection:
                  </span>{' '}
                  Your written application notes are checked automatically for
                  safety. Uploaded specimen PAN and Aadhaar card images are
                  stored securely and never scanned by text filters.
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 items-start gap-3">
                <div>
                  <label
                    htmlFor="kyc-fullname"
                    className="block text-xs font-semibold text-ink-heading mb-1"
                  >
                    Applicant Full Name
                  </label>
                  <input
                    id="kyc-fullname"
                    type="text"
                    required
                    placeholder="e.g. Vikramaditya Kulkarni"
                    value={fullName}
                    aria-invalid={Boolean(fullNameError)}
                    aria-describedby={
                      fullNameError ? 'kyc-fullname-error' : undefined
                    }
                    onBlur={() => markTouched('full_name')}
                    onChange={(e) => onFullNameChange(e.target.value)}
                    className={`w-full px-3 py-1.5 text-xs bg-white border rounded text-ink-heading placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue ${
                      fullNameError ? 'border-gred' : 'border-slate-300'
                    }`}
                  />
                  {fullNameError && (
                    <p
                      id="kyc-fullname-error"
                      className="text-xs text-gred mt-1"
                    >
                      {fullNameError}
                    </p>
                  )}
                </div>

                <div>
                  <label
                    htmlFor="kyc-pan"
                    className="block text-xs font-semibold text-ink-heading mb-1"
                  >
                    PAN Number (10 characters)
                  </label>
                  <input
                    id="kyc-pan"
                    type="text"
                    required
                    maxLength={10}
                    placeholder="e.g. ABCPK7419D"
                    value={panNumber}
                    aria-invalid={Boolean(panError)}
                    aria-describedby={panError ? 'kyc-pan-error' : undefined}
                    onBlur={() => markTouched('pan_number')}
                    onChange={(e) =>
                      onPanNumberChange(e.target.value.toUpperCase())
                    }
                    className={`w-full px-3 py-1.5 text-xs bg-white border rounded font-mono uppercase text-ink-heading placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue ${
                      panError ? 'border-gred' : 'border-slate-300'
                    }`}
                  />
                  {panError && (
                    <p id="kyc-pan-error" className="text-xs text-gred mt-1">
                      {panError}
                    </p>
                  )}
                </div>

                <div>
                  <label
                    htmlFor="kyc-aadhaar"
                    className="block text-xs font-semibold text-ink-heading mb-1"
                  >
                    Aadhaar Number (12 digits)
                  </label>
                  <input
                    id="kyc-aadhaar"
                    type="text"
                    required
                    maxLength={12}
                    placeholder="e.g. 999951827364"
                    value={aadhaarNumber}
                    aria-invalid={Boolean(aadhaarError)}
                    aria-describedby={
                      aadhaarError ? 'kyc-aadhaar-error' : undefined
                    }
                    onBlur={() => markTouched('aadhaar_number')}
                    onChange={(e) =>
                      onAadhaarNumberChange(e.target.value.replace(/\D/g, ''))
                    }
                    className={`w-full px-3 py-1.5 text-xs bg-white border rounded font-mono tabular-nums text-ink-heading placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue ${
                      aadhaarError ? 'border-gred' : 'border-slate-300'
                    }`}
                  />
                  {aadhaarError && (
                    <p
                      id="kyc-aadhaar-error"
                      className="text-xs text-gred mt-1"
                    >
                      {aadhaarError}
                    </p>
                  )}
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 items-start gap-3">
                <div>
                  <label
                    htmlFor="kyc-city"
                    className="block text-xs font-semibold text-ink-heading mb-1"
                  >
                    City
                  </label>
                  <input
                    id="kyc-city"
                    type="text"
                    placeholder="e.g. Bengaluru"
                    value={city}
                    onChange={(e) => onCityChange(e.target.value)}
                    className="w-full px-3 py-1.5 text-xs bg-white border border-slate-300 rounded text-ink-heading placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue"
                  />
                </div>
                <div>
                  <label
                    htmlFor="kyc-employment"
                    className="block text-xs font-semibold text-ink-heading mb-1"
                  >
                    Occupation Type
                  </label>
                  <select
                    id="kyc-employment"
                    value={employmentType}
                    onChange={(e) =>
                      onEmploymentTypeChange(e.target.value as EmploymentType)
                    }
                    className="w-full px-3 py-1.5 text-xs bg-white border border-slate-300 rounded text-ink-heading focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue"
                  >
                    <option value="SALARIED">Salaried Employee</option>
                    <option value="SELF_EMPLOYED">
                      Self-Employed Professional
                    </option>
                    <option value="FLEET_OPERATOR">
                      Commercial Fleet Operator
                    </option>
                  </select>
                </div>
                <div>
                  <label
                    htmlFor="kyc-employer"
                    className="block text-xs font-semibold text-ink-heading mb-1"
                  >
                    Employer or Business Name
                  </label>
                  <input
                    id="kyc-employer"
                    type="text"
                    placeholder="e.g. Sahyadri Digital Systems Ltd"
                    value={employerName}
                    onChange={(e) => onEmployerNameChange(e.target.value)}
                    className="w-full px-3 py-1.5 text-xs bg-white border border-slate-300 rounded text-ink-heading placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue"
                  />
                </div>
              </div>

              {kycMode === 'IMAGE' && (
                <div className="p-3 rounded bg-surface border border-slate-200 flex items-center justify-between text-xs">
                  <div>
                    <p className="font-semibold text-ink-heading">
                      Watermarked Specimen Identity Cards Attached
                    </p>
                    <p className="text-xs text-ink-muted">
                      Marked SPECIMEN — NOT A GOVERNMENT DOCUMENT for demo
                      verification.
                    </p>
                  </div>
                  <Badge variant="info">Specimen Card Attached</Badge>
                </div>
              )}
            </div>

            {/* Step 2 & 3: Loan Terms & Optional Fleet Credit */}
            <div
              data-testid="wizard-step-panel-1"
              className={`space-y-3 rounded border p-4 ${
                wizardState.activeStepIndex === 1 ||
                wizardState.activeStepIndex === 2
                  ? 'border-gblue bg-white'
                  : 'border-slate-200 bg-surface/50'
              }`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-xs font-semibold text-ink-heading uppercase tracking-wider flex items-center gap-1.5">
                  <Car size={15} className="text-gblue" />
                  Steps 2 &amp; 3: Loan Approval &amp; Optional Fleet Top-Up
                </h3>
                <Button variant="outline" size="sm" onClick={onOpenCalculator}>
                  Adjust EMI &amp; Tenure
                </Button>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 items-start gap-3 bg-surface p-3 rounded border border-slate-200 text-xs">
                <div>
                  <span className="text-ink-muted block">Selected Vehicle</span>
                  <span className="font-semibold text-ink-heading">
                    {selectedVehicle.model}
                  </span>
                </div>
                <div>
                  <span className="text-ink-muted block">Loan Principal</span>
                  <span className="font-semibold text-ink-heading tabular-nums">
                    {formatInr(financeMetrics.principalInr)}
                  </span>
                </div>
                <div>
                  <span className="text-ink-muted block">
                    Monthly EMI ({tenureMonths}m @ {interestRatePct}%)
                  </span>
                  <span className="font-semibold text-gblue tabular-nums">
                    {formatInr(financeMetrics.emiInr)}/mo
                  </span>
                </div>
                <div>
                  <span className="text-ink-muted block">
                    Income Share (Max 60%)
                  </span>
                  <span className="font-semibold text-ink-heading tabular-nums">
                    {financeMetrics.foirPct}% of {formatInr(monthlyIncomeInr)}
                  </span>
                </div>
              </div>

              <p className="text-xs text-ink-muted">
                Your total EMIs (including {formatInr(existingObligationsInr)}{' '}
                existing obligations and {formatInr(financeMetrics.emiInr)} for
                this vehicle) would use{' '}
                <span className="font-semibold text-ink-heading tabular-nums">
                  {financeMetrics.foirPct}%
                </span>{' '}
                of your monthly income — we allow up to 60%. Down payment:{' '}
                <span className="font-semibold text-ink-heading tabular-nums">
                  {formatInr(downPaymentInr)}
                </span>
                .
              </p>

              {wizardState.utilityType === 'COMMERCIAL_FLEET_TOPUP' && (
                <div className="grid grid-cols-1 sm:grid-cols-2 items-start gap-3 p-3 rounded bg-gamber-tint/40 border border-gamber-border">
                  <div>
                    <label
                      htmlFor="fleet-size-input"
                      className="block text-xs font-semibold text-ink-heading mb-1"
                    >
                      Step 3 — Active Commercial Fleet Size (Vehicles)
                    </label>
                    <input
                      id="fleet-size-input"
                      type="number"
                      min={1}
                      value={fleetSize}
                      onChange={(e) =>
                        onFleetSizeChange(Math.max(1, Number(e.target.value)))
                      }
                      className="w-full px-3 py-1.5 text-xs bg-white border border-slate-300 rounded tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue"
                    />
                  </div>
                  <div>
                    <div className="flex items-center justify-between text-xs mb-1">
                      <label
                        htmlFor="fleet-limit-input"
                        className="font-semibold text-ink-heading"
                      >
                        Working-Capital Top-Up Credit Limit
                      </label>
                      <span className="font-semibold text-ink-heading tabular-nums">
                        {formatInr(requestedLimitInr)}
                      </span>
                    </div>
                    <input
                      id="fleet-limit-input"
                      type="number"
                      step={50000}
                      value={requestedLimitInr}
                      onChange={(e) =>
                        onRequestedLimitChange(
                          Math.max(50000, Number(e.target.value))
                        )
                      }
                      className="w-full px-3 py-1.5 text-xs bg-white border border-slate-300 rounded tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue"
                    />
                  </div>
                </div>
              )}
            </div>

            {/* Step 4 & 5: Insurance, FASTag & Application Details */}
            <div
              data-testid="wizard-step-panel-3"
              className={`space-y-3 rounded border p-4 ${
                wizardState.activeStepIndex === 3 ||
                wizardState.activeStepIndex === 4
                  ? 'border-gblue bg-white'
                  : 'border-slate-200 bg-surface/50'
              }`}
            >
              <h3 className="text-xs font-semibold text-ink-heading uppercase tracking-wider flex items-center gap-1.5">
                <FileText size={15} className="text-gblue" />
                Steps 4 &amp; 5: Insurance, FASTag &amp; Application Details
              </h3>

              <div className="grid grid-cols-1 sm:grid-cols-3 items-start gap-3">
                <div>
                  <label
                    htmlFor="field-loan-purpose"
                    className="block text-xs font-semibold text-ink-heading mb-1"
                  >
                    Purpose of Vehicle Purchase
                  </label>
                  <textarea
                    id="field-loan-purpose"
                    rows={2}
                    placeholder="e.g. Personal electric SUV for daily city commute and family travel."
                    value={loanPurposeText}
                    aria-invalid={Boolean(purposeError)}
                    aria-describedby={
                      purposeError ? 'field-loan-purpose-error' : undefined
                    }
                    onChange={(e) => onLoanPurposeChange(e.target.value)}
                    className="w-full px-2.5 py-1.5 text-xs bg-white border border-slate-300 rounded text-ink-heading placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue"
                  />
                  {purposeError && (
                    <p
                      id="field-loan-purpose-error"
                      className="text-xs text-gred mt-1"
                    >
                      {purposeError}
                    </p>
                  )}
                </div>

                <div>
                  <label
                    htmlFor="field-employment-remarks"
                    className="block text-xs font-semibold text-ink-heading mb-1"
                  >
                    Employment &amp; Income Details
                  </label>
                  <textarea
                    id="field-employment-remarks"
                    rows={2}
                    placeholder="e.g. Salaried engineer with 8 years experience and direct salary credit."
                    value={employmentRemarks}
                    aria-invalid={Boolean(remarksError)}
                    aria-describedby={
                      remarksError
                        ? 'field-employment-remarks-error'
                        : undefined
                    }
                    onChange={(e) => onEmploymentRemarksChange(e.target.value)}
                    className="w-full px-2.5 py-1.5 text-xs bg-white border border-slate-300 rounded text-ink-heading placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue"
                  />
                  {remarksError && (
                    <p
                      id="field-employment-remarks-error"
                      className="text-xs text-gred mt-1"
                    >
                      {remarksError}
                    </p>
                  )}
                </div>

                <div>
                  <label
                    htmlFor="field-applicant-notes"
                    className="block text-xs font-semibold text-ink-heading mb-1"
                  >
                    Additional Preferences (Insurance &amp; FASTag)
                  </label>
                  <textarea
                    id="field-applicant-notes"
                    rows={2}
                    placeholder="e.g. Include zero-depreciation motor insurance and pre-linked FASTag wallet."
                    value={applicantNotes}
                    aria-invalid={Boolean(notesError)}
                    aria-describedby={
                      notesError ? 'field-applicant-notes-error' : undefined
                    }
                    onChange={(e) => onApplicantNotesChange(e.target.value)}
                    className="w-full px-2.5 py-1.5 text-xs bg-white border border-slate-300 rounded text-ink-heading placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue"
                  />
                  {notesError && (
                    <p
                      id="field-applicant-notes-error"
                      className="text-xs text-gred mt-1"
                    >
                      {notesError}
                    </p>
                  )}
                </div>
              </div>
            </div>

            {/* Wizard Navigation & Primary Submit Footer */}
            <div className="flex flex-wrap items-center justify-between gap-3 pt-3 border-t border-slate-200">
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="md"
                  disabled={wizardState.activeStepIndex === 0}
                  onClick={() =>
                    onSelectStep(Math.max(0, wizardState.activeStepIndex - 1))
                  }
                >
                  <ArrowLeft size={14} />
                  Previous Step
                </Button>
                <Button
                  variant="outline"
                  size="md"
                  disabled={
                    wizardState.activeStepIndex ===
                    wizardState.steps.length - 1
                  }
                  onClick={() =>
                    onSelectStep(
                      Math.min(
                        wizardState.steps.length - 1,
                        wizardState.activeStepIndex + 1
                      )
                    )
                  }
                >
                  Next Step
                  <ArrowRight size={14} />
                </Button>
              </div>

              <Button
                id="submit-journey-btn"
                type="submit"
                variant="primary"
                size="lg"
                disabled={submitting}
              >
                <Wallet size={15} />
                {submitting
                  ? 'Submitting Loan Application...'
                  : 'Submit Loan Application'}
              </Button>
            </div>
          </form>

          {/* Visitor's Own Loan Sanction Receipt — ONLY rendered when submittedLoan exists in this session */}
          {submittedLoan && (
            <div
              data-testid="visitor-sanction-receipt"
              className="bg-white border border-slate-200 rounded p-5 shadow-sm space-y-4"
            >
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 pb-3">
                <div className="flex items-center gap-2">
                  <CheckCircle2 size={18} className="text-ggreen shrink-0" />
                  <div>
                    <span className="text-xs font-mono text-ink-muted block">
                      APPLICATION REFERENCE · {submittedLoan.journey_id}
                    </span>
                    <h3 className="text-sm font-semibold text-ink-heading">
                      {submittedLoan.customer?.full_name} —{' '}
                      {submittedLoan.vehicle?.model || submittedLoan.vehicle_id}
                    </h3>
                  </div>
                </div>
                <Badge variant={mapStatusToBadgeVariant(submittedLoan.status)}>
                  {formatLoanStatusCustomer(submittedLoan.status)}
                </Badge>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 items-start gap-3 text-xs">
                <div className="p-2.5 rounded bg-surface border border-slate-200">
                  <span className="text-ink-muted block">Verified PAN</span>
                  <span className="font-semibold text-ink-heading font-mono">
                    {maskPan(submittedLoan.customer?.pan_number)}
                  </span>
                </div>
                <div className="p-2.5 rounded bg-surface border border-slate-200">
                  <span className="text-ink-muted block">
                    Income Share (FOIR)
                  </span>
                  <span className="font-semibold text-ink-heading tabular-nums">
                    {submittedLoan.underwriting_audit?.foir_pct != null
                      ? `${submittedLoan.underwriting_audit.foir_pct}%`
                      : '—'}
                  </span>
                </div>
                <div className="p-2.5 rounded bg-surface border border-slate-200">
                  <span className="text-ink-muted block">
                    FASTag &amp; Insurance
                  </span>
                  <span className="font-semibold text-ink-heading font-mono">
                    {submittedLoan.insurance_policy?.fasttag_id || 'Pending'}
                  </span>
                </div>
                <div className="p-2.5 rounded bg-surface border border-slate-200">
                  <span className="text-ink-muted block">
                    Bank Payout Reference
                  </span>
                  <span className="font-semibold text-ggreen font-mono">
                    {submittedLoan.disbursement?.cbs_utr_number || 'Pending'}
                  </span>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
