#!/usr/bin/env bash
# Initialize, validate, plan, and apply the Terraform workloads root
# (infra/terraform/workloads) using platform outputs from infra/terraform/platform/outputs.json.
#
# Usage:
#   ./infra/deploy/apply_workloads.sh [extra-terraform-plan-args...]
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PLATFORM_OUTPUTS="${REPO_ROOT}/infra/terraform/platform/outputs.json"
WORKLOADS_DIR="${REPO_ROOT}/infra/terraform/workloads"

if [[ ! -d "${WORKLOADS_DIR}" ]]; then
  echo "ERROR: Workloads directory not found at ${WORKLOADS_DIR}" >&2
  exit 1
fi

if [[ ! -f "${PLATFORM_OUTPUTS}" ]]; then
  echo "ERROR: Platform outputs file not found at ${PLATFORM_OUTPUTS}. Wait for @infra-platform APPLIED stage 1/2." >&2
  exit 1
fi

echo "[apply_workloads] Initializing infra/terraform/workloads..." >&2
terraform -chdir="${WORKLOADS_DIR}" init -input=false

echo "[apply_workloads] Validating infra/terraform/workloads..." >&2
terraform -chdir="${WORKLOADS_DIR}" validate

# Build optional -var arguments from platform outputs if variables exist in workloads root
EXTRA_VARS=()
if [[ -f "${SCRIPT_DIR}/extract_workload_vars.py" ]]; then
  GENERATED_TFVARS="${HOME}/tmp/workloads-platform-outputs.tfvars.json"
  mkdir -p "${HOME}/tmp"
  python3 "${SCRIPT_DIR}/extract_workload_vars.py" \
    --platform-outputs "${PLATFORM_OUTPUTS}" \
    --workloads-dir "${WORKLOADS_DIR}" \
    --out "${GENERATED_TFVARS}"
  if [[ -s "${GENERATED_TFVARS}" ]]; then
    EXTRA_VARS+=("-var-file=${GENERATED_TFVARS}")
  fi
fi

PLAN_FILE="${HOME}/tmp/workloads.tfplan"
mkdir -p "${HOME}/tmp"

echo "[apply_workloads] Planning infra/terraform/workloads..." >&2
terraform -chdir="${WORKLOADS_DIR}" plan -input=false -out="${PLAN_FILE}" "${EXTRA_VARS[@]}" "$@"

echo "[apply_workloads] Applying infra/terraform/workloads..." >&2
terraform -chdir="${WORKLOADS_DIR}" apply -input=false -auto-approve "${PLAN_FILE}"

terraform -chdir="${WORKLOADS_DIR}" output -json > "${WORKLOADS_DIR}/outputs.json"
echo "[apply_workloads] Workloads apply complete. Outputs written to ${WORKLOADS_DIR}/outputs.json" >&2
