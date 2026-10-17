# `shared/` — Shared Domain-Neutral Contracts, OpenTelemetry & Linter Tools

**Dedicated Documentation Folder**: [`../docs/shared/`](../docs/shared/README.md) ([Master LLD §1.1](../docs/LLD.md))

---

## Subdirectories inside `shared/`

| Subdirectory | Description |
| :--- | :--- |
| [`shared/godview_shared/`](godview_shared/) | Shared Python package (`config.py` `MODEL_TABLE`, `contracts/`, `otel.py` W3C baggage & OIDC trace exporter, `capabilities.py` 21 `SIX_SENSES_CAPABILITIES`, `adapter_contract.py`). |
| [`shared/tools/`](tools/) | Repository governance linters: `lint_brands.py` (`I-01` zero forbidden brand names) and `lint_neutrality.py` (`I-02` zero tenant strings in `godview/` or `shared/godview_shared/`). |
| [`shared/tests/`](tests/) | Unit tests for `godview_shared` contracts, OTel baggage, OIDC trace exporter, and linters. |
