import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import {
  calculateFinanceMetrics,
  formatInr,
  maskPan,
  validatePanNumber,
  validateAadhaarNumber,
} from '../utils/calculator';
import {
  ALLOWED_BADGE_VARIANTS,
  mapStatusToBadgeVariant,
  type BadgeVariant,
} from '../components/Badge';
import {
  initialWizardState,
  journeyWizardReducer,
  telemetryReducer,
  initialTelemetryState,
} from '../state/journeyState';
import App from '../App';

describe('URL 1 — Customer Loan Portal: Calculator Maths & Formatting', () => {
  it('computes accurate EMI, LTV, and FOIR for standard reducing-balance auto loan', () => {
    const metrics = calculateFinanceMetrics({
      exShowroomInr: 1850000,
      downPaymentInr: 350000,
      tenureMonths: 60,
      interestRatePct: 8.75,
      monthlyIncomeInr: 115000,
      existingObligationsInr: 12000,
      minDownPaymentPct: 15,
    });

    expect(metrics.principalInr).toBe(1500000);
    expect(metrics.emiInr).toBeGreaterThanOrEqual(30950);
    expect(metrics.emiInr).toBeLessThanOrEqual(30965);
    expect(metrics.ltvPct).toBeCloseTo(81.08, 1);
    expect(metrics.downPaymentPct).toBeCloseTo(18.92, 1);
    expect(metrics.foirPct).toBeCloseTo(37.35, 1);
    expect(metrics.foirEligible).toBe(true);
    expect(metrics.downPaymentEligible).toBe(true);
    expect(metrics.totalPayableInr).toBe(metrics.emiInr * 60);
    expect(metrics.totalInterestInr).toBe(metrics.totalPayableInr - 1500000);
  });

  it('flags policy breach when FOIR exceeds 60% and still marks eligible when <= 60%', () => {
    const breached = calculateFinanceMetrics({
      exShowroomInr: 2400000,
      downPaymentInr: 240000,
      tenureMonths: 36,
      interestRatePct: 9.5,
      monthlyIncomeInr: 85000,
      existingObligationsInr: 15000,
      minDownPaymentPct: 10,
    });
    expect(breached.foirPct).toBeGreaterThan(60);
    expect(breached.foirEligible).toBe(false);

    const eligible = calculateFinanceMetrics({
      exShowroomInr: 1200000,
      downPaymentInr: 300000,
      tenureMonths: 60,
      interestRatePct: 8.5,
      monthlyIncomeInr: 95000,
      existingObligationsInr: 5000,
      minDownPaymentPct: 10,
    });
    expect(eligible.foirPct).toBeLessThanOrEqual(60);
    expect(eligible.foirEligible).toBe(true);
  });

  it('formats Indian Rupee figures with en-IN grouping, masks PANs, and validates PAN/Aadhaar patterns', () => {
    expect(formatInr(1485000)).toBe('₹14,85,000');
    expect(formatInr(30956)).toBe('₹30,956');
    expect(maskPan('ABCPK7419D')).toBe('ABC••••19D');
    expect(maskPan('')).toBe('—');
    expect(validatePanNumber('ABCDE1234F')).toBe(true);
    expect(validatePanNumber('abcde1234f')).toBe(false);
    expect(validatePanNumber('12345ABCDE')).toBe(false);
    expect(validateAadhaarNumber('987654321012')).toBe(true);
    expect(validateAadhaarNumber('98765432')).toBe(false);
  });
});

describe('URL 1 — Badge Variants Contract', () => {
  it('maps every loan and step status strictly to the 8 allowed badge variants (never destructive)', () => {
    const statuses = [
      'KYC_PENDING',
      'KYC_VERIFIED',
      'MODEL_ARMOR_BLOCKED',
      'APPROVED',
      'REJECTED',
      'INSURED',
      'DISBURSEMENT_PENDING',
      'DISBURSED',
    ] as const;

    for (const s of statuses) {
      const variant: BadgeVariant = mapStatusToBadgeVariant(s);
      expect(ALLOWED_BADGE_VARIANTS).toContain(variant);
      expect(variant).not.toBe('destructive');
    }
    expect(mapStatusToBadgeVariant('MODEL_ARMOR_BLOCKED')).toBe('danger');
    expect(mapStatusToBadgeVariant('DISBURSED')).toBe('success');
    expect(mapStatusToBadgeVariant('APPROVED')).toBe('success');
  });
});

