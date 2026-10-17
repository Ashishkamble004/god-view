#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PGHOST="${PGHOST:-127.0.0.1}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-app_user}"
PGPASSWORD="${PGPASSWORD:-local_dev_password_only}"
PGDATABASE="${PGDATABASE:-godview_lms}"
export PGPASSWORD

echo "Applying local-env PostgreSQL extensions and godview_readonly role..."
psql -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d "${PGDATABASE}" -v ON_ERROR_STOP=1 -f "${SCRIPT_DIR}/01_extensions_and_roles.sql"

if [ -f "${REPO_ROOT}/cymbal/services/loan-orchestrator-api/app/schema.sql" ]; then
  echo "Applying canonical tenant schema from cymbal/services/loan-orchestrator-api/app/schema.sql..."
  psql -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d "${PGDATABASE}" -v ON_ERROR_STOP=1 -f "${REPO_ROOT}/cymbal/services/loan-orchestrator-api/app/schema.sql"
fi

echo "Applying local-env seed schema and 8 fictional vehicles..."
psql -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d "${PGDATABASE}" -v ON_ERROR_STOP=1 -f "${SCRIPT_DIR}/02_schema_and_seed.sql"

echo "Local database seed complete."
