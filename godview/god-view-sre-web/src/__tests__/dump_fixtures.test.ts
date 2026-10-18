import * as fs from 'fs';
import * as path from 'path';
import { describe, expect, it } from 'vitest';
import { resetMockCockpitState } from '../mocks/handlers';

describe('Export Fixtures for Plugin Sidecar', () => {
  it('exports mock dataset to json for sidecar mock mode', () => {
    const dataset = resetMockCockpitState(42);
    const nodes = dataset.perceive.discovered_services.map((s, idx) => ({
      id: s.id,
      name: s.name,
      kind: s.kind,
      tier: (idx % 2 === 0 ? 'compute' : 'edge') as any,
      status: 'green' as const,
      business: {
        domain: s.inferred_role || 'Enterprise Core Platform',
        role_summary: `${s.name} handles distributed operations with verified evidence.`,
        confidence: s.role_confidence || 0.94,
      },
      incidents_open: 0,
      agent_id: s.owner_agent_id || null,
      golden: {
        rps: idx === 0 ? null : 120 + idx * 25,
        p95_ms: idx === 0 ? null : 18 + idx * 4,
        error_ratio: 0.0001,
      },
      posture: {
        ingress: 'internal',
        iap: true,
        invoker_iam: true,
        waf: 'sensitivity-1',
      },
    }));
    const edges = dataset.perceive.discovered_edges.map((e, idx) => ({
      id: e.id || `edge-${idx}`,
      src: e.src,
      dst: e.dst,
      kind: e.protocol || 'http',
      evidence_kind: e.evidence || 'trace',
      rps: 45 + idx * 10,
      error_ratio: 0.0001,
      confidence: 0.96,
    }));
    const topology = {
      env_id: dataset.perceive.env_id,
      generated_at: new Date().toISOString(),
      nodes,
      edges,
      journeys: dataset.journeys.map((j) => ({
        name: j.name,
        steps: j.steps.map((st) => ({ node: st.service, operation: st.operation })),
        health: 'green' as const,
        confidence: 0.94,
      })),
      layout_hint: {
        groups: [
          { tier: 'edge', node_ids: nodes.filter((n) => n.tier === 'edge').map((n) => n.id) },
          { tier: 'compute', node_ids: nodes.filter((n) => n.tier === 'compute').map((n) => n.id) },
        ],
      },
    };
    const payload = {
      me: dataset.me,
      perceive: dataset.perceive,
      topology,
      court: dataset.court,
      invariants: dataset.invariants,
      sentinels: dataset.sentinels,
      journeys: dataset.journeys,
      incidents: dataset.incidents,
      approvals: dataset.approvals,
      changes: dataset.changes,
      cost: dataset.cost,
    };

    const outPath = path.resolve(__dirname, '../../dist/mock-fixtures.json');
    fs.mkdirSync(path.dirname(outPath), { recursive: true });
    fs.writeFileSync(outPath, JSON.stringify(payload, null, 2), 'utf-8');
    expect(fs.existsSync(outPath)).toBe(true);
  });
});
