#!/usr/bin/env bash
# Package and upload the core-banking-cibil-vm payload to GCS, and optionally
# reset the VM to re-run its startup script once @infra-platform confirms the
# startup script pulls from the bucket.
#
# Refuses to reset core-banking-cibil-vm unless it carries label godview_managed="true".
#
# Usage:
#   ./infra/deploy/deploy_vm_payload.sh [--bucket gs://general-ak-god-view-kyc-vault/vm-artifacts] [--reset]
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PROJECT_ID="${GCP_PROJECT_ID:-general-ak}"
ZONE="${GCP_ZONE:-us-central1-a}"
VM_NAME="core-banking-cibil-vm"
CBS_DIR="${REPO_ROOT}/cymbal/services/core-banking-cibil-vm"

DEST_PREFIX="gs://${PROJECT_ID}-god-view-kyc-vault/vm-artifacts"
DO_RESET="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bucket)
      DEST_PREFIX="${2%/}"
      shift 2
      ;;
    --reset)
      DO_RESET="true"
      shift
      ;;
    *)
      echo "ERROR: Unknown argument '$1'" >&2
      exit 1
      ;;
  esac
done

if [[ ! -d "${CBS_DIR}" ]]; then
  echo "ERROR: CBS service directory not found at ${CBS_DIR}" >&2
  exit 1
fi

if [[ -f "${CBS_DIR}/build_payload.sh" ]]; then
  echo "[deploy_vm_payload] Building payload via ${CBS_DIR}/build_payload.sh ..." >&2
  bash "${CBS_DIR}/build_payload.sh" >/dev/null
  # Ensure Debian 12 (Python 3.11) compatibility in staged payload:
  if [[ -f "${HOME}/tmp/cbs-payload-stage/shared/pyproject.toml" ]]; then
    sed -i 's/requires-python = ">=3.13"/requires-python = ">=3.11"/' "${HOME}/tmp/cbs-payload-stage/shared/pyproject.toml"
  fi
  rm -f "${HOME}/tmp/cbs-payload-stage/vendor/godview_shared-"*.whl \
        "${HOME}/tmp/cbs-payload-stage/vendor/core_banking_cibil_vm-"*.whl \
        "${HOME}/tmp/cbs-payload-stage/vendor/"*cp313*.whl 2>/dev/null || true
  if [[ -f "${HOME}/tmp/cbs-payload-stage/install.sh" ]]; then
    sed -i 's/if ! python3 -m venv --help >\/dev\/null 2>&1; then/if [[ ! -x "${APP_DIR}\/.venv\/bin\/pip" ]]; then/' "${HOME}/tmp/cbs-payload-stage/install.sh"
    sed -i 's/if \[\[ ! -x "\${APP_DIR}\/\.venv\/bin\/python" \]\]; then/if [[ ! -x "${APP_DIR}\/.venv\/bin\/pip" ]]; then\n  rm -rf "${APP_DIR}\/.venv"/' "${HOME}/tmp/cbs-payload-stage/install.sh"
    sed -i 's/"\${PIP_BIN}" install /"${PIP_BIN}" install --ignore-requires-python /g' "${HOME}/tmp/cbs-payload-stage/install.sh"
    sed -i 's|-m cibil_cbs\.env_sync|"${APP_DIR}/cibil_cbs/env_sync.py"|g' "${HOME}/tmp/cbs-payload-stage/install.sh"
  fi
  if [[ -f "${HOME}/tmp/cbs-payload-stage/systemd/cibil-cbs.service" ]]; then
    sed -i 's|-m cibil_cbs\.env_sync|/opt/cibil-cbs/cibil_cbs/env_sync.py|g' "${HOME}/tmp/cbs-payload-stage/systemd/cibil-cbs.service"
  fi
  for name in "cbs-payload.tar.gz" "core-banking-cibil-vm.tar.gz"; do
    tar -C "${HOME}/tmp/cbs-payload-stage" \
      --exclude='.venv' \
      --exclude='__pycache__' \
      --exclude='.pytest_cache' \
      --exclude='.ruff_cache' \
      -czf "${HOME}/tmp/${name}" .
  done
  TARBALL="${HOME}/tmp/cbs-payload.tar.gz"
else
  mkdir -p "${HOME}/tmp/cbs-payload-stage"
  rm -rf "${HOME}/tmp/cbs-payload-stage/"*
  cp -a "${CBS_DIR}/." "${HOME}/tmp/cbs-payload-stage/"
  if [[ -d "${REPO_ROOT}/shared" ]]; then
    cp -a "${REPO_ROOT}/shared" "${HOME}/tmp/cbs-payload-stage/shared"
  fi

  TARBALL="${HOME}/tmp/cbs-payload.tar.gz"
  tar -C "${HOME}/tmp/cbs-payload-stage" \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='.pytest_cache' \
    --exclude='.ruff_cache' \
    -czf "${TARBALL}" .
fi

SHA="$(sha256sum "${TARBALL}" | cut -c1-12)"

echo "[deploy_vm_payload] Uploading ${TARBALL} (sha=${SHA}) to ${DEST_PREFIX}/ ..." >&2
gcloud storage cp "${TARBALL}" "${DEST_PREFIX}/cbs-payload-${SHA}.tar.gz" --project="${PROJECT_ID}"
gcloud storage cp "${TARBALL}" "${DEST_PREFIX}/cbs-payload.tar.gz" --project="${PROJECT_ID}"
gcloud storage cp "${TARBALL}" "${DEST_PREFIX}/core-banking-cibil-vm.tar.gz" --project="${PROJECT_ID}"

if [[ "${DO_RESET}" == "true" ]]; then
  MANAGED_LABEL="$(gcloud compute instances describe "${VM_NAME}" \
    --zone="${ZONE}" \
    --project="${PROJECT_ID}" \
    --format="value(labels.godview_managed)" 2>/dev/null || true)"
  if [[ "${MANAGED_LABEL}" != "true" ]]; then
    echo "ERROR: VM '${VM_NAME}' does not exist or lacks label godview_managed=true (found '${MANAGED_LABEL}'). Refusing to reset." >&2
    exit 1
  fi
  echo "[deploy_vm_payload] Resetting ${VM_NAME} in ${ZONE} to trigger startup script..." >&2
  gcloud compute instances reset "${VM_NAME}" --zone="${ZONE}" --project="${PROJECT_ID}" --quiet
fi

echo "${DEST_PREFIX}/core-banking-cibil-vm-${SHA}.tar.gz"
