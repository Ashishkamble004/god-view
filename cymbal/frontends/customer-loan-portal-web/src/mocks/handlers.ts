import { http, HttpResponse } from 'msw';
import { SEED_LOANS, SEED_TELEMETRY_EVENTS, SEED_VEHICLES } from './data';
import type {
  LoanApplication,
  LoanApplyRequest,
  LoanStatus,
  TelemetryStreamEvent,
} from '../api/types';
import { calculateFinanceMetrics } from '../utils/calculator';

const loansStore: LoanApplication[] = [...SEED_LOANS];
const telemetryListeners = new Set<(ev: TelemetryStreamEvent) => void>();

export function subscribeMockTelemetry(
  listener: (ev: TelemetryStreamEvent) => void
): () => void {
  telemetryListeners.add(listener);
  return () => {
    telemetryListeners.delete(listener);
  };
}

function broadcastMockTelemetry(event: TelemetryStreamEvent) {
  telemetryListeners.forEach((fn) => fn(event));
  if (typeof window !== 'undefined') {
    window.dispatchEvent(
      new CustomEvent<TelemetryStreamEvent>('cymbal:telemetry', {
        detail: event,
      })
    );
  }
}

function containsAdversarialPrompt(text: string): boolean {
  const lowered = text.toLowerCase();
  return (
    lowered.includes('ignore previous instructions') ||
    lowered.includes('system prompt') ||
    lowered.includes('drop table') ||
    lowered.includes('override underwriting') ||
    lowered.includes('bypass kyc')
  );
}

