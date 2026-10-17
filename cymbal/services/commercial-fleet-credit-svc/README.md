# Utility 3: Commercial Fleet & Two-Wheeler Credit Line Service (`commercial-fleet-credit-svc`)

Runs on Cloud Run (`port 8080`, internal ingress).

## Endpoints
- `POST /internal/fleet-credit/evaluate`: Evaluates `COMMERCIAL_FLEET` and `TWO_WHEELER` working-capital credit lines, writes `commercial_credit_lines` (`godview_lms`), and emits `event_type="credit_line.decided"` with canonical `godview_shared.otel` span attributes (`journey.id`, `customer.id`, `loan.id`, `decision`, `cibil_score`, `status_from`, `status_to`, `event_type`, `amount_inr`, `entity`, `entity_id`, `synthetic`).
- `GET /metrics` and `GET /healthz`: Prometheus metrics and health probe.

## Database Configuration
- Requires explicit `GODVIEW_ENV` (no silent fallback to in-memory fakes outside `GODVIEW_ENV=hermetic`).
- Accepts `DATABASE_URL` (override) or `DB_HOST`, `DB_PORT` (`5432`), `DB_NAME` (`godview_lms`), `DB_USER` (`app_user`), and `DB_PASSWORD` / `DB_PASSWORD_SECRET_ID`.
