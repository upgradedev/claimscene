#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Stage ClaimScene's live secrets into Google Secret Manager. Run this once, and
# again on every key rotation. This is the ONLY place raw secret values are
# handled: they are read from YOUR shell env, written straight to Secret Manager,
# and never printed, committed, logged, or placed on the Cloud Run service spec.
#
# Usage (NEBIUS_INFERENCE_API_KEY is optional — the VLM fallback rung):
#   B2_APPLICATION_KEY_ID=... B2_APPLICATION_KEY=... GMI_API_KEY=... \
#   [NEBIUS_INFERENCE_API_KEY=...] \
#     bash deploy/stage-secrets.sh
#
# Then deploy references them by name (no secret values needed):
#   CLAIMSCENE_MODE=live B2_BUCKET_NAME=claimscene B2_S3_ENDPOINT=... \
#     bash deploy/deploy-cloudrun.sh
#
# To rotate: create new keys in the GMI Cloud / Backblaze dashboards, re-run this
# with the new values (adds a new version), redeploy, verify /health, then revoke
# the old keys. Cloud Run reads `:latest`, so a redeploy picks up the new version.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-upgradegr-claimscene}"
B2_APPLICATION_KEY_SECRET="${B2_APPLICATION_KEY_SECRET:-claimscene-b2-application-key}"
B2_APPLICATION_KEY_ID_SECRET="${B2_APPLICATION_KEY_ID_SECRET:-claimscene-b2-application-key-id}"
GMI_API_KEY_SECRET="${GMI_API_KEY_SECRET:-claimscene-gmi-api-key}"
NEBIUS_INFERENCE_API_KEY_SECRET="${NEBIUS_INFERENCE_API_KEY_SECRET:-claimscene-nebius-inference-api-key}"

: "${B2_APPLICATION_KEY_ID:?set B2_APPLICATION_KEY_ID in your shell env}"
: "${B2_APPLICATION_KEY:?set B2_APPLICATION_KEY in your shell env}"
: "${GMI_API_KEY:?set GMI_API_KEY in your shell env}"

gcloud config set project "${PROJECT_ID}" >/dev/null
gcloud services enable secretmanager.googleapis.com --project "${PROJECT_ID}"

stage() {  # $1 = secret name, $2 = value
  local name="$1" value="$2"
  if ! gcloud secrets describe "${name}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud secrets create "${name}" --replication-policy=automatic --project "${PROJECT_ID}" >/dev/null
  fi
  # printf %s → no trailing newline (a stray \n silently breaks the credential).
  printf %s "${value}" | gcloud secrets versions add "${name}" --data-file=- --project "${PROJECT_ID}" >/dev/null
  echo "✓ staged ${name} (new version)"
}

stage "${B2_APPLICATION_KEY_ID_SECRET}" "${B2_APPLICATION_KEY_ID}"
stage "${B2_APPLICATION_KEY_SECRET}"    "${B2_APPLICATION_KEY}"
stage "${GMI_API_KEY_SECRET}"           "${GMI_API_KEY}"

# Optional Nebius VLM-fallback key — staged only if provided.
if [ -n "${NEBIUS_INFERENCE_API_KEY:-}" ]; then
  stage "${NEBIUS_INFERENCE_API_KEY_SECRET}" "${NEBIUS_INFERENCE_API_KEY}"
fi

echo "✓ all secrets staged for project ${PROJECT_ID}"
echo "  deploy:  CLAIMSCENE_MODE=live B2_BUCKET_NAME=claimscene B2_S3_ENDPOINT=... bash deploy/deploy-cloudrun.sh"
