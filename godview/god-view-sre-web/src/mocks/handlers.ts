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

  http.get('/api/v1/topology', () => {
    const nodes = state.perceive.discovered_services.map((s, idx) => {
      let tier = 'compute';
      if (['api_gateway', 'lb_frontend', 'security_policy'].includes(s.kind)) tier = 'edge';
      else if (['cloud_sql', 'bucket', 'firestore_db', 'bigquery_dataset'].includes(s.kind)) tier = 'data';
      else if (['pubsub_topic', 'pubsub_subscription'].includes(s.kind)) tier = 'messaging';
      else if (['reasoning_engine', 'agent'].includes(s.kind)) tier = 'observer';

      const openIncidents = state.incidents.filter(
        (inc) =>
          (inc.symptom_summary && inc.symptom_summary.toLowerCase().includes(s.name.toLowerCase())) ||
          (inc.root_cause && inc.root_cause.toLowerCase().includes(s.name.toLowerCase()))
      ).length;

      return {
        id: s.id,
        name: s.name,
        kind: s.kind,
        tier,
        status: openIncidents > 0 ? 'red' : 'green',
        business: {
          domain: s.inferred_role || 'Enterprise Core Platform',
          role_summary: `${s.name} handles distributed ${s.inferred_role.toLowerCase()} operations with verified evidence and contracts.`,
          confidence: s.role_confidence || 0.94,
        },
        incidents_open: openIncidents,
        agent_id: s.owner_agent_id || null,
        golden: {
          rps: 140 + idx * 20,
          p95_ms: 16 + idx * 3,
          error_ratio: openIncidents > 0 ? 0.045 : 0.0001,
        },
        posture: {
          ingress: 'internal-and-cloud-load-balancing',
          iap: true,
          invoker_iam: true,
          waf: openIncidents > 0 ? 'sensitivity-4' : 'sensitivity-1',
        },
      };
    });

    const edges = state.perceive.discovered_edges.map((e, idx) => ({
      id: e.id || `edge-${idx}`,
      src: e.src,
      dst: e.dst,
      kind: e.protocol || 'http',
      evidence_kind: e.evidence || 'trace',
      rps: 42 + idx * 8,
      error_ratio: 0.0001,
      confidence: 0.96,
    }));

    const journeys = state.journeys.map((j) => ({
      name: j.name,
      steps: j.steps.map((st) => ({
        node: st.service,
        operation: st.operation,
      })),
      health: (j.status === 'degraded' || j.status === 'stalled' ? 'amber' : 'green') as any,
      confidence: 0.92,
    }));

    return HttpResponse.json({
      env_id: state.perceive.env_id,
      generated_at: new Date().toISOString(),
      nodes,
      edges,
      journeys,
      layout_hint: {
        groups: [
          { tier: 'edge', node_ids: nodes.filter((n) => n.tier === 'edge').map((n) => n.id) },
          { tier: 'compute', node_ids: nodes.filter((n) => n.tier === 'compute').map((n) => n.id) },
          { tier: 'data', node_ids: nodes.filter((n) => n.tier === 'data').map((n) => n.id) },
        ],
      },
    });
  }),

  http.get('/api/v1/business-context/:nodeId', ({ params }) => {
    const rawId = decodeURIComponent(String(params.nodeId));
    const s = state.perceive.discovered_services.find((svc) => svc.id === rawId || svc.name === rawId) || state.perceive.discovered_services[0];
    const relatedIncidents = state.incidents.filter(
      (inc) =>
        (inc.symptom_summary && inc.symptom_summary.toLowerCase().includes(s.name.toLowerCase())) ||
        (inc.root_cause && inc.root_cause.toLowerCase().includes(s.name.toLowerCase()))
    );

    return HttpResponse.json({
      role_summary: `${s.name} coordinates and executes distributed operations within the ${s.inferred_role || 'Enterprise'} subsystem. All behavior verified via live telemetry.`,
      domain: s.inferred_role || 'Enterprise Financial Core',
      capabilities: s.routes.length > 0 ? s.routes : ['POST /v1/process', 'GET /v1/healthz', 'POST /v1/reconcile'],
      upstream: state.perceive.discovered_edges.filter((e) => e.dst === s.id).map((e) => e.src),
      downstream: state.perceive.discovered_edges.filter((e) => e.src === s.id).map((e) => e.dst),
      journeys: state.journeys
        .filter((j) => j.steps.some((st) => st.service === s.id))
        .map((j) => ({
          name: j.name,
          steps: j.steps.map((st) => ({ service: st.service, operation: st.operation })),
          confidence: 0.95,
          evidence: { kind: 'trace', ref: j.journey_key },
        })),
      kpis: [
        {
          name: 'Request Throughput',
          definition: 'sum(rate(http_requests_total[5m]))',
          unit: 'req/sec',
          confidence: 0.98,
          evidence: { kind: 'metric', ref: 'prometheus/run_timeseries' },
          value: 184.2,
        },
        {
          name: 'p95 Execution Latency',
          definition: 'histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])))',
          unit: 'ms',
          confidence: 0.95,
          evidence: { kind: 'trace', ref: 'cloudtrace/p95' },
          value: 38.6,
        },
        {
          name: 'Invariant Conformance',
          definition: 'verified_executions / total_executions',
          unit: '%',
          confidence: 0.99,
          evidence: { kind: 'ledger', ref: 'bigquery/conformance' },
          value: 99.8,
        },
      ],
      invariants: [
        {
          statement: 'Every mutated record must emit a verifiable audit ledger event',
          check: 'ledger.verify_audit_event(event_id) == true',
          severity: 'HIGH',
          confidence: 0.96,
          evidence: { kind: 'openapi', ref: 'contracts/spec.json' },
          status: 'verified',
        },
        {
          statement: 'Outbound traffic must pass through the verified secure gateway NEG',
          check: 'network.egress_route == verified_gateway',
          severity: 'MEDIUM',
          confidence: 0.91,
          evidence: { kind: 'env', ref: 'EGRESS_GATEWAY_URL' },
          status: 'verified',
        },
      ],
      risk_notes: [
        'Cloud Armor WAF rules require periodic sensitivity evaluation to prevent valid JSON payload blocks.',
        'High memory watermark observed during peak ingestion bursts.',
      ],
      confidence: s.role_confidence || 0.94,
      evidence: [
        { kind: 'openapi', ref: `${s.name}/openapi.json`, excerpt: 'endpoints: /v1/process, /v1/validate' },
        { kind: 'trace', ref: 'projects/env-prod/traces/tr-88419', excerpt: 'Parent span calls downstream auth service' },
        { kind: 'env', ref: 'CONTAINER_ENV_VARS', excerpt: 'DB_HOST=10.0.1.4, PORT=8080' },
      ],
      inferred_by: 'gemini-4-argon@global',
      inferred_at: new Date().toISOString(),
      recent_incidents: relatedIncidents.map((i) => ({
        id: i.incident_id,
        title: i.symptom_summary,
        severity: i.severity,
        status: i.outcome,
      })),
    });
  }),

  http.get('/api/v1/harness/:agentId/tree', ({ params }) => {
    const rawId = decodeURIComponent(String(params.agentId));
    return HttpResponse.json({
      agent_id: rawId,
      version: 'v1.2-active',
      active: true,
      accuracy_gain_pct: 18.4,
      tree: {
        id: 'root-decision-tree',
        label: 'Incident Triage & Heuristic Synthesis',
        type: 'condition',
        description: 'Analyzes incoming incident evidence pack and determines remediation path',
        confidence: 0.94,
        version: 'v1.2',
        children: [
          {
            id: 'branch-waf-block',
            label: 'Cloud Armor WAF False Positive Detected',
            type: 'heuristic',
            description: 'Rule 1000 triggered on legitimate application JSON payload',
            confidence: 0.96,
            children: [
              {
                id: 'action-propose-waf-mitigation',
                label: 'Propose armor.set_waf_sensitivity (Level 2 Approval)',
                type: 'action',
                description: 'Set sensitivity 1 with preview mode first to avoid downtime',
                confidence: 0.98,
                children: [
                  {
                    id: 'verify-probe-outcome',
                    label: 'Synthetic POST Probe Verification',
                    type: 'verification',
                    description: 'Verify 422 Unprocessable Entity instead of 403 Forbidden',
                    confidence: 0.99,
                  },
                ],
              },
            ],
          },
          {
            id: 'branch-latency-breach',
            label: 'Downstream Service Latency SLA Breach',
            type: 'heuristic',
            description: 'p95 latency exceeded 200ms threshold on external dependency',
            confidence: 0.88,
            children: [
              {
                id: 'action-scale-workload',
                label: 'Propose run.scale_instances or Warm Standby',
                type: 'action',
                description: 'Adjust min_instances to absorb burst traffic',
                confidence: 0.91,
              },
            ],
          },
        ],
      },
      skills: [
        {
          id: 'skill-waf-paranoia-mitigation',
          name: 'Cloud Armor WAF Sensitivity Tuning',
          fitness_score: 0.96,
          description: 'Autonomously identifies CRS rule false positives on JSON POST payloads and calculates optimal sensitivity.',
        },
        {
          id: 'skill-distributed-trace-correlation',
          name: 'Distributed Trace Anomaly Isolation',
          fitness_score: 0.94,
          description: 'Correlates multi-hop OpenTelemetry spans to pinpoint lock contention and slow database queries.',
        },
      ],
    });
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
