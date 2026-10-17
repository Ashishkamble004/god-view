# Utility 5: Core Banking & CIBIL VM Service (`core-banking-cibil-vm`)

Runs on the GCE `e2-micro` VM (`core-banking-cibil-vm`, port `8085`) via systemd (`systemd/cibil-cbs.service`).

## Endpoints
- `POST /internal/cibil-cbs/score`: Persona-seeded deterministic SHA-256 CIBIL bureau score in `300..899` (or `900` when `cibil_stub_900` is active in `chaos_fault_states`), returning `cibil_score`, `cibil_bureau_ref` (`CIBIL-2026-XXXXXX`), and `gce_vm_name`. (Note: `underwriting_audits` is owned and written by Utility 2 `underwriting-los-svc`.)
- `POST /internal/cibil-cbs/disburse`: Idempotent CBS disbursement by `loan_id` (`PENDING` -> `DISBURSED`), generating `sanction_letter_no` (`SANC-2026-XXXXXX`), `nach_mandate_id` (`NACH-2026-XXXXXX`), and `cbs_utr_number` (`CBS-UTR-2026-XXXXXX`), persisting to `disbursements` (`godview_lms`).
- `POST /internal/cibil-cbs/score-and-disburse`: Combined bureau score + idempotent disbursement endpoint.
- `GET /internal/cibil-cbs/disbursements` and `GET /internal/cibil-cbs/disbursements/{loan_id}`: Read endpoints for disbursement records.
- `GET /metrics` and `GET /healthz`: Prometheus metrics and health probe.

## Chaos Hooks (polled from PostgreSQL `chaos_fault_states`)
- `cibil_stub_900` (UC-07): Forces every CIBIL evaluation to `900`.
- `gce_vm_latency_504` (UC-10 / UC-16): Sleeps `sleep_seconds` (default `6.0s`) and returns HTTP `504`, leaving `disbursements` row in `PENDING` (attempt 1) or `FAILED` (attempt >= 2) for later saga replay.

## Database & VM Environment Contract
- Reads `DATABASE_URL` (override) or `DB_HOST`, `DB_PORT` (`5432`), `DB_NAME` (`godview_lms`), `DB_USER` (`app_user`), and `DB_PASSWORD` / `DB_PASSWORD_SECRET_ID` (`projects/<project_id>/secrets/cymbal-pg-app-password/versions/latest`).
- `install.sh` and `ExecStartPre=+/opt/cibil-cbs/.venv/bin/python -m cibil_cbs.env_sync` populate `/etc/default/cibil-cbs` (`0640`) from GCE instance metadata (`DB_HOST` / `db-host` / `sql_private_ip`) and Secret Manager.
- Payload tarball built via `bash cymbal/services/core-banking-cibil-vm/build_payload.sh` -> `gs://general-ak-god-view-kyc-vault/vm-artifacts/{cbs-payload.tar.gz,core-banking-cibil-vm.tar.gz}`.
