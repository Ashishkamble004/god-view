# Unknown Environment: Fictional Parcel Logistics Stack

This directory contains the unknown, non-tenant test environment for God-View v5 neutrality testing.

## Overview
- **Domain**: Fictional parcel shipment & transit logistics (intake -> route -> dispatch -> proof).
- **Services**:
  1. `parcel-intake-svc`: Public ingress accepting new parcel shipments (`POST /v1/parcels/intake`).
  2. `parcel-route-svc`: Routing engine calculating carrier transit routes (`POST /v1/parcels/route`).
  3. `parcel-dispatch-svc`: Courier dispatch service (`POST /v1/parcels/dispatch`).
  4. `parcel-proof-svc`: Delivery confirmation and recipient signature service (`POST /v1/parcels/proof`).
  5. `parcel-db`: PostgreSQL datastore tracking manifests, routes, dispatches, and delivery proofs.
- **Contracts**: OpenAPI 3.0 specs served at `/openapi.json` for all microservices.
- **Telemetry**: OTLP/HTTP traces and Prometheus metrics endpoints.
- **Neutrality**: Zero lending, banking, or vehicle terms.
