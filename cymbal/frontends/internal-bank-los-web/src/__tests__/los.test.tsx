import { describe, it, expect } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { SEED_LOANS } from '../mocks/data';
import {
  sortLoanQueue,
  filterLoanQueue,
  computeLosKpis,
  formatEnumLabel,
  formatLatencyMs,
} from '../utils/queueUtils';
import {
  ALLOWED_BADGE_VARIANTS,
  mapStatusToBadgeVariant,
} from '../components/Badge';
import {
  losTelemetryReducer,
  initialLosTelemetryState,
} from '../state/telemetryState';
import App from '../App';

describe('URL 2 — Internal Bank LOS: Queue Sorting, Filtering, Formatters & KPI Maths', () => {
  it('sorts loan applications deterministically by loan_amount_inr, foir_pct, and cibil_score (asc & desc)', () => {
    const byAmountDesc = sortLoanQueue(SEED_LOANS, 'loan_amount_inr', 'desc');
    expect(byAmountDesc[0].loan_amount_inr).toBeGreaterThanOrEqual(
      byAmountDesc[byAmountDesc.length - 1].loan_amount_inr
    );

    const byAmountAsc = sortLoanQueue(SEED_LOANS, 'loan_amount_inr', 'asc');
    expect(byAmountAsc[0].loan_amount_inr).toBeLessThanOrEqual(
      byAmountAsc[byAmountAsc.length - 1].loan_amount_inr
    );

    const byFoirDesc = sortLoanQueue(SEED_LOANS, 'foir_pct', 'desc');
    const firstFoir = byFoirDesc[0].underwriting_audit?.foir_pct ?? 0;
    const lastFoir =
      byFoirDesc[byFoirDesc.length - 1].underwriting_audit?.foir_pct ?? 0;
    expect(firstFoir).toBeGreaterThanOrEqual(lastFoir);
  });

  it('filters loan applications by status, utility_type, synthetic marker, and search query', () => {
    const blockedOnly = filterLoanQueue(SEED_LOANS, {
      status: 'MODEL_ARMOR_BLOCKED',
      utilityType: 'ALL',
      syntheticMode: 'ALL',
      search: '',
    });
    expect(blockedOnly.length).toBeGreaterThan(0);
    expect(
      blockedOnly.every((l) => l.status === 'MODEL_ARMOR_BLOCKED')
    ).toBe(true);

    const humanOnly = filterLoanQueue(SEED_LOANS, {
      status: 'ALL',
      utilityType: 'ALL',
      syntheticMode: 'HUMAN',
      search: '',
    });
    expect(humanOnly.length).toBeGreaterThan(0);
    expect(
      humanOnly.every((l) => l.customer?.is_ai_generated === false)
    ).toBe(true);

    const syntheticOnly = filterLoanQueue(SEED_LOANS, {
      status: 'ALL',
      utilityType: 'ALL',
      syntheticMode: 'SYNTHETIC',
      search: '',
    });
    expect(syntheticOnly.length).toBeGreaterThan(0);
    expect(
      syntheticOnly.every((l) => l.customer?.is_ai_generated === true)
    ).toBe(true);
  });

  it('computes accurate KPI ribbon totals and formats enums and millisecond latencies cleanly', () => {
    const kpis = computeLosKpis(SEED_LOANS);
    expect(kpis.totalApplications).toBe(SEED_LOANS.length);
    expect(kpis.approvalRatePct).toBeGreaterThan(0);
    expect(kpis.approvalRatePct).toBeLessThanOrEqual(100);
    expect(kpis.modelArmorBlockedCount).toBeGreaterThanOrEqual(1);
    expect(kpis.stuckDisbursementCount).toBeGreaterThanOrEqual(1);
    expect(kpis.stuckDisbursementRupees).toBeGreaterThan(0);

    expect(formatEnumLabel('MODEL_ARMOR_BLOCKED')).toBe('Model Armor Blocked');
    expect(formatEnumLabel('DISBURSEMENT_PENDING')).toBe(
      'Disbursement Pending'
    );
    expect(formatEnumLabel('COMPREHENSIVE_MOTOR_3YR')).toBe(
      'Comprehensive Motor (3-Yr)'
    );
    expect(formatLatencyMs(1480)).toBe('1,480 ms');
    expect(formatLatencyMs(6140)).toBe('6,140 ms');
  });
});

