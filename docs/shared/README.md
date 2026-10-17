# Shared Contracts, OpenTelemetry W3C Baggage & Capability Registry — Documentation Hub

**Codebase Folder**: [`shared/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/shared) (`shared/godview_shared/` & `shared/tools/`)
**Documentation Folder**: [`docs/shared/`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/docs/shared)

---

## 1. Purpose of `shared/godview_shared/`

`shared/godview_shared/` is the zero-dependency contract boundary shared between the **God-View v4.0 SRE Control Plane** (`godview/`) and the **Cymbal Auto Finance Customer Application** (`cymbal/`). It contains **zero tenant-specific logic** (`I-02`).

### 1.1 Module Map

| File Path | Key Exports | Responsibility |
| :--- | :--- | :--- |
| [`shared/godview_shared/config.py`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/shared/godview_shared/config.py) | `MODEL_TABLE`, `BudgetConstants`, `GodViewSettings` | Central model routing table (`primary_reasoning` & `escalation_reasoning` = `gemini-4-argon` @ `global`, fallback = `gemini-3.8-flash` @ `global`; `fast_utility` = `gemini-2.5-flash`; `specimen_image` = `gemini-3.1-flash-image-preview`; `embedding` = `text-embedding-005` 768-dim) and FinOps budget constants. |
| [`shared/godview_shared/contracts/__init__.py`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/shared/godview_shared/contracts/__init__.py) | `TENANT_BUSINESS_TABLES`, `TrustLevel`, `SentinelState`, `IncidentRecord`, `ApprovalRequest` | Shared Pydantic v2 schemas for incidents, Sentinels, TrustGate (`L0–L4`), and Living System Models. |
| [`shared/godview_shared/otel.py`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/shared/godview_shared/otel.py) | `extract_baggage_context`, `inject_baggage_headers`, `set_business_span_attributes`, `OtlpHttpSpanExporter` | W3C `traceparent` + `baggage` (`journey.id`, `customer.id`, `synthetic`) propagation and OIDC-authenticated OTLP HTTP span exporter (`/v1/traces`). |
| [`shared/godview_shared/capabilities.py`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/shared/godview_shared/capabilities.py) | `SIX_SENSES_CAPABILITIES` | Canonical registry of all 21 MCP capabilities across the Six Senses with their `TrustLevel` and mandatory `reversal_capability`. |
| [`shared/godview_shared/adapter_contract.py`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/shared/godview_shared/adapter_contract.py) | `BaseSenseAdapter`, `AdapterCallRequest`, `AdapterResponse` | Base contract for all Six Senses MCP adapters. |
| [`shared/tools/lint_brands.py`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/shared/tools/lint_brands.py) | `scan_paths`, `main` | Enforces Invariant `I-01` (`0` forbidden competitor, real-bank, or real-automaker brand hits across the repo). Also callable via `python3 scripts/lint_brands.py`. |
| [`shared/tools/lint_neutrality.py`](file:///usr/local/google/home/kambleashish/Documents/Projects/god-view/shared/tools/lint_neutrality.py) | `main` | Enforces Invariant `I-02` (`0` tenant-specific tokens inside `godview/` or `shared/godview_shared/`). |

For full class diagrams (`classDiagram`) and method signatures, see **[Master LLD §1.1](../LLD.md)**.
