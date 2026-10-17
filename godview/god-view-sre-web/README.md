# God-View Cockpit (`god-view-sre-web`)

Operator control room UI for the Autonomous SRE & Software Evolution Organism (URL 3, deployed behind IAP).

## Stack

- **Runtime / UI**: Vite 5, React 18, TypeScript (`strict: true`), Tailwind CSS 3, `lucide-react`, `@fontsource/inter`, `react-markdown`
- **Testing & Mocks**: Vitest, `@testing-library/react`, MSW (`msw/node` for tests and Vite dev middleware for `npm run dev:mock`)
- **Production Container**: `nginx:1.27-alpine` listening on port `8080` with `/healthz`, gzip compression, immutable asset caching, and SPA history fallback

## Hash-Routed Views

- `#organism` — Court org-chart SVG/CSS diagram (Sovereign at top, court agents grouped by type with trust level per action type, warmth state, last wake, tokens today)
- `#agent/<id>` — Living semantic model (`entities`, `rules`, `state_machines`, `routes`, `dependencies`, `decision_boundaries`, `recent_commits`) + `SKILL.md` markdown viewer and 5-level Trust Ladder (`L0..L4`) with explicit operator `whitelisted` gate
- `#invariants` — Business invariant board (`proposed | shadow | enforced | retired`), provenance, 7-day shadow evaluation telemetry, revision history, and `INVARIANT.md` markdown renderer + active sentinels
- `#journeys` — Process mining DAG from `directly_follows` transitions, conformance violation table, funnel conversion KPIs, and `₹ at risk`
- `#incidents` / `#incident/<id>` — Incident timeline, `EvidencePack` signals/commits/blast radius, multi-round swarm transcript (`proposal`, `critique`, `handoff`, `synthesis`, `verdict`), `ConvergenceRecord`, and actions with `audit_id`
- `#approvals` — Operator inbox rendering `ApprovalCard` items with narrative, `₹ impact`, reversal recipe, and `Approve` / `Reject` / `Snooze` actions (`POST /api/v1/approvals/{id}:approve|reject|snooze`)
- `#changes` — Pull request reviews with risk score, business summary, impacted journeys/invariants, and canary verdicts
- `#cost` — Attribution findings (`cost_findings`) and Terraform drift reconciliation (`tf.drift_diff`)
- `#controls` — Operator controls: two-step red Traffic Kill Switch (`POST /api/v1/tenant/kill-switch`), Tenant Pause/Resume, Sovereign Hibernate/Wake, Journey Simulator controls, and Chaos Drills Drawer (`POST /api/v1/chaos/drills/{id}:start|stop`)

## Commands

```bash
# Install dependencies
npm ci

# Typecheck
npm run typecheck

# Run unit & integration tests (Vitest + MSW)
npm test

# Run local development server with randomised MSW fixtures on http://127.0.0.1:5175
npm run dev:mock

# Build static production bundle into dist/
npm run build
```