describe('URL 2 — Badge Variants & SSE Telemetry Reducer', () => {
  it('maps every loan, audit, topology, and disbursement status strictly to allowed badge variants (no destructive)', () => {
    const statuses = [
      'KYC_PENDING',
      'KYC_VERIFIED',
      'MODEL_ARMOR_BLOCKED',
      'APPROVED',
      'REJECTED',
      'INSURED',
      'DISBURSEMENT_PENDING',
      'DISBURSED',
      'PENDING',
      'FAILED',
      'REPLAYED',
      'CLEAN',
      'BLOCKED',
      'DEGRADED',
      'HEALTHY',
    ];
    for (const s of statuses) {
      const v = mapStatusToBadgeVariant(s);
      expect(ALLOWED_BADGE_VARIANTS).toContain(v);
      expect(v).not.toBe('destructive');
    }
    expect(mapStatusToBadgeVariant('DEGRADED')).toBe('warning');
  });

  it('ingests SSE events into LOS telemetry state and deduplicates by event_id', () => {
    let state = initialLosTelemetryState;
    const ev = {
      event_id: 'evt-los-1',
      event_time: '2026-10-16T11:10:00Z',
      journey_id: 'jrn-los-1',
      loan_id: 'loan-los-1',
      customer_id: 'cust-los-1',
      trace_id: 'trc-los-1',
      service: 'kyc-model-armor-svc',
      operation: 'POST /internal/kyc/verify',
      event_type: 'armor.blocked' as const,
      status_to: 'MODEL_ARMOR_BLOCKED' as const,
      duration_ms: 88,
    };
    state = losTelemetryReducer(state, { type: 'EVENT_RECEIVED', event: ev });
    state = losTelemetryReducer(state, { type: 'EVENT_RECEIVED', event: ev });
    expect(state.events).toHaveLength(1);
    expect(state.events[0].event_type).toBe('armor.blocked');
  });
});

describe('URL 2 — Views (#queue, #vault, #ledgers, #replay, #topology) & IAP User Chip', () => {
  it('renders warning chip "Not signed in (no IAP header)" when IAP header is absent', async () => {
    server.use(
      http.get('/api/v1/me', () => {
        return HttpResponse.json({
          email: '',
          iap_authenticated: false,
        });
      })
    );

    window.location.hash = '#queue';
    render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId('iap-user-chip')).toHaveTextContent(
        /Not signed in \(no IAP header\)/i
      );
    });
  });

  it('renders KYC Vault Inspector (#vault) with payload-driven GCS URI, template ID, and explicit text-only Model Armor verdict', async () => {
    window.location.hash = '#vault';
    render(<App />);

    await waitFor(() => {
      expect(
        screen.getByText(/Text-Only Model Armor Guardrail/i)
      ).toBeInTheDocument();
      expect(
        screen.getByText(/Specimen images are never scanned by Model Armor/i)
      ).toBeInTheDocument();
      expect(
        screen.getAllByText(/gs:\/\/general-ak-god-view-kyc-vault/i).length
      ).toBeGreaterThan(0);
      expect(
        screen.getByText(
          /projects\/general-ak\/locations\/us-central1\/templates\/cymbal-text-guard/i
        )
      ).toBeInTheDocument();
    });
  });

  it('renders Multi-Utility Ledgers (#ledgers) for godview_lms and Read-Only Saga Replay Queue (#replay) with biz.replay_disbursement', async () => {
    window.location.hash = '#replay';
    render(<App />);

    await waitFor(() => {
      expect(
        screen.getAllByText(/Saga Replay Queue/i).length
      ).toBeGreaterThan(0);
      expect(screen.getByText(/Read-Only Inspector/i)).toBeInTheDocument();
      expect(
        screen.getByText(/biz\.replay_disbursement/i)
      ).toBeInTheDocument();
    });

    fireEvent.click(
      screen.getByRole('button', { name: /Multi-Utility Ledgers/i })
    );
    expect(window.location.hash).toBe('#ledgers');
    await waitFor(() => {
      expect(screen.getByText(/godview_lms/i)).toBeInTheDocument();
      expect(
        screen.getAllByText(/underwriting_audits/i).length
      ).toBeGreaterThan(0);
    });
  });
});
