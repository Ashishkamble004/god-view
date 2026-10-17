import * as fs from 'fs';
import * as path from 'path';
import { describe, expect, it } from 'vitest';
import { resetMockCockpitState } from '../mocks/handlers';
import { buildTopologyFallback } from '../api/client';

describe('Export Fixtures for Plugin Sidecar', () => {
  it('exports mock dataset to json for sidecar mock mode', () => {
    const dataset = resetMockCockpitState(42);
    const topology = buildTopologyFallback(dataset.perceive, dataset.incidents, dataset.journeys);
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
