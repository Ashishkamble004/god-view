#!/usr/bin/env bash
# Build a service container image via Google Cloud Build (no local Docker required),
# resolve the immutable @sha256 digest from Artifact Registry, and optionally pin it
# in images.auto.tfvars.
#
# Usage:
#   ./infra/deploy/build_image.sh <svc> [--pin]
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PROJECT_ID="${GCP_PROJECT_ID:-general-ak}"
REGION="${GCP_REGION:-us-central1}"
AR_REPO="us-central1-docker.pkg.dev/${PROJECT_ID}/god-view-containers"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <service-name> [--pin]" >&2
  exit 1
fi

SVC="$1"
PIN_TFVARS="false"
if [[ "${2:-}" == "--pin" ]]; then
  PIN_TFVARS="true"
fi

# Map service name to its source directory and target Terraform root for image pinning
case "${SVC}" in
  loan-orchestrator-api)
    SVC_DIR="cymbal/services/loan-orchestrator-api"
    TF_ROOT="platform"
    NEEDS_SHARED="true"
    ;;
  kyc-model-armor-svc)
    SVC_DIR="cymbal/services/kyc-model-armor-svc"
    TF_ROOT="workloads"
    NEEDS_SHARED="true"
    ;;
  underwriting-los-svc)
    SVC_DIR="cymbal/services/underwriting-los-svc"
    TF_ROOT="workloads"
    NEEDS_SHARED="true"
    ;;
  commercial-fleet-credit-svc)
    SVC_DIR="cymbal/services/commercial-fleet-credit-svc"
    TF_ROOT="platform"
    NEEDS_SHARED="true"
    ;;
  motor-insurance-svc)
    SVC_DIR="cymbal/services/motor-insurance-svc"
    TF_ROOT="platform"
    NEEDS_SHARED="true"
    ;;
  core-banking-cibil-vm)
    SVC_DIR="cymbal/services/core-banking-cibil-vm"
    TF_ROOT="platform"
    NEEDS_SHARED="true"
    ;;
  ai-customer-simulator)
    SVC_DIR="cymbal/services/ai-customer-simulator"
    TF_ROOT="platform"
    NEEDS_SHARED="true"
    ;;
  customer-loan-portal-web)
    SVC_DIR="cymbal/frontends/customer-loan-portal-web"
    TF_ROOT="platform"
    NEEDS_SHARED="false"
    ;;
  internal-bank-los-web)
    SVC_DIR="cymbal/frontends/internal-bank-los-web"
    TF_ROOT="platform"
    NEEDS_SHARED="false"
    ;;
  god-view-sre-web)
    SVC_DIR="godview/god-view-sre-web"
    TF_ROOT="platform"
    NEEDS_SHARED="false"
    ;;
  god-view-sovereign)
    SVC_DIR="godview/sovereign"
    TF_ROOT="platform"
    NEEDS_SHARED="true"
    ;;
  god-view-hibernate-job)
    SVC_DIR="godview/sovereign"
    TF_ROOT="platform"
    NEEDS_SHARED="true"
    ;;
  *)
    echo "ERROR: Unknown service '${SVC}'" >&2
    exit 1
    ;;
esac

ABS_SVC_DIR="${REPO_ROOT}/${SVC_DIR}"
DOCKERFILE_PATH="${ABS_SVC_DIR}/Dockerfile"

if [[ ! -f "${DOCKERFILE_PATH}" ]]; then
  echo "ERROR: Dockerfile not found at ${DOCKERFILE_PATH}" >&2
  exit 1
fi

# Compute deterministic 12-char content SHA across the service inputs
HASH_PATHS=("${ABS_SVC_DIR}")
if [[ "${NEEDS_SHARED}" == "true" && -d "${REPO_ROOT}/shared" ]]; then
  HASH_PATHS+=("${REPO_ROOT}/shared")
