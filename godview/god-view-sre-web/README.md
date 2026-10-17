# God-View Cockpit (`god-view-sre-web`)

Operator control room UI for the Autonomous SRE & Software Evolution Organism (URL 3, deployed behind IAP at `godview.<domain>`).

## Stack

- **Runtime / UI**: Vite 6, React 18, TypeScript (`strict: true`), Three.js (`three` 0.177 with `OrbitControls`), Tailwind CSS 3, `lucide-react`, `@fontsource/inter`, `react-markdown`
- **Testing & Mocks**: Vitest, `@testing-library/react`, MSW (`msw/node` for tests and Vite dev middleware for `npm run dev:mock`)
- **Production Container**: `nginx:1.27-alpine` listening on port `8080` with `/healthz`, gzip compression, immutable asset caching, and SPA history fallback

## Views & Hash Routes

- `#globe` *(Default)* — **God-View 3D Animated Globe Cockpit**:
  - Full-bleed Three.js 3D sphere with subtle meridians and parallels.
  - Architectural tier latitudinal bands (`observer` +78°, `edge` +55°, `messaging` +35°, `compute` +8°, `data` -42°, `platform` -68°, `external` orbiting satellites).
  - Domain longitudinal angular clustering.
  - Great-circle tube arcs with animated traffic flow pulses.
  - Incident pulsing alert beacons.
  - Slim left rail (environment, active swarm agents, token budget, tier toggles, and live search).
  - Right slide-out **Business Context Inspector Drawer** for selected services (role summary, domain, golden signals KPI matrix with honesty rule, security posture & invariants, journeys, evidence, and incident war-room links).
  - Accessible 2D Architectural Tier Matrix fallback for `prefers-reduced-motion` and headless environments.
- `#organism` — Court org-chart SVG/CSS diagram (Sovereign at top, court agents grouped by type with trust level per action type, warmth state, last wake, tokens today).
- `#agent/<id>` — Living semantic model (`entities`, `rules`, `state_machines`, `routes`, `dependencies`, `decision_boundaries`, `recent_commits`) + `SKILL.md` markdown viewer and 5-level Trust Ladder (`L0..L4`) with explicit operator `whitelisted` gate.
- `#invariants` — Business invariant board (`proposed | shadow | enforced | retired`), provenance, 7-day shadow evaluation telemetry, revision history, and `INVARIANT.md` markdown renderer + active sentinels.
- `#journeys` — Process mining DAG from `directly_follows` transitions, conformance violation table, funnel conversion KPIs, and `₹ at risk`.
- `#incidents` / `#incident/<id>` — Incident timeline, `EvidencePack` signals/commits/blast radius, multi-round swarm transcript (`proposal`, `critique`, `handoff`, `synthesis`, `verdict`), `ConvergenceRecord`, and actions with `audit_id`.
- `#approvals` — Operator inbox rendering `ApprovalCard` items with narrative, `₹ impact`, reversal recipe, and `Approve` / `Reject` / `Snooze` actions (`POST /api/v1/approvals/{id}:approve|reject|snooze`).
- `#changes` — Pull request reviews with risk score, business summary, impacted journeys/invariants, and canary verdicts.
- `#cost` — Attribution findings (`cost_findings`) and Terraform drift reconciliation (`tf.drift_diff`).
- `#controls` — Operator controls: two-step red Traffic Kill Switch (`POST /api/v1/tenant/kill-switch`), Tenant Pause/Resume, Sovereign Hibernate/Wake, Journey Simulator controls, and Chaos Drills Drawer (`POST /api/v1/chaos/drills/{id}:start|stop`).

## Jetski UI Plugin (AuxPane Sidecar)

The Cockpit is also packaged as an interactive Jetski UI plugin, enabling operators to launch the live 3D globe directly inside Jetski's Auxiliary Pane (AuxPane):

- **Plugin Directory**: `~/.gemini/config/plugins/godview_cockpit/`
- **Sidecar Component**: `sidecars/globe/` (`main.mjs` running via Node.js Sidecar SDK)
- **Features**:
  - Serves static assets directly from `dist/`.
  - Proxies `/api/*` to the Sovereign backend (`GODVIEW_SOVEREIGN_URL` or default `https://god-view-sovereign-mcj3w7ujpq-uc.a.run.app`).
  - Automatically fetches and caches (50 min) Google Cloud ID tokens via `gcloud auth print-identity-token` and adds `Authorization: Bearer <token>`.
  - Supports `?mode=mock` query parameter or automatic fallback to offline MSW fixture shapes when Sovereign is unreachable.
  - Automatically adopts host CSS theme tokens (`--background`, `--foreground`, `--primary`, `--card`, `--border`).

### Enabling the UI Plugin in Jetski Web

1. Open Jetski Web and navigate to the **UI Plugins** tab on the left sidebar.
2. Find **God-View Globe Cockpit** (`godview_cockpit`) and toggle it to **Enabled**.
3. In any conversation, click the **+** (Plugins) icon in the conversation header.
4. Select **God-View Globe** to open the 3D globe cockpit in the right-hand AuxPane.

## Development & Build Commands

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

# Preview production build locally on port 8080
npm run preview
```
