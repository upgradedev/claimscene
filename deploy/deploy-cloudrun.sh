#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Turnkey Cloud Run deploy for ClaimScene.
#
#   • builds the image with Cloud Build (no local Docker — cloud-first)
#   • pushes to Artifact Registry
#   • deploys a public Cloud Run service on port 8000
#
# OFFLINE (default) needs ZERO credentials — the app runs the full review-adjust
# flow with deterministic fakes. Flip to LIVE by passing the creds below.
#
# Usage:
#   bash deploy/deploy-cloudrun.sh                 # offline deploy (default)
#   CLAIMSCENE_MODE=live \
#   B2_APPLICATION_KEY_ID=... B2_APPLICATION_KEY=... \
#   B2_BUCKET_NAME=claimscene B2_S3_ENDPOINT=... GMI_API_KEY=... \
#     bash deploy/deploy-cloudrun.sh               # live deploy
#
# Knobs (all env vars with sane defaults):
#   PROJECT_ID REGION SERVICE AR_REPO IMAGE_TAG CLAIMSCENE_MODE
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-upgradegr-claimscene}"
REGION="${REGION:-europe-west1}"
SERVICE="${SERVICE:-claimscene}"
AR_REPO="${AR_REPO:-claimscene}"
IMAGE_TAG="${IMAGE_TAG:-$(date +%Y%m%d-%H%M%S)}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/claimscene:${IMAGE_TAG}"

CLAIMSCENE_MODE="${CLAIMSCENE_MODE:-offline}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "▶ project=${PROJECT_ID}  region=${REGION}  service=${SERVICE}  mode=${CLAIMSCENE_MODE}"
echo "▶ image=${IMAGE}"

# ── 1. Target project + required APIs ────────────────────────────────────────
gcloud config set project "${PROJECT_ID}" >/dev/null
gcloud services enable \
  run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  --project "${PROJECT_ID}"

# ── 2. Artifact Registry repo (idempotent) ───────────────────────────────────
if ! gcloud artifacts repositories describe "${AR_REPO}" \
      --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  echo "▶ creating Artifact Registry repo '${AR_REPO}' in ${REGION}"
  gcloud artifacts repositories create "${AR_REPO}" \
    --repository-format docker --location "${REGION}" \
    --description "ClaimScene container images" --project "${PROJECT_ID}"
fi

# ── 3. Build the image with Cloud Build ──────────────────────────────────────
gcloud builds submit "${REPO_ROOT}" \
  --config "${REPO_ROOT}/deploy/cloudbuild.yaml" \
  --substitutions "_IMAGE=${IMAGE}" \
  --project "${PROJECT_ID}"

# ── 4. Assemble runtime env vars for the chosen mode ─────────────────────────
ENV_VARS="CLAIMSCENE_MODE=${CLAIMSCENE_MODE}"

if [ "${CLAIMSCENE_MODE}" = "live" ]; then
  : "${B2_APPLICATION_KEY_ID:?live mode needs B2_APPLICATION_KEY_ID}"
  : "${B2_APPLICATION_KEY:?live mode needs B2_APPLICATION_KEY}"
  : "${B2_BUCKET_NAME:?live mode needs B2_BUCKET_NAME}"
  : "${B2_S3_ENDPOINT:?live mode needs B2_S3_ENDPOINT}"
  : "${GMI_API_KEY:?live mode needs GMI_API_KEY}"
  ENV_VARS="${ENV_VARS},B2_APPLICATION_KEY_ID=${B2_APPLICATION_KEY_ID}"
  ENV_VARS="${ENV_VARS},B2_APPLICATION_KEY=${B2_APPLICATION_KEY}"
  ENV_VARS="${ENV_VARS},B2_BUCKET_NAME=${B2_BUCKET_NAME}"
  ENV_VARS="${ENV_VARS},B2_S3_ENDPOINT=${B2_S3_ENDPOINT}"
  ENV_VARS="${ENV_VARS},GMI_API_KEY=${GMI_API_KEY}"
  # Optional B2 region override (else derived from the endpoint host).
  [ -n "${B2_REGION:-}" ] && ENV_VARS="${ENV_VARS},B2_REGION=${B2_REGION}"
  # Optional Nebius VLM fallback rung.
  [ -n "${NEBIUS_INFERENCE_API_KEY:-}" ] && \
    ENV_VARS="${ENV_VARS},NEBIUS_INFERENCE_API_KEY=${NEBIUS_INFERENCE_API_KEY},NEBIUS_INFERENCE_BASE_URL=${NEBIUS_INFERENCE_BASE_URL:-}"
fi

# ── 5. Deploy to Cloud Run (public, port 8000, scales to zero) ───────────────
# --timeout 600 from the start: a live illustration (still + image-to-video
# clip) runs for minutes; the default 300s edge deadline would 504 the
# synchronous POST /cases/render while the case completes server-side.
gcloud run deploy "${SERVICE}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --port 8000 \
  --timeout 600 \
  --cpu 1 --memory 1Gi \
  --min-instances 0 --max-instances 4 \
  --set-env-vars "${ENV_VARS}" \
  --project "${PROJECT_ID}"

URL="$(gcloud run services describe "${SERVICE}" --region "${REGION}" \
        --project "${PROJECT_ID}" --format 'value(status.url)')"
echo "✓ deployed: ${URL}"
echo "  health : ${URL}/health"
echo "  webapp : ${URL}/"
