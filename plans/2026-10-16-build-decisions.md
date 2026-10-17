# Build Phase — Coordinator Decisions, Engineering Rules & UI/UX Rules

- **Date**: 2026-10-16 (evening IST) · **Applies to**: every build-swarm worker · **Authority**: coordinator, acting on Ashish's decisions
- **Source of truth order**: `TDD.md` → `docs/GOD_VIEW_MCP_AGENT_SPEC.md` → `docs/TERRAFORM_GITOPS_SPEC.md` → `docs/SDD.md` → `docs/BRD.md` → `plans/2026-10-16-god-view-organism-design.md`. If this file conflicts with those, this file wins only for the open points listed in §A (it closes them); everything else defers to the documents.

---

## A. Open points closed by the coordinator (consolidated from the five documents)

| # | Open point | Decision |
| :-- | :-- | :-- |
| A1 | Reasoning Engine quota | `non_regional_reasoning_engine_service_entities` = 100 per project, 29 used → no increase needed for v1. Court Manager hard-caps the court at 40 agents (`GODVIEW_COURT_MAX_AGENTS=40`). |
| A2 | API Gateway → `loan-orchestrator-api` ingress | Orchestrator Cloud Run ingress = `INGRESS_TRAFFIC_ALL`; `roles/run.invoker` granted ONLY to `godview-apigw-sa` (gateway), `godview-runtime-sa` (tenant internals), `godview-sense-sa` (`/metrics` scrape), `godview-act-sa` (`/admin/v1`). No `allUsers`. All other tenant services: `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` is NOT required; use `INTERNAL` ingress (VPC-only) + IAM. |
| A3 | Sovereign machine-to-machine reachability & MCP hosting | Cloud Scheduler, Chat callbacks and Agent Engine agents call the Sovereign's `*.run.app` URL with OIDC ID tokens (ingress ALL, IAM-only: `godview-sense-sa`, the Scheduler SA, the Chat app SA). Humans reach it only through the LB + IAP at `godview.ak-demos.com/api/*`. **MCP servers run as stdio subprocesses inside the Sovereign container image** (Node 22 + Go binaries baked in); **court agents never host MCP servers** — they invoke capabilities through the Sovereign's IAM-only endpoint `POST /v1/capabilities/{capability_id}:invoke` (body: args + agent identity + incident id), which is the single TrustGate enforcement point. Add this endpoint to the Sovereign control API. |
| A4 | Terraform provider support checks | `infra-platform` must prove each with `terraform validate` + `plan`: Google-managed IAP OAuth (`iap { enabled = true }` on the backend service) → if unsupported, create `google_iap_brand` + `google_iap_client`; GKE `control_plane_endpoints_config.dns_endpoint_config` → if unsupported, use the private endpoint + Cloud Run Direct VPC egress; `networking.gke.io/load-balancer-ip-addresses` → if unsupported, `spec.loadBalancerIP`; `google_monitoring_notification_channel` type `google_chat` is OPTIONAL — email channel to `admin@kambleashish.altostrat.com` is mandatory. |
| A5 | `/metrics` scrape path | Sovereign uses Direct VPC egress with `all-traffic` routing; scrapes Cloud Run services at their `run.app` URLs with an ID token for `godview-sense-sa`; scrapes GKE pods/Services and the VM by private IP. Fallback per TDD §9-10 (`obs.list_timeseries`, ≤ 4 min MTTD) must be implemented behind the same sentinel interface. |
| A6 | Network numbers | `god-view-subnet` `10.10.0.0/20`; GKE master CIDR `172.16.0.0/28`; PSA range `/16` auto-allocated (`google_compute_global_address` purpose VPC_PEERING, prefix 16); Cloud Armor rate limit `120 req/min/IP` (`ban_duration_sec = 300`). |
| A7 | Sizing | Sovereign `1 vCPU / 1 GiB`, `min_instances = max_instances = 1`, CPU always allocated; Cockpit `god-view-sre-web` `1 vCPU / 512 MiB`, min 0 max 2; Agent Engine staging = separate bucket `general-ak-god-view-agent-staging`. |
| A8 | Schedules | Morning digest `09:05 IST`; nightly reflection `03:30 IST`; `godview-probe-run` `*/5 * * * *` (09:00–17:00 IST only); earned-warmth sweep every `15 min`; sentinel baseline window `7 d`; invariant shadow window `24 h`; invariant auto-retirement after `30 d` without a breach AND owner confirmation. |
| A9 | Token budgets (per-agent daily, enforced by the Sovereign governor) | Application / Business-Line / Change agents `$0.75/day` each; Data / Platform agents `$0.50/day`; Sovereign `$1.50/day`; `gemini-4-argon` escalation reserve `20 %` of the fleet ceiling; **fleet ceiling `$6.00/day`** (≈ $180/month). Exhaustion → that agent degrades to L1 until 00:00 IST; fleet exhaustion → all agents L1 + Chat/email notice. Terraform variables carry these numbers; the governor refuses to start without them. |
| A10 | Trust-ladder edge semantics | A bad outcome resets THAT action type to **L1** (not level−1); L4 whitelisting of an action class can be done only by the human owner from the Cockpit (IAP identity recorded in `trust_records.whitelisted_by`); the global kill switch lets the single in-flight step finish (blast radius 1) and aborts everything queued; new L2 approvals are auto-expired while the kill switch is on. |
| A11 | Discovery exclusion | `GODVIEW_HINT_EXCLUDE` (comma-separated resource names, default `general-ak-god-view-tfstate,god-view-sovereign,god-view-sre-web,general-ak-god-view-agent-staging`) plus label `godview_role=observer` on God-View's own resources keep them out of tenant discovery. Hints only narrow, never add knowledge. |
| A12 | Data Agent v1 read isolation | No read replica in v1. PostgreSQL role `godview_readonly` (`default_transaction_read_only = on`, `statement_timeout = '5s'`, `SELECT` on all tables), created by `cymbal/services/loan-orchestrator-api/app/schema.sql` and referenced by Terraform as `google_sql_user` + grants. |
| A13 | OTel → `godview_ledger` write path | v1 = `godview/ledger/exporter`: a Sovereign-side OTel collector-style exporter writing with the **BigQuery Storage Write API** (default stream, at-least-once, dedup by `event_id`); tenant services only emit spans. Datastream CDC stays Phase 2 (`enable_datastream = false`, flags `cloudsql.logical_decoding = on` already set so Phase 2 needs no restart). |
| A14 | Tenant latency KPI | UC-24 `P95 < 3.5 s` excluding image generation stays as an **informational Cockpit KPI**; authoritative SLOs are design §13. |
| A15 | Google Chat | No Chat app can be configured tonight. Build the Chat adapter to spec (cards + callbacks) with a feature flag `GODVIEW_CHAT_ENABLED=false`; approvals run through the Cockpit inbox + email adapter until the owner creates the Chat app. |
| A16 | Simulator cadence | Cloud Scheduler `godview-simulator-tick` `* 9-16 * * *` Asia/Kolkata (weekdays flag `* 9-16 * * 1-5`, default every day) → `POST /v1/simulator/tick` on `ai-customer-simulator`; each tick launches a journey with probability `300/480`; paused by Scheduled Pause / Kill Switch / Hibernate. |
| A17 | Firestore vector indexes | Four (`discovered_services.purpose_embedding`, `invariants.statement_embedding`, `episodic_incidents.symptom_embedding`, `procedural_skills.trigger_embedding`), 768-dim COSINE, flat. |
| A18 | Browser → API routing (CORS avoidance) | All three web apps call **same-origin** paths only. URL map path rules: `cymbal.ak-demos.com/api/*` and `los.cymbal.ak-demos.com/api/*` → API Gateway serverless NEG (the gateway strips nothing; the OpenAPI template mounts the tenant API under `/api/v1/...` as well as `/v1/...`); `godview.ak-demos.com/api/*` → Sovereign NEG behind IAP; `godview.ak-demos.com/public/*` → Sovereign NEG without IAP; everything else on each host → that host's frontend NEG. `api.cymbal.ak-demos.com` stays as the direct API host for the simulator, tests and partners. |
| A19 | Local e2e on this machine | This Cloudtop has no Docker, so `make local-up` / L3 local e2e cannot run here tonight. `infra-workloads-cicd` must still deliver a correct, statically validated `docker-compose.yml`, kind config and seed; L3 runs on the owner's machine later and is recorded as "not executed here" — never faked. |
| A20 | Web app containers | Static bundles served by `nginx:1.27-alpine` on port 8080 with `try_files` SPA fallback, gzip, immutable asset caching, and a `/healthz` location; no runtime env injection — all API paths are same-origin relative. |

