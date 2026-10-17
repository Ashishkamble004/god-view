# God-View v4.0 + Harness² SRE Control Plane — Documentation Hub

**Codebase Folder**: [`godview/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/godview)
**Documentation Folder**: [`docs/god-view/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/docs/god-view)
**Primary Reasoning & Harness Proposer Model**: `gemini-4-argon` (`global`), with automatic HTTP 429 / quota / reserve fallback to `gemini-3.8-flash` (`global`)

---

## 1. Purpose & Strict Plane Separation

This folder contains the dedicated architecture and engineering specifications for **God-View v4.0**, the domain-neutral autonomous Site Reliability Engineering (SRE) and Business Continuity organism located in [`godview/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/godview).

Per **Invariant `I-02` (Domain-Neutral Core)** enforced by [`shared/tools/lint_neutrality.py`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/shared/tools/lint_neutrality.py), the God-View control plane is strictly separated from any customer tenant application:

| Plane | Codebase Folder | Documentation Folder | Responsibility |
| :--- | :--- | :--- | :--- |
| **God-View SRE Control Plane** | [`godview/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/godview) | [`docs/god-view/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/docs/god-view) | Sovereign orchestrator, Six Senses discovery, 10 s deterministic `SentinelEngine`, Harness² recursive agent substrate (`c1..c8`), 5 Court Specialist Agents, MCP adapters, and URL 3 SRE Cockpit (`godview.ak-demos.com`). |
| **Customer Application (Cymbal Auto Finance)** | [`cymbal/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/cymbal) | [`docs/cymbal-auto-finance/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/docs/cymbal-auto-finance) | 5-utility digital vehicle-loan origination microservices (`loan-orchestrator-api`, `kyc-model-armor-svc`, `underwriting-los-svc`, `commercial-fleet-credit-svc`, `motor-insurance-svc`, `core-banking-cibil-vm`), `ai-customer-simulator`, URL 1 Customer Loan Portal, and URL 2 Internal Bank LOS Portal. |
| **Shared Contracts & OTel** | [`shared/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/shared) | [`docs/shared/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/docs/shared) | Domain-neutral Pydantic contracts, `MODEL_TABLE`, W3C TraceContext/Baggage (`journey.id`, `customer.id`, `synthetic`), OIDC OTLP span exporter, and 21 `SIX_SENSES_CAPABILITIES`. |
| **Multi-Tier GCP Infrastructure** | [`infra/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/infra) | [`docs/infra/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/docs/infra) | 3-stage Terraform platform (`220` resources in `general-ak`), GKE Autopilot workloads, Cloud Build & GCE VM tarball pipelines, and `docker-compose.yml` hermetic local stack. |

---

## 2. Documents in `docs/god-view/`

1. **[`HLD.md` — God-View v4.0 + Harness² High-Level Design](HLD.md)**
   - "Eyes vs. Brains" separation (`0` LLM tokens at rest; 10 s deterministic `SentinelEngine`)
   - Six Senses autonomous discovery & Living System Model
   - **Harness² (`arXiv:2610.14224`)** Level-1 Task Recursion (`Propose -> Probe -> Compose` with `INV` + `DOM` + `AGG` proposers on `gemini-4-argon` @ `global`) and Level-2 Harnessing Recursion (`run_selection` + `reflect_adoption` + `CloudSyncedLibrary`)
   - 5 Court Specialist Agents (`application`, `data`, `business_line`, `change`, `platform`) and `CourtSwarmSession` consensus
   - Deterministic `TrustGate` (`L0–L4`) and URL 3 God-View SRE Cockpit (`godview.ak-demos.com`)
2. **[`LLD.md` — God-View v4.0 + Harness² Low-Level Design](LLD.md)**
   - Module-by-module class diagrams (`classDiagram`) and method contracts for `godview/sovereign/`, `godview/harness/`, `godview/agents/`, `godview/adapters/`, `godview/invariants/`, `godview/ledger/`, and `godview/god-view-sre-web/`
   - Entity-Relationship schemas (`erDiagram`) for BigQuery (`godview_ledger`) and Firestore Native (`god-view-memory-db` with 4 768-dim vector indexes)
   - Wire-level sequence diagrams (`sequenceDiagram`) for Sentinel breach detection, Level-1/Level-2 Harness² recursion, TrustGate remediation, and GitOps PR creation
3. **[`SYSTEM_DESIGN.md` — God-View SRE System Design, Budgets & Protocols](SYSTEM_DESIGN.md)**
   - Latency & detection SLOs, `TokenBudgetGovernor` FinOps model, MCP stdio/HTTP subprocess isolation, A2A v0.3 protocol, and Cloud Scheduler lifecycle automation
4. **[`../GOD_VIEW_MCP_AGENT_SPEC.md` — MCP Tooling & Agent Specification](../GOD_VIEW_MCP_AGENT_SPEC.md)**
   - Detailed MCP capability contracts and safety invariants (`I-01` through `I-12`)
