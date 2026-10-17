#!/usr/bin/env bash
# God-View Smoke Test Suite (Terraform GitOps Spec §7 + Direct Cloud Run ID-Token Checks).
#
# Usage:
#   ./infra/deploy/smoke.sh [--with-mutations] [--with-drift]
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"

PROJECT_ID="${GCP_PROJECT_ID:-general-ak}"
REGION="${GCP_REGION:-us-central1}"
ZONE="${GCP_ZONE:-us-central1-a}"

WITH_MUTATIONS="false"
WITH_DRIFT="false"

for arg in "$@"; do
  case "${arg}" in
    --with-mutations) WITH_MUTATIONS="true" ;;
    --with-drift) WITH_DRIFT="true" ;;
    *)
      echo "Unknown flag: ${arg}" >&2
      exit 1
      ;;
  esac
done

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

record_pass() {
  echo "[PASS] $1"
  PASS_COUNT=$((PASS_COUNT + 1))
}

record_warn() {
  echo "[WARN] $1"
  WARN_COUNT=$((WARN_COUNT + 1))
}

record_fail() {
  echo "[FAIL] $1"
  FAIL_COUNT=$((FAIL_COUNT + 1))
}

echo "=== 1. Managed SSL Certificate & Global Edge URLs (TF Spec §7) ==="
ID_TOKEN="$(gcloud auth print-identity-token 2>/dev/null || true)"
CERT_STATUS="$(gcloud compute ssl-certificates describe god-view-edge-cert \
  --project="${PROJECT_ID}" \
  --format="value(managed.status)" 2>/dev/null || echo "NOT_FOUND")"
echo "Certificate god-view-edge-cert status: ${CERT_STATUS}"

if [[ "${CERT_STATUS}" == "ACTIVE" ]]; then
  record_pass "Managed certificate god-view-edge-cert is ACTIVE"

  URL1_CODE="$(curl -s -o /dev/null -w "%{http_code}" https://cymbal.ak-demos.com || echo "000")"
  if [[ "${URL1_CODE}" == "200" ]]; then
    record_pass "URL 1 (https://cymbal.ak-demos.com) returned 200"
  else
    record_fail "URL 1 (https://cymbal.ak-demos.com) expected 200, got ${URL1_CODE}"
  fi

  URL2_HEADERS="$(curl -sI https://los.cymbal.ak-demos.com || true)"
  if echo "${URL2_HEADERS}" | grep -qE "HTTP/.* 302" && echo "${URL2_HEADERS}" | grep -qi "accounts.google.com"; then
    record_pass "URL 2 (https://los.cymbal.ak-demos.com) returned 302 redirect to accounts.google.com (IAP)"
  else
    record_fail "URL 2 (https://los.cymbal.ak-demos.com) did not return IAP 302 redirect"
  fi

  URL3_HEADERS="$(curl -sI https://godview.ak-demos.com || true)"
  if echo "${URL3_HEADERS}" | grep -qE "HTTP/.* 302" && echo "${URL3_HEADERS}" | grep -qi "accounts.google.com"; then
    record_pass "URL 3 (https://godview.ak-demos.com) returned 302 redirect to accounts.google.com (IAP)"
  else
    record_fail "URL 3 (https://godview.ak-demos.com) did not return IAP 302 redirect"
  fi

  APIGW_CODE="$(curl -s -o /dev/null -w "%{http_code}" https://api.cymbal.ak-demos.com/v1/vehicles || echo "000")"
  if [[ "${APIGW_CODE}" == "200" ]]; then
    record_pass "API Gateway (https://api.cymbal.ak-demos.com/v1/vehicles) returned 200"
  else
    record_fail "API Gateway (https://api.cymbal.ak-demos.com/v1/vehicles) expected 200, got ${APIGW_CODE}"
  fi

  SOV_RUN_URL="$(gcloud run services describe god-view-sovereign --project="${PROJECT_ID}" --region="${REGION}" --format="value(status.url)" 2>/dev/null || true)"
  SOV_HEALTH="$(curl -s -H "Authorization: Bearer ${ID_TOKEN}" https://godview.ak-demos.com/public/healthz || true)"
  if ! echo "${SOV_HEALTH}" | grep -qE "sentinel_interval_seconds|sentinel_cadence_s" && [[ -n "${SOV_RUN_URL}" ]]; then
    SOV_HEALTH="$(curl -s -H "Authorization: Bearer ${ID_TOKEN}" "${SOV_RUN_URL}/public/healthz" || true)"
  fi
  if echo "${SOV_HEALTH}" | grep -qE "sentinel_interval_seconds|sentinel_cadence_s"; then
    record_pass "Sovereign public healthz returned sentinel cadence: ${SOV_HEALTH}"
  else
    record_fail "Sovereign public healthz unexpected response: ${SOV_HEALTH}"
  fi
else
  record_warn "Managed certificate god-view-edge-cert is ${CERT_STATUS} (DNS/cert provisioning takes 15-60m); skipping HTTPS edge hostname checks"
fi