---

## B. Engineering rules (non-negotiable; verifiers reject violations)

1. **No rush work.** Correctness and authenticity over speed. If something cannot be done properly tonight, leave it undone with a precise note in `DEPLOY_RUNBOOK.md` or the board — never fake it.
2. **TDD, literally.** RED: write the named test file first, run it, watch it fail. GREEN: minimal implementation. REFACTOR: keep green. Post test output, not adjectives.
3. **Prove it, don't assume it** (cymbal-sarthi method). Before coding against any model/API behaviour, write a throwaway probe script under `~/tmp/` that isolates one variable and prints the raw result. Change one variable per probe.
4. **Verification ladder**: offline assertion → live probe → **rendered artifact (screenshot for anything visual; look at it)** → end-to-end through the real API → deployed check. Exit code 0 is not evidence for anything visual.
5. **Test both failure directions.** When asserting "never X when Y", also assert "still X when not Y".
6. **Fail loudly.** Typed exceptions, honest UI errors. **Never** `except Exception: pass`, never silent fallbacks to fake data in production paths. Hermetic fakes live only under `shared/godview_shared/fakes/` and are selected by `GODVIEW_ENV=hermetic`.
7. **Read the code back after editing.** Re-open the region you changed. Full-block replacements only.
8. **Zero hardcoded tenant knowledge in `godview/` and `shared/`** (TDD I-02): tests use randomised names; `make lint-neutrality` must stay clean.
9. **Real identifiers only**: models per TDD I-09 (Gemini 3.x/4 at `global`), MCP tools per TDD §3.7, Terraform resources that exist in provider `google` ≥ 6.x / `google-beta`.
10. **Every mutating capability goes through TrustGate** (TDD I-06) — no bypass flags, not even for tests (tests construct a `TrustDecision`).
11. **Secrets**: never in code, env files or the repo; Secret Manager names per board #9. Local env uses `local-env/.env.example` placeholders.
12. **Python**: 3.13, FastAPI, Pydantic v2, `uv` for envs (`uv venv .venv && uv pip install -e .[dev]`); `python3 -m venv` is broken on this machine. `ruff` clean. Each package has its own `pyproject.toml`; the root `pyproject.toml` (sovereign-core) only declares the workspace, import-linter contracts and pytest markers.
13. **Node**: 22, npm, Vite, React 18, TypeScript `strict`, Tailwind 3, lucide-react, vitest + @testing-library/react. No CSS-in-JS libraries, no UI kits that impose their look (no MUI/Chakra/Ant). Headless primitives (Radix) are allowed.
14. **Shell realities** (this Cloudtop): `export PATH="$HOME/.local/bin:$PATH"` (terraform 1.16.6, kubectl, uv live there); **no docker, no sudo** → images are built with **Cloud Build** (`gcloud builds submit --tag us-central1-docker.pkg.dev/general-ak/god-view-containers/<svc>:<sha>`), never locally; heredocs and `python3 -c` are blocked → write script files; one long-running command per call; if `gcloud builds submit` dies with `ECPProxyError`, the build is still running (`gcloud builds list`). Scratch files go to `~/tmp/`, never into the repository.
15. **GCP mutations are reserved to `infra-platform` (Terraform platform root) and `deployer` (builds, deploys, workloads root, Agent Engine).** Everyone else may run read-only `gcloud` at most. The owner's gcloud/ADC session expires around **22:30 UTC (04:00 IST)**; after that no GCP call works until the morning re-login. Never start a long apply after 22:00 UTC.
16. **Board etiquette**: results, not status; `@mention` only the peer who acts next; `@coordinator` only for blockers, decisions, or the final deliverable; refer to earlier posts by `#seq`.

