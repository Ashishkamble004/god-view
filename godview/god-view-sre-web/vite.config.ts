import type { IncomingMessage, ServerResponse } from 'node:http';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';
import { getMockCockpitState } from './src/mocks/handlers';

function readBody(req: IncomingMessage): Promise<Record<string, unknown>> {
  return new Promise((resolve) => {
    const chunks: Buffer[] = [];
    req.on('data', (chunk: Buffer) => chunks.push(chunk));
    req.on('end', () => {
      try {
        const raw = Buffer.concat(chunks).toString('utf8');
        resolve(raw ? (JSON.parse(raw) as Record<string, unknown>) : {});
      } catch {
        resolve({});
      }
    });
    req.on('error', () => resolve({}));
  });
}

function sendJson(res: ServerResponse, status: number, payload: unknown): void {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json');
  res.end(JSON.stringify(payload));
}

function cockpitMockDevPlugin() {
  return {
    name: 'cockpit-mock-api-middleware',
    configureServer(server: {
      middlewares: {
        use: (
          fn: (
            req: IncomingMessage,
            res: ServerResponse,
            next: () => void
          ) => void | Promise<void>
        ) => void;
      };
    }) {
      if (process.env.VITE_MOCK !== 'true') return;

      server.middlewares.use(async (req, res, next) => {
        const rawUrl = req.url ?? '';
        const url = rawUrl.startsWith('/v1/') ? `/api${rawUrl}` : rawUrl;
        if (!url.startsWith('/api/v1/')) {
          next();
          return;
        }

        const state = getMockCockpitState();
        const pathOnly = url.split('?')[0];
        const method = (req.method ?? 'GET').toUpperCase();

        if (method === 'GET' && pathOnly === '/api/v1/events/stream') {
          res.writeHead(200, {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            Connection: 'keep-alive',
          });
          res.write(
            `data: ${JSON.stringify({
              type: 'sovereign.heartbeat',
              data: { at: new Date().toISOString(), sentinel_cadence_s: state.perceive.sentinel_cadence_s },
            })}\n\n`
          );
          const timer = setInterval(() => {
            res.write(
              `data: ${JSON.stringify({
                type: 'sovereign.heartbeat',
                data: { at: new Date().toISOString(), sentinel_cadence_s: state.perceive.sentinel_cadence_s },
              })}\n\n`
            );
          }, 10000);
          req.on('close', () => clearInterval(timer));
          return;
        }

        if (method === 'GET' && pathOnly === '/api/v1/me') {
          sendJson(res, 200, state.me);
          return;
        }

        if (pathOnly === '/api/v1/perceive') {
          if (method === 'POST') {
            state.perceive.heartbeat_at = new Date().toISOString();
          }
          sendJson(res, 200, state.perceive);
          return;
        }

        if (method === 'GET' && pathOnly === '/api/v1/court') {
          sendJson(res, 200, { agents: state.court });
          return;
        }

        if (pathOnly.startsWith('/api/v1/court/')) {
          const tail = decodeURIComponent(pathOnly.slice('/api/v1/court/'.length));
          if (method === 'GET') {
            const found = state.court.find((a) => a.id === tail);
            if (!found) {
              sendJson(res, 404, { code: 'AGENT_NOT_FOUND', message: `Agent ${tail} not found` });
              return;
            }
            sendJson(res, 200, found);
            return;
          }
          if (method === 'POST') {
            const idx = tail.lastIndexOf(':');
            const agentId = idx !== -1 ? tail.slice(0, idx) : tail;
            const action = idx !== -1 ? tail.slice(idx + 1) : 'wake';
            const agent = state.court.find((a) => a.id === agentId);
            if (!agent) {
              sendJson(res, 404, { code: 'AGENT_NOT_FOUND', message: `Agent ${agentId} not found` });
              return;
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
            sendJson(res, 200, agent);
            return;
          }
        }

        if (method === 'POST' && pathOnly.startsWith('/api/v1/trust-records/')) {
          const tail = decodeURIComponent(pathOnly.slice('/api/v1/trust-records/'.length));
          const idx = tail.lastIndexOf(':');
          const recordId = idx !== -1 ? tail.slice(0, idx) : tail;
          const body = await readBody(req);
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
              sendJson(res, 200, rec);
              return;
            }
          }
          sendJson(res, 404, { code: 'TRUST_RECORD_NOT_FOUND', message: 'Not found' });
          return;
        }

        if (method === 'GET' && pathOnly === '/api/v1/invariants') {
          sendJson(res, 200, { invariants: state.invariants });
          return;
        }

        if (pathOnly === '/api/v1/sentinels') {
          if (method === 'GET') {
            sendJson(res, 200, { sentinels: state.sentinels });
            return;
          }
          if (method === 'POST') {
            sendJson(res, 200, state.sentinels[0]);
            return;
          }
        }

        if (method === 'PATCH' && pathOnly.startsWith('/api/v1/sentinels/')) {
          const sentinelId = decodeURIComponent(pathOnly.slice('/api/v1/sentinels/'.length));
          const body = await readBody(req);
          const s = state.sentinels.find((x) => x.sentinel_id === sentinelId);
          if (!s) {
            sendJson(res, 404, { code: 'SENTINEL_NOT_FOUND', message: 'Not found' });
            return;
          }
          if (typeof body.state === 'string') {
            s.state = body.state as typeof s.state;
          }
          sendJson(res, 200, s);
          return;
        }

        if (method === 'GET' && pathOnly === '/api/v1/journeys') {
          sendJson(res, 200, { journeys: state.journeys });
          return;
        }

        if (method === 'GET' && pathOnly === '/api/v1/incidents') {
          sendJson(res, 200, { incidents: state.incidents });
          return;
        }

        if (method === 'GET' && pathOnly.startsWith('/api/v1/incidents/')) {
          const incidentId = decodeURIComponent(pathOnly.slice('/api/v1/incidents/'.length));
          const inc = state.incidents.find((i) => i.incident_id === incidentId);
          if (!inc) {
            sendJson(res, 404, { code: 'INCIDENT_NOT_FOUND', message: 'Not found' });
            return;
          }
          sendJson(res, 200, inc);
          return;
        }

        if (method === 'GET' && pathOnly === '/api/v1/approvals') {
          sendJson(res, 200, { approvals: state.approvals });
          return;
        }

        if (method === 'POST' && pathOnly.startsWith('/api/v1/approvals/')) {
          const tail = decodeURIComponent(pathOnly.slice('/api/v1/approvals/'.length));
          const idx = tail.lastIndexOf(':');
          const approvalId = idx !== -1 ? tail.slice(0, idx) : tail;
          const action = idx !== -1 ? tail.slice(idx + 1) : 'approve';
          const card = state.approvals.find((a) => a.approval_id === approvalId);
          if (!card) {
            sendJson(res, 404, { code: 'APPROVAL_NOT_FOUND', message: 'Not found' });
            return;
          }
          if (action === 'approve') {
            card.decision = 'approved';
            card.audit_id = 'aud-20261016-0091';
          } else if (action === 'reject') {
            card.decision = 'rejected';
          } else if (action === 'snooze') {
            card.decision = 'snoozed';
          }
          card.decided_by = state.me.email;
          card.decided_at = new Date().toISOString();
          sendJson(res, 200, card);
          return;
        }

        if (method === 'GET' && pathOnly === '/api/v1/changes') {
          sendJson(res, 200, { changes: state.changes });
          return;
        }

        if (method === 'GET' && pathOnly === '/api/v1/cost') {
          sendJson(res, 200, state.cost);
          return;
        }

        if (method === 'POST' && pathOnly.startsWith('/api/v1/cost/drift/')) {
          const tail = decodeURIComponent(pathOnly.slice('/api/v1/cost/drift/'.length));
          const idx = tail.lastIndexOf(':');
          const driftId = idx !== -1 ? tail.slice(0, idx) : tail;
          const action = idx !== -1 ? tail.slice(idx + 1) : 'restore_attribute';
          const item = state.cost.drift_diffs.find((d) => d.id === driftId);
          if (!item) {
            sendJson(res, 404, { code: 'DRIFT_NOT_FOUND', message: 'Not found' });
            return;
          }
          item.status = action === 'restore_attribute' ? 'restored' : 'reconciling';
          if (action === 'restore_attribute') item.live_value = item.expected_tfstate;
          sendJson(res, 200, item);
          return;
        }

        if (method === 'GET' && pathOnly === '/api/v1/chaos/drills') {
          sendJson(res, 200, { drills: state.drills });
          return;
        }

        if (method === 'POST' && pathOnly.startsWith('/api/v1/chaos/drills/')) {
          const tail = decodeURIComponent(pathOnly.slice('/api/v1/chaos/drills/'.length));
          const idx = tail.lastIndexOf(':');
          const drillId = idx !== -1 ? tail.slice(0, idx) : tail;
          const action = idx !== -1 ? tail.slice(idx + 1) : 'start';
          const drill = state.drills.find((d) => d.drill_id === drillId);
          if (!drill) {
            sendJson(res, 404, { code: 'DRILL_NOT_FOUND', message: 'Not found' });
            return;
          }
          drill.is_active = action === 'start';
          drill.triggered_by = drill.is_active ? state.me.email : null;
          drill.activated_at = drill.is_active ? new Date().toISOString() : null;
          sendJson(res, 200, drill);
          return;
        }

        if (method === 'POST' && pathOnly === '/api/v1/tenant/kill-switch') {
          const body = await readBody(req);
          const enabled = Boolean(body.enabled);
          state.perceive.operator_state.kill_switch_enabled = enabled;
          state.perceive.operator_state.kill_switch_reason = enabled
            ? String(body.reason ?? 'Emergency operator traffic halt')
            : null;
          state.perceive.operator_state.kill_switch_actor = enabled ? state.me.email : null;
          state.perceive.operator_state.kill_switch_updated_at = new Date().toISOString();
          if (enabled) {
            state.perceive.operator_state.simulator_enabled = false;
            state.perceive.operator_state.tenant_paused = true;
          } else {
            state.perceive.operator_state.tenant_paused = false;
          }
          sendJson(res, 200, state.perceive.operator_state);
          return;
        }

        if (method === 'POST' && pathOnly === '/api/v1/tenant/pause') {
          state.perceive.operator_state.tenant_paused = true;
          state.perceive.operator_state.simulator_enabled = false;
          sendJson(res, 200, state.perceive.operator_state);
          return;
        }

        if (method === 'POST' && pathOnly === '/api/v1/tenant/resume') {
          state.perceive.operator_state.tenant_paused = false;
          state.perceive.operator_state.simulator_enabled = true;
          sendJson(res, 200, state.perceive.operator_state);
          return;
        }

        if (method === 'POST' && pathOnly === '/api/v1/hibernate') {
          const body = await readBody(req);
          const enabled = Boolean(body.enabled);
          state.perceive.operator_state.hibernated = enabled;
          state.perceive.sovereign_status = enabled ? 'hibernating' : 'healthy';
          state.perceive.sentinel_cadence_s = enabled ? 60 : 10;
          sendJson(res, 200, state.perceive.operator_state);
          return;
        }

        if (method === 'POST' && pathOnly === '/api/v1/simulator/state') {
          const body = await readBody(req);
          state.perceive.operator_state.simulator_enabled = Boolean(body.enabled);
          sendJson(res, 200, state.perceive.operator_state);
          return;
        }

        if (method === 'POST' && pathOnly === '/api/v1/simulator/journeys') {
          const body = await readBody(req);
          state.perceive.operator_state.simulator_journeys_today += Number(body.count ?? 5);
          sendJson(res, 200, state.perceive.operator_state);
          return;
        }

        next();
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), cockpitMockDevPlugin()],
  server: {
    host: '127.0.0.1',
    port: 5175,
    proxy:
      process.env.VITE_MOCK === 'true'
        ? undefined
        : {
            '/api': {
              target:
                process.env.VITE_SOVEREIGN_PROXY_TARGET ||
                'http://127.0.0.1:8080',
              changeOrigin: true,
            },
            '/v1': {
              target:
                process.env.VITE_SOVEREIGN_PROXY_TARGET ||
                'http://127.0.0.1:8080',
              changeOrigin: true,
            },
          },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/setupTests.ts'],
    css: false,
  },
});
