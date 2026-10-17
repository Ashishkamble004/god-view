# Utility 4: Motor Insurance & FastTag Add-On Service (`motor-insurance-svc`)

Runs on Cloud Run (`port 8080`, internal ingress).

## Endpoints
- `POST /internal/insurance/issue`: Issues comprehensive motor insurance and FastTag wallet linkage (`FTAG-2026-XXXXXX`) only when the loan exists and its status is in `{"APPROVED", "DISBURSED"}` (returns HTTP `409 LOAN_NOT_FOUND` if the loan is missing or `409 LOAN_NOT_APPROVED` for `KYC_PENDING`, `KYC_VERIFIED`, `MODEL_ARMOR_BLOCKED`, `REJECTED`). Writes to `insurance_policies` (`godview_lms`) and emits `event_type="insurance.issued"`.
- `GET /metrics` and `GET /healthz`: Prometheus metrics and health probe.

## Chaos Hook (`chaos_fault_states`)
- `insurance_no_policy_write` (UC-05 silent-failure drill): Returns a normal-looking HTTP `200` `ACTIVE` response (`policy_id`, `fasttag_id`) without writing a row to `insurance_policies` and without emitting any business event on the OTel span (keeping the drill marker only in `chaos_fault_states`).

## Database Configuration
- Requires explicit `GODVIEW_ENV` (no silent fallback to in-memory fakes outside `GODVIEW_ENV=hermetic`).
- Accepts `DATABASE_URL` (override) or `DB_HOST`, `DB_PORT` (`5432`), `DB_NAME` (`godview_lms`), `DB_USER` (`app_user`), and `DB_PASSWORD` / `DB_PASSWORD_SECRET_ID`.