---

## C. UI/UX rules for the three web apps (customer portal, internal LOS portal, God-View Cockpit)

**Design intent**: a real financial product, not a template. Quiet, precise, confident. Everything legible at a glance on a 1440×900 laptop in a bright room.

| Area | Rule |
| :-- | :-- |
| Canvas & surfaces | Luminous white: page `#FFFFFF`, secondary surfaces `#F8FAFC` (slate-50), cards white with `1px` slate-200 borders and **no drop shadows heavier than `shadow-sm`**. Generous but disciplined spacing (8-pt grid). |
| Ink & typography | Headings `#0F172A` (slate-900), body `#334155` (slate-700), muted `#64748B` (slate-500). Font: Inter (or Google Sans if available) via `@fontsource`, 14 px base, 1.5 line height, tight tracking on large numerals, `tabular-nums` on every ₹ figure and table numeral. Max 2 font weights per view. |
| Accent colour | Google four-colour palette **used only as accents**: blue `#1A73E8`/`#4285F4` (primary actions, focus rings, links), green `#188038`/`#34A853` (success, approvals), amber `#F9AB00`/`#FBBC04` (warnings), red `#D93025`/`#EA4335` (danger, blocks). One hairline four-colour gradient is allowed per app as a top rule or logo mark — never as text fill, never on buttons, never on cards. |
| Forbidden | **No purple, violet, indigo, fuchsia, magenta or lavender anywhere** (`grep -rniE "purple|violet|indigo|fuchsia|magenta|lavender" src/` must return nothing, including Tailwind class names). No glassmorphism, no neon glows, no giant hero banners, no gradient text, no emoji as icons, no "✨ AI" sparkles, no rounded-3xl blobs, no stock illustrations, no fake testimonials. |
| Light/dark | Light mode is the product. If a `dark:` variant is written at all it must be an explicit pair; **never use 200–400 colour shades bare** (`text-slate-300` on white is invisible). Grep before shipping. |
| Layout patterns | Dense, well-aligned data: tables with sticky headers, right-aligned numerals, zebra-free rows separated by hairlines, column sorting where it matters, row hover, pagination or virtualised lists over ~200 rows. Forms: labelled inputs, inline validation, helper text, disabled-while-submitting, keyboard-accessible. Status chips: small, text + dot, semantic colours. Empty / loading / error states designed on purpose (skeletons, not spinners, for tables). |
| Navigation | Hash-routed tabs/sections (`#showroom`, `#journey`, `#queue`, `#vault`, `#organism`, `#invariants`, …) so every view deep-links and can be screenshotted without clicks. Persistent left rail or top bar; breadcrumb on detail pages. |
| Components | Badge variants limited to `default | secondary | outline | muted | success | warning | danger | info` (there is no `destructive`). Buttons: primary (blue), secondary (white + border), danger (red, confirmation required), ghost. Icons: lucide-react at 16/20 px, stroke 1.75. |
| Data honesty | Nothing hardcoded that comes from the API: no hardcoded service, utility, agent or model names in `godview/god-view-sre-web/`; model badges reflect the actual configured model. Same-origin API paths only (`/api/*`, `/v1/*`) — no hardcoded hosts. |
| Motion | 150–200 ms ease-out transitions on hover/expand only; no page-load animations, no parallax, no auto-playing carousels. |
| Accessibility | WCAG AA contrast, visible focus rings (blue), `aria-label`s on icon buttons, semantic headings, reduced-motion respected. |
| Proof | Before posting "ready": `npm run typecheck && npm test && npm run build`, the forbidden-colour grep, and **screenshots of every main view at 1440×900** (`/google/bin/releases/gemini-agents-gbrowser/gbrowser --width 1440 --height 900 <url>#<tab>`), saved under `~/tmp/screens/<app>/` and listed in the ready post. Look at them. If it looks like a template, it is not done. |

