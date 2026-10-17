#!/usr/bin/env bash
# Build the GCE VM payload tarball (cbs-payload.tar.gz and core-banking-cibil-vm.tar.gz)
# under ~/tmp/ with vendored wheels, app/, cibil_cbs/, shared/, systemd/cibil-cbs.service, and install.sh.
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
STAGE_DIR="${HOME}/tmp/cbs-payload-stage"

rm -rf "${STAGE_DIR}"
mkdir -p "${STAGE_DIR}/vendor"

cp -a "${SCRIPT_DIR}/." "${STAGE_DIR}/"
if [[ -d "${REPO_ROOT}/shared" ]]; then
  rm -rf "${STAGE_DIR}/shared"
  cp -a "${REPO_ROOT}/shared" "${STAGE_DIR}/shared"
fi

# Collect cached third-party wheels and build fresh godview_shared + core_banking_cibil_vm wheels into vendor/
if [[ -d "${HOME}/.cache/uv" ]]; then
  find "${HOME}/.cache/uv" -name "*.whl" -exec cp -n {} "${STAGE_DIR}/vendor/" \; 2>/dev/null || true
fi
rm -f "${STAGE_DIR}/vendor/commercial_fleet_credit_svc-"*.whl \
      "${STAGE_DIR}/vendor/motor_insurance_svc-"*.whl \
      "${STAGE_DIR}/vendor/ai_customer_simulator-"*.whl \
      "${STAGE_DIR}/vendor/godview_adapters_"*.whl
python3 "${SCRIPT_DIR}/cibil_cbs/_pack_wheels.py" "${STAGE_DIR}/vendor" >/dev/null

chmod +x "${STAGE_DIR}/install.sh"

for name in "cbs-payload.tar.gz" "core-banking-cibil-vm.tar.gz"; do
  tar -C "${STAGE_DIR}" \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='.pytest_cache' \
    --exclude='.ruff_cache' \
    -czf "${HOME}/tmp/${name}" .
done

echo "${HOME}/tmp/cbs-payload.tar.gz"
