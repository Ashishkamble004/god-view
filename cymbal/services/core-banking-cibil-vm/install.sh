#!/usr/bin/env bash
# Idempotent Debian 12 installer for Utility 5 (core-banking-cibil-vm, GCE e2-micro, port 8085).
# Works offline from vendored wheels in /opt/cibil-cbs/vendor or over Cloud NAT.
set -euo pipefail

APP_DIR="/opt/cibil-cbs"
SERVICE_USER="cibilcbs"
UNIT_NAME="cibil-cbs.service"
ENV_FILE="/etc/default/cibil-cbs"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

mkdir -p "${APP_DIR}"
if [[ "${SCRIPT_DIR}" != "${APP_DIR}" ]]; then
  cp -a "${SCRIPT_DIR}/." "${APP_DIR}/"
fi

if ! python3 -m ensurepip --version >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends python3-venv python3-pip
fi

if [[ ! -x "${APP_DIR}/.venv/bin/pip" ]]; then
  rm -rf "${APP_DIR}/.venv"
  python3 -m venv "${APP_DIR}/.venv" || {
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y --no-install-recommends python3-venv python3-pip
    rm -rf "${APP_DIR}/.venv"
    python3 -m venv "${APP_DIR}/.venv"
  }
fi

PIP_BIN="${APP_DIR}/.venv/bin/pip"
cd "${APP_DIR}"
if [[ -d "${APP_DIR}/vendor" ]] && compgen -G "${APP_DIR}/vendor/*.whl" >/dev/null; then
  "${PIP_BIN}" install --no-index --find-links="${APP_DIR}/vendor" -r "${APP_DIR}/requirements.txt" || \
    "${PIP_BIN}" install --find-links="${APP_DIR}/vendor" -r "${APP_DIR}/requirements.txt"
else
  "${PIP_BIN}" install --upgrade pip
  "${PIP_BIN}" install -r "${APP_DIR}/requirements.txt"
fi

# Populate /etc/default/cibil-cbs (mode 0640) with DB_HOST from GCE instance metadata
# and DB_PASSWORD from Secret Manager via the metadata-server token.
GODVIEW_ENV="${GODVIEW_ENV:-gcp}" "${APP_DIR}/.venv/bin/python" "${APP_DIR}/cibil_cbs/env_sync.py"
chmod 0640 "${ENV_FILE}"
chown root:"${SERVICE_USER}" "${ENV_FILE}"

chown -R "${SERVICE_USER}:${SERVICE_USER}" "${APP_DIR}"

install -m 0644 "${APP_DIR}/systemd/${UNIT_NAME}" "/etc/systemd/system/${UNIT_NAME}"
systemctl daemon-reload
systemctl enable "${UNIT_NAME}"
systemctl restart "${UNIT_NAME}"
