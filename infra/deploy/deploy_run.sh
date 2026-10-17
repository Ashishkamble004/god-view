#!/usr/bin/env bash
# Deploy an image to an existing Terraform-managed Cloud Run service.
# IMPORTANT: Passes ONLY --region and --image so all Terraform-managed revision
# configuration (scaling, env vars, secrets, VPC egress, service account, ingress)
# is preserved. Refuses to mutate any service not labelled godview_managed="true".
#
# Usage:
#   ./infra/deploy/deploy_run.sh <svc> <image>
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <service-name> <image-uri>" >&2
  exit 1
fi

SVC="$1"
IMAGE="$2"
PROJECT_ID="${GCP_PROJECT_ID:-general-ak}"
REGION="${GCP_REGION:-us-central1}"

# Safety check: verify service exists and carries label godview_managed="true"
MANAGED_LABEL="$(gcloud run services describe "${SVC}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --format="value(metadata.labels.godview_managed)" 2>/dev/null || true)"

if [[ "${MANAGED_LABEL}" != "true" ]]; then
  echo "ERROR: Cloud Run service '${SVC}' does not exist or lacks label godview_managed=true (found '${MANAGED_LABEL}'). Refusing to deploy." >&2
  exit 1
fi

echo "[deploy_run] Deploying ${SVC} with image ${IMAGE} in ${REGION}..." >&2
gcloud run deploy "${SVC}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --quiet >&2

REVISION="$(gcloud run services describe "${SVC}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --format="value(status.latestReadyRevisionName)")"
URL="$(gcloud run services describe "${SVC}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --format="value(status.url)")"

echo "[deploy_run] Deployed ${SVC}: revision=${REVISION} url=${URL}" >&2
echo "${SVC} ${REVISION} ${URL}"