Persona cues: URL 1 is for a customer buying a vehicle — warm, reassuring, minimal jargon, ₹ figures prominent, progress of the journey always visible. URL 2 is for underwriters — information-dense, scannable queues, audit trails, verdict badges. URL 3 is for an SRE lead / CTO — calm control-room: the organism org-chart first, then invariants, journeys, incidents, approvals; red is reserved for the Kill Switch and Sev-1.

---

## D. Deployment policy for tonight

1. `infra-platform` authors the platform root, proves it with `test_terraform_platform.py` + `terraform validate/plan`, posts the plan summary to `@infra-verifier`, and **applies only after `infra-verifier` posts PLAN OK** (one PASS per stage). Stage 1: APIs, SAs/IAM, VPC/PSA/NAT/firewalls, Artifact Registry, buckets, secrets, Firestore DB + indexes, BigQuery dataset/tables, Cloud SQL, GKE cluster, Pub/Sub. Stage 2: Cloud Run services on the placeholder image `us-docker.pkg.dev/cloudrun/container/hello` with `lifecycle { ignore_changes = [template[0].containers[0].image] }`, API Gateway, serverless NEGs, LB + managed cert + DNS records (`cymbal.`, `los.cymbal.`, `api.cymbal.`, `godview.` in zone `ak-demos-com`), Cloud Armor, IAP, VM (startup script serving a placeholder 200 on 8085 until the CBS payload lands). Stage 3: Scheduler jobs, uptime check + heartbeat alert + email channel, budget (optional if permission is missing), hibernate job.
2. `deployer` owns `infra/deploy/` (scripts) and `DEPLOY_RUNBOOK.md`: builds images with Cloud Build and deploys a service **only after its verifier PASS**; applies the workloads root; deploys Agent Engine agents; runs the smoke tests of TF Spec §7; records every URL, revision and command in the runbook; writes the "pending after re-login" section for anything not done by 22:15 UTC.
3. Nothing in `general-ak` outside resources labelled `godview_managed="true"` may be modified or deleted — ever. The existing Cloud DNS zone `ak-demos-com` is a data source; only the four new records are created.
4. After 22:00 UTC: no new `terraform apply`, no new Cloud Build submissions longer than 10 minutes; after 22:30 UTC assume no GCP access until morning.

---

## E. Waves

| Wave | Workers | Starts when |
| :-- | :-- | :-- |
| 1 | `sovereign-core` (M0 first), `infra-platform`, `infra-workloads-cicd`, `frontends-tenant`, `frontend-cockpit`, `deployer`, `infra-verifier`, `frontend-verifier` | now |
| 2 | `adapters-gcp`, `adapters-local-k8s-sql-git`, `ledger-invariants`, `tenant-orchestrator`, `tenant-gke-utilities`, `tenant-vm-cbs`, `ai-customer-simulator`, `backend-verifier`, `agents-verifier` | `sovereign-core` posts "M0 shared/ landed" |
| 3 | `court-agents` | `sovereign-core` M1 + both adapter slices PASS |