export const handlers = [
  http.get('/api/v1/vehicles', () => {
    return HttpResponse.json(SEED_VEHICLES);
  }),

  http.get('/api/v1/loans', ({ request }) => {
    const url = new URL(request.url);
    const statusFilter = url.searchParams.get('status');
    const limitParam = Number(url.searchParams.get('limit') || '50');
    const filtered = statusFilter
      ? loansStore.filter((l) => l.status === statusFilter)
      : loansStore;
    return HttpResponse.json(filtered.slice(0, limitParam));
  }),

  http.get('/api/v1/loans/:loanId', ({ params }) => {
    const found = loansStore.find((l) => l.loan_id === params.loanId);
    if (!found) {
      return HttpResponse.json(
        { code: 'NOT_FOUND', message: 'Loan application not found' },
        { status: 404 }
      );
    }
    return HttpResponse.json(found);
  }),

  http.post('/api/v1/loans/apply', async ({ request }) => {
    const body = (await request.json()) as LoanApplyRequest;
    const vehicle =
      SEED_VEHICLES.find((v) => v.vehicle_id === body.vehicle_id) ||
      SEED_VEHICLES[0];

    const nowIso = new Date().toISOString();
    const loanId = `loan-${Date.now().toString(36)}`;
    const customerId = `cust-${Date.now().toString(36)}`;
    const journeyId = `jrn-${Date.now().toString(36)}`;
    const traceId = `trc${Date.now().toString(16)}00a1b2c3d4`;

    const metrics = calculateFinanceMetrics({
      exShowroomInr: vehicle.ex_showroom_inr,
      downPaymentInr: body.down_payment_inr,
      tenureMonths: body.tenure_months,
      interestRatePct: body.interest_rate_pct,
      monthlyIncomeInr: body.monthly_income_inr,
      existingObligationsInr: body.existing_obligations_inr || 0,
      minDownPaymentPct: vehicle.min_down_payment_pct,
    });

    const combinedText = `${body.loan_purpose_text} ${body.employment_remarks} ${body.applicant_notes}`;
    const isBlocked = containsAdversarialPrompt(combinedText);

    let finalStatus: LoanStatus = 'DISBURSED';
    if (isBlocked) {
      finalStatus = 'MODEL_ARMOR_BLOCKED';
    } else if (!metrics.foirEligible || !metrics.ltvEligible) {
      finalStatus = 'REJECTED';
    }

    const cibilScore = 768;

    const createdLoan: LoanApplication = {
      loan_id: loanId,
      customer_id: customerId,
      vehicle_id: vehicle.vehicle_id,
      utility_type: body.utility_type,
      loan_amount_inr: metrics.principalInr,
      down_payment_inr: body.down_payment_inr,
      tenure_months: body.tenure_months,
      interest_rate_pct: body.interest_rate_pct,
      emi_inr: metrics.emiInr,
      loan_purpose_text: body.loan_purpose_text,
      employment_remarks: body.employment_remarks,
      applicant_notes: body.applicant_notes,
      status: finalStatus,
      journey_id: journeyId,
      trace_id: traceId,
      total_latency_ms: isBlocked ? 145 : 1790,
      created_at: nowIso,
      updated_at: nowIso,
      customer: {
        customer_id: customerId,
        full_name: body.full_name,
        dob: body.dob,
        email: body.email,
        phone: body.phone,
        city: body.city,
        employment_type: body.employment_type,
        employer_name: body.employer_name,
        monthly_income_inr: body.monthly_income_inr,
        pan_number: body.pan_number,
        aadhaar_number: body.aadhaar_number,
        is_ai_generated: false,
        created_at: nowIso,
      },
      vehicle,
      kyc_document: {
        kyc_id: `kyc-${loanId}`,
        customer_id: customerId,
        loan_id: loanId,
        kyc_mode: body.kyc_mode,
        pan_gcs_uri:
          body.kyc_mode === 'IMAGE'
            ? `gs://general-ak-god-view-kyc-vault/specimens/${customerId}/pan.png`
            : null,
        aadhaar_gcs_uri:
          body.kyc_mode === 'IMAGE'
            ? `gs://general-ak-god-view-kyc-vault/specimens/${customerId}/aadhaar.png`
            : null,
        pan_image_model:
          body.kyc_mode === 'IMAGE' ? 'gemini-3-pro-image' : null,
        aadhaar_image_model:
          body.kyc_mode === 'IMAGE' ? 'gemini-3-pro-image' : null,
        pan_verified: !isBlocked,
        aadhaar_verified: !isBlocked,
        model_armor_text_status: isBlocked ? 'BLOCKED' : 'CLEAN',
        model_armor_findings_json: {
          template_id: 'cymbal-text-guard',
          region: 'us-central1',
          scanned_fields: [
            'loan_purpose_text',
            'employment_remarks',
            'applicant_notes',
          ],
          images_scanned: false,
          findings: isBlocked
            ? [
                {
                  category: 'PROMPT_INJECTION_AND_JAILBREAK',
                  field: 'loan_purpose_text',
                  severity: 'CRITICAL',
                  description:
                    'Adversarial instruction override pattern detected in applicant text field.',
                },
              ]
            : [],
        },
        verified_at: nowIso,
      },
      underwriting_audit: isBlocked
        ? null
        : {
            audit_id: `aud-${loanId}`,
            loan_id: loanId,
            cibil_score: cibilScore,
            cibil_bureau_ref: `CBL-${Date.now().toString().slice(-6)}`,
            foir_pct: metrics.foirPct,
            ltv_pct: metrics.ltvPct,
            rules_version: 'a94f11c',
            gke_pod_name: 'underwriting-los-svc-6d88b9f4-xk29p',
            gce_vm_name: 'core-banking-cibil-vm',
            decision: finalStatus === 'REJECTED' ? 'REJECTED' : 'APPROVED',
            rejection_reason:
              finalStatus === 'REJECTED'
                ? `Policy threshold exceeded: FOIR ${metrics.foirPct}% (limit <= 60%) or LTV ${metrics.ltvPct}% (limit <= 90%)`
                : null,
            evaluated_at: nowIso,
          },
      commercial_credit_line:
        !isBlocked &&
        finalStatus === 'DISBURSED' &&
        body.utility_type === 'COMMERCIAL_FLEET_TOPUP'
          ? {
              credit_line_id: `crd-${loanId}`,
              customer_id: customerId,
              loan_id: loanId,
              vehicle_category:
                vehicle.category === 'TWO_WHEELER'
                  ? 'TWO_WHEELER'
                  : 'COMMERCIAL_FLEET',
              fleet_size: body.fleet_size || 5,
              requested_limit_inr: body.requested_limit_inr || 500000,
              sanctioned_limit_inr: Math.round(
                (body.requested_limit_inr || 500000) * 0.9
              ),
              utilisation_pct: 32.0,
              decision: 'APPROVED',
              status: 'ACTIVE',
              trace_id: traceId,
              evaluated_at: nowIso,
            }
          : null,
      insurance_policy:
        finalStatus === 'DISBURSED'
          ? {
              policy_id: `pol-${loanId}`,
              customer_id: customerId,
              loan_id: loanId,
              policy_type: 'ZERO_DEP_COMPREHENSIVE_PLUS_CREDIT_SHIELD',
              fasttag_id: `FTAG-CYM-${Date.now().toString().slice(-8)}`,
              premium_inr: Math.round(vehicle.ex_showroom_inr * 0.018),
              coverage_inr: vehicle.ex_showroom_inr,
              status: 'ISSUED',
              trace_id: traceId,
              issued_at: nowIso,
            }
          : null,
      disbursement:
        finalStatus === 'DISBURSED'
          ? {
              disbursement_id: `dsb-${loanId}`,
              loan_id: loanId,
              sanction_letter_no: `CYM-SAN-2026-${Date.now().toString().slice(-5)}`,
              cbs_utr_number: `CYMBU26289${Date.now().toString().slice(-7)}`,
              nach_mandate_id: `NACH-CYM-${Date.now().toString().slice(-7)}`,
              disbursed_amount_inr: metrics.principalInr,
              dealer_account_no: 'DEALER-BLR-CYMBAL-004',
              status: 'DISBURSED',
              attempts: 1,
              replay_of: null,
              disbursed_at: nowIso,
            }
          : null,
    };

    loansStore.unshift(createdLoan);

    // Emit SSE telemetry events for this journey
    broadcastMockTelemetry({
      event_id: `evt-kyc-${loanId}`,
      event_time: nowIso,
      journey_id: journeyId,
      loan_id: loanId,
      customer_id: customerId,
      trace_id: traceId,
      service: 'kyc-model-armor-svc',
      operation: 'POST /internal/kyc/verify',
      event_type: isBlocked ? 'armor.blocked' : 'kyc.verified',
      status_from: 'KYC_PENDING',
      status_to: isBlocked ? 'MODEL_ARMOR_BLOCKED' : 'KYC_VERIFIED',
      duration_ms: 145,
      is_synthetic: false,
    });

    if (!isBlocked) {
      broadcastMockTelemetry({
        event_id: `evt-los-${loanId}`,
        event_time: nowIso,
        journey_id: journeyId,
        loan_id: loanId,
        customer_id: customerId,
        trace_id: traceId,
        service: 'underwriting-los-svc',
        operation: 'POST /internal/underwriting/evaluate',
        event_type: 'loan.decided',
        status_from: 'KYC_VERIFIED',
        status_to: finalStatus === 'REJECTED' ? 'REJECTED' : 'APPROVED',
        decision: finalStatus === 'REJECTED' ? 'REJECTED' : 'APPROVED',
        foir_pct: metrics.foirPct,
        cibil_score: cibilScore,
        emi_inr: metrics.emiInr,
        duration_ms: 410,
        is_synthetic: false,
      });
    }

    if (finalStatus === 'DISBURSED') {
      if (body.utility_type === 'COMMERCIAL_FLEET_TOPUP') {
        broadcastMockTelemetry({
          event_id: `evt-flt-${loanId}`,
          event_time: nowIso,
          journey_id: journeyId,
          loan_id: loanId,
          customer_id: customerId,
          trace_id: traceId,
          service: 'commercial-fleet-credit-svc',
          operation: 'POST /internal/fleet-credit/evaluate',
          event_type: 'credit_line.decided',
          status_from: 'APPROVED',
          status_to: 'APPROVED',
          decision: 'APPROVED',
          amount_inr: body.requested_limit_inr || 500000,
          duration_ms: 280,
          is_synthetic: false,
        });
      }
      broadcastMockTelemetry({
        event_id: `evt-ins-${loanId}`,
        event_time: nowIso,
        journey_id: journeyId,
        loan_id: loanId,
        customer_id: customerId,
        trace_id: traceId,
        service: 'motor-insurance-svc',
        operation: 'POST /internal/insurance/issue',
        event_type: 'insurance.issued',
        status_from: 'APPROVED',
        status_to: 'INSURED',
        amount_inr: Math.round(vehicle.ex_showroom_inr * 0.018),
        duration_ms: 320,
        is_synthetic: false,
      });
      broadcastMockTelemetry({
        event_id: `evt-cbs-${loanId}`,
        event_time: nowIso,
        journey_id: journeyId,
        loan_id: loanId,
        customer_id: customerId,
        trace_id: traceId,
        service: 'core-banking-cibil-vm',
        operation: 'POST /internal/cibil-cbs/score-and-disburse',
        event_type: 'disbursement.settled',
        status_from: 'INSURED',
        status_to: 'DISBURSED',
        cibil_score: cibilScore,
        amount_inr: metrics.principalInr,
        duration_ms: 635,
        is_synthetic: false,
      });
    }

    return HttpResponse.json(createdLoan, { status: 200 });
  }),

  http.get('/api/v1/telemetry/stream', ({ request }) => {
    const url = new URL(request.url);
    const journeyFilter = url.searchParams.get('journey_id');
    const matchingEvents = journeyFilter
      ? SEED_TELEMETRY_EVENTS.filter((ev) => ev.journey_id === journeyFilter)
      : SEED_TELEMETRY_EVENTS;
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        for (const ev of matchingEvents) {
          controller.enqueue(
            encoder.encode(`data: ${JSON.stringify(ev)}\n\n`)
          );
        }
        controller.close();
      },
    });
    return new HttpResponse(stream, {
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
      },
    });
  }),
];