describe('URL 1 — 5-Step Journey Wizard State Machine & SSE Telemetry Reducer', () => {
  it('starts fresh sessions with all steps PENDING (or SKIPPED for optional Fleet Top-Up) and transitions cleanly on submit', () => {
    let state = initialWizardState('RETAIL_AUTO_LOAN');
    expect(state.activeStepIndex).toBe(0);
    expect(state.steps[0].status).toBe('PENDING');
    expect(state.steps[1].status).toBe('PENDING');
    expect(state.steps[2].status).toBe('SKIPPED');
    expect(state.steps[3].status).toBe('PENDING');
    expect(state.steps[4].status).toBe('PENDING');

    state = journeyWizardReducer(state, {
      type: 'APPLICATION_SUBMITTED',
      loanId: 'loan-retail-101',
      journeyId: 'jrn-retail-101',
      utilityType: 'RETAIL_AUTO_LOAN',
      finalStatus: 'DISBURSED',
    });

    expect(state.loanId).toBe('loan-retail-101');
    expect(state.steps[0].status).toBe('COMPLETED');
    expect(state.steps[1].status).toBe('COMPLETED');
    expect(state.steps[2].status).toBe('SKIPPED');
    expect(state.steps[3].status).toBe('COMPLETED');
    expect(state.steps[4].status).toBe('COMPLETED');
  });

  it('activates Step 3 (Commercial Fleet Top-Up) when utility_type is COMMERCIAL_FLEET_TOPUP and halts on MODEL_ARMOR_BLOCKED', () => {
    const fleetState = initialWizardState('COMMERCIAL_FLEET_TOPUP');
    expect(fleetState.steps[2].status).toBe('PENDING');

    const blockedState = journeyWizardReducer(fleetState, {
      type: 'APPLICATION_SUBMITTED',
      loanId: 'loan-blocked-99',
      journeyId: 'jrn-blocked-99',
      utilityType: 'COMMERCIAL_FLEET_TOPUP',
      finalStatus: 'MODEL_ARMOR_BLOCKED',
    });

    expect(blockedState.steps[0].status).toBe('BLOCKED');
    expect(blockedState.steps[1].status).toBe('PENDING');
  });

  it('reduces SSE telemetry stream events and deduplicates by event_id', () => {
    let tState = initialTelemetryState;
    const ev1 = {
      event_id: 'evt-1',
      event_time: '2026-10-16T10:00:01Z',
      journey_id: 'jrn-1',
      loan_id: 'loan-1',
      customer_id: 'cust-1',
      trace_id: 'trc-1',
      service: 'kyc-model-armor-svc',
      operation: 'POST /internal/kyc/verify',
      event_type: 'kyc.verified' as const,
      status_to: 'KYC_VERIFIED' as const,
      duration_ms: 140,
    };
    const ev2 = {
      event_id: 'evt-2',
      event_time: '2026-10-16T10:00:02Z',
      journey_id: 'jrn-1',
      loan_id: 'loan-1',
      customer_id: 'cust-1',
      trace_id: 'trc-1',
      service: 'underwriting-los-svc',
      operation: 'POST /internal/underwriting/evaluate',
      event_type: 'loan.decided' as const,
      decision: 'APPROVED',
      foir_pct: 41.2,
      cibil_score: 762,
      status_to: 'APPROVED' as const,
      duration_ms: 310,
    };

    tState = telemetryReducer(tState, { type: 'EVENT_RECEIVED', event: ev1 });
    tState = telemetryReducer(tState, { type: 'EVENT_RECEIVED', event: ev1 }); // duplicate
    tState = telemetryReducer(tState, { type: 'EVENT_RECEIVED', event: ev2 });

    expect(tState.events).toHaveLength(2);
    expect(tState.byJourneyId['jrn-1']).toHaveLength(2);
    expect(tState.latestStatusByLoanId['loan-1']).toBe('APPROVED');
  });
});

