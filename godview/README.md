# `godview/` — God-View v4.0 + Harness² Autonomous SRE Control Plane

**Dedicated Documentation Folder**: [`../docs/god-view/`](../docs/god-view/README.md) ([HLD](../docs/god-view/HLD.md) · [LLD](../docs/god-view/LLD.md) · [System Design](../docs/god-view/SYSTEM_DESIGN.md))
**Primary Model**: `gemini-4-argon` (`global`), with automatic fallback to `gemini-3.8-flash` (`global`)

---

## Subpackages inside `godview/`

| Subdirectory | Role & Key Modules |
| :--- | :--- |
| [`godview/sovereign/`](sovereign/) | `PerceptionOrgan` (Six Senses), `SentinelEngine` (10 s zero-token loop), `TrustGate` (`L0–L4`), `CourtManager`, `TokenBudgetGovernor`, `NervousSystemSubscriber`, and FastAPI `control_api`. |
| [`godview/harness/`](harness/) | Google Research **Harness² (`arXiv:2610.14224`)** recursive agent substrate: `core/` (`spec`, `tree`, `ledger`, `recursion`, `selection`, `library`, `runmark`), `GODVIEW_SURFACE` (`c1..c8`), `validate_script_ast`, `HookRunner`, `VertexGeminiProposer`, `GodViewCourtAdapter`, and `CloudSyncedLibrary`. |
| [`godview/agents/`](agents/) | 5 Court Specialist Agents (`application`, `data`, `business_line`, `change`, `platform`), seed `HarnessTree`s in `base_trees/`, `CourtSwarmSession` consensus, A2A `AgentCard`/`AgentExecutor`, and Harness² inspection routes. |
| [`godview/adapters/`](adapters/) | Six Senses MCP adapters: `gcp/`, `kubernetes/`, `sql/`, `git/`, `local/`, and `channels/`. |
| [`godview/invariants/`](invariants/) | `sqlglot` read-only SQL AST compiler (`I-05`), invariant loader, and Sentinel skill compiler. |
| [`godview/ledger/`](ledger/) | BigQuery `godview_ledger` DDL (`events`, `state_transitions`, `journeys`, `kpi_rollups`), OTel exporter, KPI rollups, and Directly-Follows Graph (DFG) process mining. |
| [`godview/god-view-sre-web/`](god-view-sre-web/) | URL 3 God-View SRE Cockpit React 18 + TypeScript + Tailwind SPA (IAP-protected SRE operator console). |
