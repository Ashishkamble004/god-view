import { http, HttpResponse } from 'msw';
import {
  SEED_IAP_USER,
  SEED_LOANS,
  SEED_TELEMETRY_EVENTS,
  SEED_TOPOLOGY,
  getSeedKycSignedUrl,
  getSeedTraceRecord,
} from './data';

export const handlers = [
  http.get('/api/v1/me', () => {
    if (
      typeof window !== 'undefined' &&
      window.location.hash.includes('session-expired')
    ) {
      return HttpResponse.json(
        { email: '', iap_authenticated: false },
        { status: 401 }
      );
    }
    return HttpResponse.json(SEED_IAP_USER);
  }),

  http.get('/api/v1/loans', ({ request }) => {
    if (
      typeof window !== 'undefined' &&
      window.location.hash.includes('session-expired')
    ) {
      return HttpResponse.json(
        {
          code: 'IAP_SESSION_EXPIRED',
          message: 'IAP session expired or missing X-Goog-Authenticated-User-Email header.',
        },
        { status: 401 }
      );
    }
    const url = new URL(request.url);
    const statusFilter = url.searchParams.get('status');
    const utilityFilter = url.searchParams.get('utility_type');
    let results = [...SEED_LOANS];
    if (statusFilter && statusFilter !== 'ALL') {
      results = results.filter((l) => l.status === statusFilter);
    }
    if (utilityFilter && utilityFilter !== 'ALL') {
      results = results.filter((l) => l.utility_type === utilityFilter);
    }
    return HttpResponse.json(results);
  }),

  http.get('/api/v1/loans/:loanId', ({ params }) => {
    const found = SEED_LOANS.find((l) => l.loan_id === params.loanId);
    if (!found) {
      return HttpResponse.json(
        { code: 'NOT_FOUND', message: `Loan ${String(params.loanId)} not found.` },
        { status: 404 }
      );
    }
    return HttpResponse.json(found);
  }),

  http.get('/api/v1/kyc-documents/:customerId/:docType', ({ params }) => {
    const customerId = String(params.customerId);
    const docType = params.docType === 'aadhaar' ? 'aadhaar' : 'pan';
    return HttpResponse.json(getSeedKycSignedUrl(customerId, docType));
  }),

  http.get('/api/v1/observability/topology', () => {
    return HttpResponse.json(SEED_TOPOLOGY);
  }),

  http.get('/api/v1/observability/traces/:traceId', ({ params }) => {
    return HttpResponse.json(getSeedTraceRecord(String(params.traceId)));
  }),

  http.get('/api/v1/telemetry/stream', () => {
    const body = SEED_TELEMETRY_EVENTS.map(
      (ev) => `data: ${JSON.stringify(ev)}\n\n`
    ).join('');
    return new HttpResponse(body, {
      status: 200,
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
      },
    });
  }),
];