fi
if [[ "${SVC}" == "god-view-sovereign" || "${SVC}" == "god-view-hibernate-job" ]]; then
  for sub in "${REPO_ROOT}/godview"/*; do
    sub_name="$(basename "${sub}")"
    if [[ "${sub_name}" != "god-view-sre-web" && -d "${sub}" ]]; then
      HASH_PATHS+=("${sub}")
    fi
  done
fi

SHA="$(
  find "${HASH_PATHS[@]}" -type f \
    ! -path '*/.venv/*' \
    ! -path '*/node_modules/*' \
    ! -path '*/dist/*' \
    ! -path '*/__pycache__/*' \
    ! -path '*/.pytest_cache/*' \
    ! -path '*/.ruff_cache/*' \
    -print0 \
  | sort -z \
  | xargs -0 sha256sum \
  | sha256sum \
  | cut -c1-12
)"

IMAGE_TAG="${AR_REPO}/${SVC}:${SHA}"
CLOUDBUILD_YAML="${SCRIPT_DIR}/cloudbuild/${SVC}.yaml"
IGNORE_FILE="${SCRIPT_DIR}/gcloudignore"

# Determine whether Dockerfile expects repo-root context or service-directory context.
if [[ "${SVC}" == "god-view-sovereign" || "${SVC}" == "god-view-hibernate-job" ]]; then
  STAGE_DIR="${HOME}/tmp/cloudbuild-${SVC}-${SHA}"
  rm -rf "${STAGE_DIR}"
  mkdir -p "${STAGE_DIR}/godview"
  cp -a "${DOCKERFILE_PATH}" "${STAGE_DIR}/Dockerfile"
  if [[ -d "${REPO_ROOT}/shared" ]]; then
    cp -a "${REPO_ROOT}/shared" "${STAGE_DIR}/shared"
  fi
  if [[ -f "${REPO_ROOT}/godview/__init__.py" ]]; then
    cp -a "${REPO_ROOT}/godview/__init__.py" "${STAGE_DIR}/godview/__init__.py"
  fi
  for sub in "${REPO_ROOT}/godview"/*; do
    sub_name="$(basename "${sub}")"
    if [[ "${sub_name}" != "god-view-sre-web" && -d "${sub}" ]]; then
      cp -a "${sub}" "${STAGE_DIR}/godview/${sub_name}"
    fi
  done
  if [[ -f "${REPO_ROOT}/pyproject.toml" ]]; then
    cp -a "${REPO_ROOT}/pyproject.toml" "${STAGE_DIR}/pyproject.toml"
  fi
  if [[ -f "${REPO_ROOT}/uv.lock" ]]; then
    cp -a "${REPO_ROOT}/uv.lock" "${STAGE_DIR}/uv.lock"
  fi
  BUILD_CONTEXT="."
  REL_DOCKERFILE="Dockerfile"
  SUBMIT_DIR="${STAGE_DIR}"
elif grep -qE '^[[:space:]]*(COPY|ADD)[[:space:]].*(cymbal/|godview/|shared/)' "${DOCKERFILE_PATH}"; then
  BUILD_CONTEXT="."
  REL_DOCKERFILE="${SVC_DIR}/Dockerfile"
  SUBMIT_DIR="${REPO_ROOT}"
elif [[ "${NEEDS_SHARED}" == "true" ]]; then
  # Stage service dir + shared/ into ~/tmp/cloudbuild-<svc>-<sha>
  STAGE_DIR="${HOME}/tmp/cloudbuild-${SVC}-${SHA}"
  rm -rf "${STAGE_DIR}"
  mkdir -p "${STAGE_DIR}"
  cp -a "${ABS_SVC_DIR}/." "${STAGE_DIR}/"
  if [[ -d "${REPO_ROOT}/shared" && ! -d "${STAGE_DIR}/shared" ]]; then
    cp -a "${REPO_ROOT}/shared" "${STAGE_DIR}/shared"
  fi
  BUILD_CONTEXT="."
  REL_DOCKERFILE="Dockerfile"
  SUBMIT_DIR="${STAGE_DIR}"
else
  # Frontend service: ensure dist/ is built locally, then stage nginx.conf + html/ for fast, deterministic nginx image build
  if [[ ! -f "${ABS_SVC_DIR}/dist/index.html" ]]; then
    echo "[build_image] Building frontend bundle in ${ABS_SVC_DIR} ..." >&2
    (cd "${ABS_SVC_DIR}" && npm run build >&2)
  fi
  STAGE_DIR="${HOME}/tmp/cloudbuild-${SVC}-${SHA}"
  rm -rf "${STAGE_DIR}"
  mkdir -p "${STAGE_DIR}"
  cp -a "${ABS_SVC_DIR}/nginx.conf" "${STAGE_DIR}/nginx.conf"
  cp -a "${ABS_SVC_DIR}/dist" "${STAGE_DIR}/html"
  cat > "${STAGE_DIR}/Dockerfile" <<'DOCKER_EOF'
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY html /usr/share/nginx/html
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
DOCKER_EOF
  BUILD_CONTEXT="."
  REL_DOCKERFILE="Dockerfile"
  SUBMIT_DIR="${STAGE_DIR}"
fi

echo "[build_image] Service=${SVC} SHA=${SHA} Context=${BUILD_CONTEXT} Dockerfile=${REL_DOCKERFILE}" >&2
echo "[build_image] Submitting Cloud Build for ${IMAGE_TAG} ..." >&2

set +e
gcloud builds submit "${SUBMIT_DIR}" \
  --project="${PROJECT_ID}" \
  --config="${CLOUDBUILD_YAML}" \
  --ignore-file="${IGNORE_FILE}" \
  --substitutions="_SERVICE=${SVC},_SHA=${SHA},_CONTEXT=${BUILD_CONTEXT},_DOCKERFILE=${REL_DOCKERFILE}" \
  >&2
SUBMIT_RC=$?
set -e

if [[ ${SUBMIT_RC} -ne 0 ]]; then
  echo "[build_image] gcloud builds submit exited with ${SUBMIT_RC} (checking if build is still running after ECPProxyError)..." >&2
  sleep 5
  BUILD_ID="$(gcloud builds list \
    --project="${PROJECT_ID}" \
    --filter="substitutions._SERVICE='${SVC}' AND substitutions._SHA='${SHA}'" \
    --limit=1 \
    --format="value(id)")"
  if [[ -z "${BUILD_ID}" ]]; then
    echo "ERROR: Could not find Cloud Build for ${SVC}:${SHA}" >&2
    exit 1
  fi
  echo "[build_image] Polling Cloud Build ID ${BUILD_ID} ..." >&2
  while true; do
    STATUS="$(gcloud builds describe "${BUILD_ID}" --project="${PROJECT_ID}" --format="value(status)")"
    echo "[build_image] Build ${BUILD_ID} status: ${STATUS}" >&2
    case "${STATUS}" in
      SUCCESS)
        break
        ;;
      WORKING|QUEUED|PENDING)
        sleep 10
        ;;
      *)
        echo "ERROR: Cloud Build ${BUILD_ID} finished with status ${STATUS}" >&2
        exit 1
        ;;
    esac
  done
fi

DIGEST="$(gcloud artifacts docker images describe "${IMAGE_TAG}" \
  --project="${PROJECT_ID}" \
  --format="value(image_summary.digest)")"

if [[ -z "${DIGEST}" ]]; then
  echo "ERROR: Failed to resolve digest for ${IMAGE_TAG}" >&2
  exit 1
fi

IMMUTABLE_IMAGE="${AR_REPO}/${SVC}@${DIGEST}"
echo "[build_image] Built ${IMMUTABLE_IMAGE} (tag ${IMAGE_TAG})" >&2

if [[ "${PIN_TFVARS}" == "true" ]]; then
  python3 "${SCRIPT_DIR}/pin_image.py" --root "${TF_ROOT}" --service "${SVC}" --image "${IMMUTABLE_IMAGE}" >&2
fi

# Print the immutable digest URI to stdout for callers
echo "${IMMUTABLE_IMAGE}"