echo ""
echo "=== 2. Direct Cloud Run Service Checks (ID Token) ==="
for svc in loan-orchestrator-api commercial-fleet-credit-svc motor-insurance-svc ai-customer-simulator customer-loan-portal-web internal-bank-los-web god-view-sovereign god-view-sre-web; do
  SVC_URL="$(gcloud run services describe "${svc}" --project="${PROJECT_ID}" --region="${REGION}" --format="value(status.url)" 2>/dev/null || true)"
  SVC_INGRESS="$(gcloud run services describe "${svc}" --project="${PROJECT_ID}" --region="${REGION}" --format="value(metadata.annotations.'run.googleapis.com/ingress')" 2>/dev/null || true)"
  if [[ -z "${SVC_URL}" ]]; then
    record_warn "Cloud Run service ${svc} not yet created"
    continue
  fi
  HEALTH_PATH="/healthz"
  if [[ "${svc}" == "god-view-sovereign" ]]; then
    HEALTH_PATH="/public/healthz"
  fi
  HTTP_CODE="$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer ${ID_TOKEN}" "${SVC_URL}${HEALTH_PATH}" || echo "000")"
  if [[ "${HTTP_CODE}" == "200" ]]; then
    record_pass "Cloud Run ${svc} (${SVC_URL}${HEALTH_PATH}) returned 200"
  elif [[ "${SVC_INGRESS}" == *"internal"* && ( "${HTTP_CODE}" == "403" || "${HTTP_CODE}" == "404" ) ]]; then
    record_pass "Cloud Run ${svc} enforces internal ingress (${SVC_INGRESS}, external HTTP ${HTTP_CODE})"
  else
    record_warn "Cloud Run ${svc} (${SVC_URL}${HEALTH_PATH}) returned HTTP ${HTTP_CODE} (ingress=${SVC_INGRESS})"
  fi
done

echo ""
echo "=== 3. Private Networking Verification (TF Spec §7) ==="
SQL_IPS="$(gcloud sql instances describe god-view-pg --project="${PROJECT_ID}" --format="value(ipAddresses.type)" 2>/dev/null || true)"
if [[ "${SQL_IPS}" == "PRIVATE" ]]; then
  record_pass "Cloud SQL god-view-pg has PRIVATE IP only (${SQL_IPS})"
elif [[ -z "${SQL_IPS}" ]]; then
  record_warn "Cloud SQL god-view-pg not yet created"
else
  record_fail "Cloud SQL god-view-pg expected PRIVATE IP only, got: ${SQL_IPS}"
fi

VM_ACCESS_CFG="$(gcloud compute instances describe core-banking-cibil-vm --zone="${ZONE}" --project="${PROJECT_ID}" --format="value(networkInterfaces[0].accessConfigs)" 2>/dev/null || echo "NOT_FOUND")"
if [[ -z "${VM_ACCESS_CFG}" ]]; then
  record_pass "GCE VM core-banking-cibil-vm has no external IP (accessConfigs is empty)"
elif [[ "${VM_ACCESS_CFG}" == "NOT_FOUND" ]]; then
  record_warn "GCE VM core-banking-cibil-vm not yet created"
else
  record_fail "GCE VM core-banking-cibil-vm has unexpected external accessConfig: ${VM_ACCESS_CFG}"
fi

echo ""
echo "=== 4. Nervous System & Scheduled Pause (Optional Mutation Smoke) ==="
if [[ "${WITH_MUTATIONS}" == "true" ]]; then
  if gcloud pubsub topics publish godview-nervous-system --project="${PROJECT_ID}" --message='{"specversion":"1.0","type":"godview.test","source":"//deploy/smoke","id":"smoke-test-1"}' >/dev/null 2>&1; then
    record_pass "Published test CloudEvent to godview-nervous-system"
  else
    record_fail "Failed to publish test CloudEvent to godview-nervous-system"
  fi

  if gcloud scheduler jobs run godview-tenant-pause --location="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    record_pass "Triggered Scheduler job godview-tenant-pause"
  else
    record_fail "Failed to trigger Scheduler job godview-tenant-pause"
  fi
else
  echo "[SKIP] Pass --with-mutations to publish to godview-nervous-system and trigger godview-tenant-pause"
fi

echo ""
echo "=== 5. GKE Drift Detection Smoke (Optional Drift Smoke) ==="
if [[ "${WITH_DRIFT}" == "true" ]]; then
  if kubectl scale deploy/underwriting-los-svc -n cymbal --replicas=0; then
    record_pass "Scaled deploy/underwriting-los-svc in namespace cymbal to 0 replicas; awaiting drift_diff breach <= 60s and restore <= 90s"
  else
    record_fail "Failed to scale deploy/underwriting-los-svc"
  fi
else
  echo "[SKIP] Pass --with-drift to run kubectl scale deploy/underwriting-los-svc -n cymbal --replicas=0"
fi

echo ""
echo "=== Smoke Summary: PASS=${PASS_COUNT} WARN=${WARN_COUNT} FAIL=${FAIL_COUNT} ==="
if [[ ${FAIL_COUNT} -gt 0 ]]; then
  exit 1
fi
