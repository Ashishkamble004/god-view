import { http, HttpResponse } from 'msw';
import type { CockpitDataset, SentinelDefinition } from '../api/types';
import { createRandomCockpitDataset } from './fixtures';

let state: CockpitDataset = createRandomCockpitDataset(42);

export function resetMockCockpitState(seed = 42): CockpitDataset {
  state = structuredClone(createRandomCockpitDataset(seed));
  return state;
}

export function getMockCockpitState(): CockpitDataset {
  return state;
}

export const handlers = [
  http.get('/api/v1/me', () => {
    return HttpResponse.json(state.me);
  }),

  http.get('/api/v1/perceive', () => {
    return HttpResponse.json(state.perceive);
  }),

  http.post('/api/v1/perceive', async () => {
    state.perceive.heartbeat_at = new Date().toISOString();
    return HttpResponse.json(state.perceive);
  }),

  http.get('/api/v1/court', () => {
    return HttpResponse.json({ agents: state.court });
  }),

  http.get('/api/v1/court/:agentId', ({ params }) => {
    const rawId = decodeURIComponent(String(params.agentId));
    const found = state.court.find((a) => a.id === rawId);
    if (!found) {
      return HttpResponse.json(
        { code: 'AGENT_NOT_FOUND', message: `Agent ${rawId} not found in court` },
        { status: 404 }
      );
    }
    return HttpResponse.json(found);
  }),

  http.post('/api/v1/court/:agentAction', ({ params }) => {
    const rawParam = decodeURIComponent(String(params.agentAction));
    const lastColon = rawParam.lastIndexOf(':');
    const agentId = lastColon !== -1 ? rawParam.slice(0, lastColon) : rawParam;
    const action = lastColon !== -1 ? rawParam.slice(lastColon + 1) : 'wake';
    const agent = state.court.find((a) => a.id === agentId);
    if (!agent) {
      return HttpResponse.json(
        { code: 'AGENT_NOT_FOUND', message: `Agent ${agentId} not found` },
        { status: 404 }
      );
    }
    if (action === 'wake') {
      agent.state = 'awake';
      agent.last_wake_at = new Date().toISOString();
      agent.wakes_7d += 1;
    } else if (action === 'warm') {
      agent.warmth_min_instances = 1;
    } else if (action === 'cool') {
      agent.warmth_min_instances = 0;
    } else if (action === 'retire') {
      agent.state = 'retiring';
      agent.warmth_min_instances = 0;
      agent.retired_at = new Date().toISOString();
    }
    return HttpResponse.json(agent);
  }),

  http.post('/api/v1/trust-records/:recordAction', async ({ params, request }) => {
    const rawParam = decodeURIComponent(String(params.recordAction));
    const lastColon = rawParam.lastIndexOf(':');
    const recordId = lastColon !== -1 ? rawParam.slice(0, lastColon) : rawParam;
    const body = (await request.json().catch(() => ({}))) as { whitelisted?: boolean };

    for (const agent of state.court) {
      const rec = agent.trust_records.find((r) => r.id === recordId);
      if (rec) {
        rec.whitelisted = Boolean(body.whitelisted);
        rec.whitelisted_by = rec.whitelisted ? state.me.email : null;
        if (rec.whitelisted && rec.counters.business_successes >= rec.thresholds.n3) {
          rec.level = 4;
        } else if (!rec.whitelisted && rec.level === 4) {
          rec.level = 3;
        }
        return HttpResponse.json(rec);
      }
    }
    return HttpResponse.json(
      { code: 'TRUST_RECORD_NOT_FOUND', message: `Trust record ${recordId} not found` },
      { status: 404 }
    );
  }),

  http.get('/api/v1/invariants', () => {
    return HttpResponse.json({ invariants: state.invariants });
  }),

  http.get('/api/v1/sentinels', () => {
    return HttpResponse.json({ sentinels: state.sentinels });
  }),

  http.post('/api/v1/sentinels', async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as { invariant_id?: string };
    const inv = state.invariants.find((i) => i.id === body.invariant_id);
    const created: SentinelDefinition = {
      sentinel_id: inv?.sentinel_id ?? `snt-invariant_sql-${Date.now()}`,
      class: 'invariant_sql',
      source_ref: inv?.id ?? 'manual',
      check: {
        dialect: inv?.check.dialect ?? 'bigquery',
        statement: inv?.check.statement ?? 'SELECT 0',
      },
      cadence_s: 10,
      baseline: { model: 'same_hour', window_days: 7 },
      wake_condition: {
        operator: inv?.wake_condition.operator ?? '>=',
        threshold: inv?.wake_condition.threshold ?? 1,
        consecutive: inv?.wake_condition.consecutive ?? 1,
        window_s: inv?.check.window_s ?? 300,
      },
      target_agent_id: inv?.wake_condition.target_agent_id ?? state.court[0]?.id ?? 'sovereign',
      state: 'enforced',
      compiled_by: state.me.email,
      compiled_at: new Date().toISOString(),
      last_run_at: new Date().toISOString(),
      last_result: { count: 0, breach: false },
      consecutive_breaches: 0,
    };
    const existingIdx = state.sentinels.findIndex((s) => s.sentinel_id === created.sentinel_id);
    if (existingIdx >= 0) {
      state.sentinels[existingIdx] = created;
    } else {
      state.sentinels.push(created);
    }
    if (inv) {
      inv.status = 'enforced';
    }
    return HttpResponse.json(created);
  }),

  http.patch('/api/v1/sentinels/:sentinelId', async ({ params, request }) => {
    const sentinelId = decodeURIComponent(String(params.sentinelId));
    const body = (await request.json().catch(() => ({}))) as {
      state?: SentinelDefinition['state'];
    };
    const sentinel = state.sentinels.find((s) => s.sentinel_id === sentinelId);
    if (!sentinel) {
      return HttpResponse.json(
        { code: 'SENTINEL_NOT_FOUND', message: `Sentinel ${sentinelId} not found` },
        { status: 404 }
      );
    }
    if (body.state) {
      sentinel.state = body.state;
    }
    return HttpResponse.json(sentinel);
  }),

  http.get('/api/v1/journeys', () => {
    return HttpResponse.json({ journeys: state.journeys });
  }),

  http.get('/api/v1/incidents', () => {
    return HttpResponse.json({ incidents: state.incidents });
  }),

  http.get('/api/v1/incidents/:incidentId', ({ params }) => {
    const incidentId = decodeURIComponent(String(params.incidentId));
    const inc = state.incidents.find((i) => i.incident_id === incidentId);
    if (!inc) {
      return HttpResponse.json(
        { code: 'INCIDENT_NOT_FOUND', message: `Incident ${incidentId} not found` },
        { status: 404 }
      );
    }
    return HttpResponse.json(inc);
  }),

  http.get('/api/v1/approvals', () => {
    return HttpResponse.json({ approvals: state.approvals });
  }),

  http.post('/api/v1/approvals/:approvalAction', async ({ params }) => {
    const rawParam = decodeURIComponent(String(params.approvalAction));
    const lastColon = rawParam.lastIndexOf(':');
    const approvalId = lastColon !== -1 ? rawParam.slice(0, lastColon) : rawParam;
    const action = lastColon !== -1 ? rawParam.slice(lastColon + 1) : 'approve';

    const card = state.approvals.find((a) => a.approval_id === approvalId);
    if (!card) {
      return HttpResponse.json(
        { code: 'APPROVAL_NOT_FOUND', message: `Approval ${approvalId} not found` },
        { status: 404 }
      );
    }

    if (action === 'approve') {
      card.decision = 'approved';
      card.decided_by = state.me.email;
      card.decided_at = new Date().toISOString();
      card.audit_id = `aud-20261016-009${state.approvals.indexOf(card) + 1}`;
    } else if (action === 'reject') {
      card.decision = 'rejected';
      card.decided_by = state.me.email;
      card.decided_at = new Date().toISOString();
    } else if (action === 'snooze') {
      card.decision = 'snoozed';
      card.decided_by = state.me.email;
      card.decided_at = new Date().toISOString();
    }

    return HttpResponse.json(card);
  }),

  http.get('/api/v1/changes', () => {
    return HttpResponse.json({ changes: state.changes });
  }),

  http.get('/api/v1/cost', () => {
    return HttpResponse.json(state.cost);
  }),

  http.post('/api/v1/cost/drift/:driftAction', ({ params }) => {
    const rawParam = decodeURIComponent(String(params.driftAction));
    const lastColon = rawParam.lastIndexOf(':');
    const driftId = lastColon !== -1 ? rawParam.slice(0, lastColon) : rawParam;
    const action = lastColon !== -1 ? rawParam.slice(lastColon + 1) : 'restore_attribute';

    const item = state.cost.drift_diffs.find((d) => d.id === driftId);
    if (!item) {
      return HttpResponse.json(
        { code: 'DRIFT_NOT_FOUND', message: `Drift item ${driftId} not found` },
        { status: 404 }
      );
    }

    if (action === 'restore_attribute') {
      item.status = 'restored';
      item.live_value = item.expected_tfstate;
    } else {
      item.status = 'reconciling';
    }
    return HttpResponse.json(item);
  }),

  http.get('/api/v1/chaos/drills', () => {
    return HttpResponse.json({ drills: state.drills });
  }),

  http.post('/api/v1/chaos/drills/:drillAction', ({ params }) => {
    const rawParam = decodeURIComponent(String(params.drillAction));
    const lastColon = rawParam.lastIndexOf(':');
    const drillId = lastColon !== -1 ? rawParam.slice(0, lastColon) : rawParam;
    const action = lastColon !== -1 ? rawParam.slice(lastColon + 1) : 'start';

    const drill = state.drills.find((d) => d.drill_id === drillId);
    if (!drill) {
      return HttpResponse.json(
        { code: 'DRILL_NOT_FOUND', message: `Drill ${drillId} not found` },
        { status: 404 }
      );
    }

    if (action === 'start') {
      drill.is_active = true;
      drill.triggered_by = state.me.email;
      drill.activated_at = new Date().toISOString();
      drill.expires_at = new Date(Date.now() + 30 * 60 * 1000).toISOString();
    } else {
      drill.is_active = false;
      drill.triggered_by = null;
      drill.activated_at = null;
      drill.expires_at = null;
    }
    return HttpResponse.json(drill);
  }),

  http.post('/api/v1/tenant/kill-switch', async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as {
      enabled?: boolean;
      reason?: string;
    };
    const enabled = Boolean(body.enabled);
    state.perceive.operator_state.kill_switch_enabled = enabled;
    state.perceive.operator_state.kill_switch_reason = enabled
      ? body.reason || 'Emergency operator traffic halt'
      : null;
    state.perceive.operator_state.kill_switch_actor = enabled ? state.me.email : null;
    state.perceive.operator_state.kill_switch_updated_at = new Date().toISOString();

    if (enabled) {
      state.perceive.operator_state.simulator_enabled = false;
      state.perceive.operator_state.tenant_paused = true;
      for (const d of state.drills) {
        d.is_active = false;
      }
      for (const apr of state.approvals) {
        if (apr.decision === 'pending' && apr.trust_level_required <= 2) {
          apr.decision = 'expired';
        }
      }
    } else {
      state.perceive.operator_state.tenant_paused = false;
    }

    return HttpResponse.json(state.perceive.operator_state);
  }),

  http.post('/api/v1/tenant/pause', () => {
    state.perceive.operator_state.tenant_paused = true;
    state.perceive.operator_state.simulator_enabled = false;
    for (const agent of state.court) {
      agent.warmth_min_instances = 0;
    }
    return HttpResponse.json(state.perceive.operator_state);
  }),

  http.post('/api/v1/tenant/resume', () => {
    state.perceive.operator_state.tenant_paused = false;
    state.perceive.operator_state.simulator_enabled = true;
    return HttpResponse.json(state.perceive.operator_state);
  }),

  http.post('/api/v1/hibernate', async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as { enabled?: boolean };
    const enabled = Boolean(body.enabled);
    state.perceive.operator_state.hibernated = enabled;
    state.perceive.sovereign_status = enabled ? 'hibernating' : 'healthy';
    state.perceive.sentinel_cadence_s = enabled ? 60 : 10;
    if (enabled) {
      state.perceive.operator_state.simulator_enabled = false;
      for (const agent of state.court) {
        agent.warmth_min_instances = 0;
      }
    }
    return HttpResponse.json(state.perceive.operator_state);
  }),

  http.post('/api/v1/simulator/state', async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as { enabled?: boolean };
    state.perceive.operator_state.simulator_enabled = Boolean(body.enabled);
    return HttpResponse.json(state.perceive.operator_state);
  }),

  http.post('/api/v1/simulator/journeys', async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as { count?: number };
    const count = Math.max(1, Number(body.count ?? 5));
    state.perceive.operator_state.simulator_journeys_today += count;
    return HttpResponse.json(state.perceive.operator_state);
  }),
];