describe('URL 1 — Full Portal Views, Privacy Isolation, Error Mapping & I-10 Compliance', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it('renders vehicle showroom from /api/v1/vehicles, hides other customers PII, and keeps progress rail only on #journey', async () => {
    window.location.hash = '#showroom';
    render(<App />);

    await waitFor(() => {
      expect(screen.getByText(/CymbalPravaah EV-X/i)).toBeInTheDocument();
    });

    // Ensure no simulator or chaos controls exist on URL 1 (Invariant I-10)
    expect(screen.queryByText(/Chaos Drill/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Kill Switch/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Simulator Burst/i)).not.toBeInTheDocument();

    // Ensure progress rail is NOT shown on #showroom (#7)
    expect(
      screen.queryByLabelText(/Loan Application Steps/i)
    ).not.toBeInTheDocument();

    // Navigate to #calculator and verify read-only interest rate (#10)
    fireEvent.click(
      screen.getByRole('button', { name: /EMI & Eligibility Calculator/i })
    );
    expect(window.location.hash).toBe('#calculator');
    expect(
      screen.getAllByText(/Fixed Obligation to Income Ratio/i).length
    ).toBeGreaterThan(0);
    expect(screen.getByTestId('calculator-readonly-rate')).toHaveTextContent(
      /Indicative 8.45% p.a./i
    );

    // Navigate to #applications and verify empty state (no other customers' PII shown) (#1)
    fireEvent.click(screen.getByRole('button', { name: /My Applications/i }));
    expect(window.location.hash).toBe('#applications');
    expect(
      screen.getByTestId('applications-empty-state')
    ).toBeInTheDocument();
    expect(screen.queryByText(/Ananya Deshmukh/i)).not.toBeInTheDocument();
  });

  it('distinguishes HTTP 503 TENANT_PAUSED from generic HTTP 503 service failure and validates inline on blur', async () => {
    window.location.hash = '#journey';
    render(<App />);

    // Progress strip IS rendered on #journey
    await waitFor(() => {
      expect(
        screen.getByLabelText(/Loan Application Steps/i)
      ).toBeInTheDocument();
    });

    // No pre-seeded receipt before submission (#1)
    expect(
      screen.queryByTestId('visitor-sanction-receipt')
    ).not.toBeInTheDocument();

    // Inline blur validation in plain customer words (#8)
    const panInput = screen.getByLabelText(/PAN Number/i);
    fireEvent.change(panInput, { target: { value: 'BADPAN' } });
    fireEvent.blur(panInput);
    expect(panInput).toHaveAttribute('aria-invalid', 'true');
    expect(
      screen.getByText(/Please enter a valid 10-character PAN/i)
    ).toBeInTheDocument();

    // Populate with sample data button (#8)
    fireEvent.click(screen.getByTestId('use-sample-data-btn'));
    expect(panInput).toHaveValue('ABCPK7419D');

    // 1. Test 503 TENANT_PAUSED
    server.use(
      http.post('/api/v1/loans/apply', () => {
        return HttpResponse.json(
          {
            code: 'TENANT_PAUSED',
            message:
              'Tenant traffic paused by operator or scheduled window (17:00–09:00 IST)',
          },
          { status: 503 }
        );
      })
    );

    fireEvent.click(
      screen.getByRole('button', { name: /Submit Loan Application/i })
    );

    await waitFor(() => {
      expect(
        screen.getByText(/Applications are paused right now/i)
      ).toBeInTheDocument();
    });

    // 2. Test generic 503 (NOT TENANT_PAUSED) -> shows retryable service error (#3)
    server.use(
      http.post('/api/v1/loans/apply', () => {
        return HttpResponse.json(
          {
            code: 'UPSTREAM_UNAVAILABLE',
            message: 'Downstream bureau check timed out.',
          },
          { status: 503 }
        );
      })
    );

    fireEvent.click(
      screen.getByRole('button', { name: /Submit Loan Application/i })
    );

    await waitFor(() => {
      expect(
        screen.getByText(/Service Temporarily Unavailable/i)
      ).toBeInTheDocument();
      expect(
        screen.getByRole('button', { name: /Try Again/i })
      ).toBeInTheDocument();
    });
  });
});
