#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Turnkey Cloud Run deploy for ClaimScene.
#
#   • builds the image with Cloud Build (no local Docker — cloud-first)
#   • pushes to Artifact Registry
#   • deploys a public Cloud Run service on port 8000
#
# OFFLINE (default) needs ZERO credentials — the app runs the full review-adjust
# flow with deterministic fakes. For a LIVE deploy the secret values (B2
# application key + id, GMI key, and the optional Nebius VLM-fallback key) live
# in Google Secret Manager, NOT on the service spec — so
# `gcloud run services describe` never prints them, and nobody with only
# run.services.get IAM can read them. Stage them once (and on every rotation)
# with deploy/stage-secrets.sh; this deploy references them by name only.
#
# Usage:
#   bash deploy/deploy-cloudrun.sh                              # offline (default)
#
#   # live — stage the secrets first (that script handles the raw values), then:
#   B2_APPLICATION_KEY_ID=... B2_APPLICATION_KEY=... GMI_API_KEY=... \
#     bash deploy/stage-secrets.sh                              # one-time / on rotation
#   CLAIMSCENE_MODE=live B2_BUCKET_NAME=claimscene B2_S3_ENDPOINT=... \
#     bash deploy/deploy-cloudrun.sh                            # live deploy — NO secret values here
#
# The deploy step needs only the NON-secret live config (bucket, endpoint, and
# optionally B2_REGION / NEBIUS_INFERENCE_BASE_URL); the secret values are pulled
# from Secret Manager by Cloud Run at runtime.
#
# Knobs (env vars, sane defaults):
#   PROJECT_ID REGION SERVICE AR_REPO IMAGE_TAG CLAIMSCENE_MODE RUNTIME_SA
#   B2_APPLICATION_KEY_SECRET B2_APPLICATION_KEY_ID_SECRET GMI_API_KEY_SECRET
#   NEBIUS_INFERENCE_API_KEY_SECRET
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-upgradegr-claimscene}"
REGION="${REGION:-europe-west1}"
SERVICE="${SERVICE:-claimscene}"
AR_REPO="${AR_REPO:-claimscene}"
IMAGE_TAG="${IMAGE_TAG:-$(date +%Y%m%d-%H%M%S)}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/claimscene:${IMAGE_TAG}"

CLAIMSCENE_MODE="${CLAIMSCENE_MODE:-offline}"

# Secret Manager secret names (override only if you named them differently).
B2_APPLICATION_KEY_SECRET="${B2_APPLICATION_KEY_SECRET:-claimscene-b2-application-key}"
B2_APPLICATION_KEY_ID_SECRET="${B2_APPLICATION_KEY_ID_SECRET:-claimscene-b2-application-key-id}"
GMI_API_KEY_SECRET="${GMI_API_KEY_SECRET:-claimscene-gmi-api-key}"
NEBIUS_INFERENCE_API_KEY_SECRET="${NEBIUS_INFERENCE_API_KEY_SECRET:-claimscene-nebius-inference-api-key}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "▶ project=${PROJECT_ID}  region=${REGION}  service=${SERVICE}  mode=${CLAIMSCENE_MODE}"
echo "▶ image=${IMAGE}"

# ── 1. Target project + required APIs ────────────────────────────────────────
# DEPLOY_SKIP_PROVISIONING=1 skips the one-time setup below. CI sets it so the
# deploy identity can stay least-privilege: enabling services and creating
# registries need project-admin rights that a deployer has no business holding
# for a step that only ever has to run once. An owner runs this script without
# the flag to provision; CI runs it with the flag to deploy.
SKIP_PROVISIONING="${DEPLOY_SKIP_PROVISIONING:-0}"

gcloud config set project "${PROJECT_ID}" >/dev/null
if [ "${SKIP_PROVISIONING}" != "1" ]; then
  gcloud services enable \
    run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
    secretmanager.googleapis.com \
    --project "${PROJECT_ID}"
fi

# ── 2. Artifact Registry repo (idempotent) ───────────────────────────────────
if [ "${SKIP_PROVISIONING}" != "1" ] && ! gcloud artifacts repositories describe "${AR_REPO}" \
      --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  echo "▶ creating Artifact Registry repo '${AR_REPO}' in ${REGION}"
  gcloud artifacts repositories create "${AR_REPO}" \
    --repository-format docker --location "${REGION}" \
    --description "ClaimScene container images" --project "${PROJECT_ID}"
fi

# ── 3. Build the image with Cloud Build ──────────────────────────────────────
# Stamp the commit being built so GET /health can prove which one is live.
# Falls back to "unknown" outside a git checkout rather than inventing a hash.
BUILD_SHA="$(git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null || echo unknown)"
if ! git -C "${REPO_ROOT}" diff --quiet HEAD 2>/dev/null; then
  BUILD_SHA="${BUILD_SHA}-dirty"   # honest: the tree did not match the commit
fi
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "▶ build sha=${BUILD_SHA} time=${BUILD_TIME}"

# Naming the staging bucket explicitly is what lets the CI identity stay
# least-privilege. Left to itself, `builds submit` resolves (and would create)
# the default `<project>_cloudbuild` bucket through a Service Usage call, and
# fails with a misleading "forbidden from accessing the bucket" unless the
# caller holds Service Usage Admin -- misleading because the bucket is readable
# and writable; it is the resolution step that is gated. Pointing at the bucket
# that already exists skips that path entirely. Measured, not guessed: as the
# deploy service account, `gcloud storage ls` on the bucket succeeds while a
# plain submit fails, and the same submit succeeds with this flag.
BUILD_SOURCE_ARGS=()
if [ "${SKIP_PROVISIONING}" = "1" ]; then
  BUILD_SOURCE_ARGS=(--gcs-source-staging-dir "gs://${PROJECT_ID}_cloudbuild/source")
fi

gcloud builds submit "${REPO_ROOT}" \
  --config "${REPO_ROOT}/deploy/cloudbuild.yaml" \
  --substitutions "_IMAGE=${IMAGE},_BUILD_SHA=${BUILD_SHA},_BUILD_TIME=${BUILD_TIME}" \
  "${BUILD_SOURCE_ARGS[@]}" \
  --project "${PROJECT_ID}"

# ── 4. Runtime config: non-secrets as env vars, secrets from Secret Manager ──
ENV_VARS="CLAIMSCENE_MODE=${CLAIMSCENE_MODE}"
SET_SECRETS_ARGS=()

if [ "${CLAIMSCENE_MODE}" = "live" ]; then
  : "${B2_BUCKET_NAME:?live mode needs B2_BUCKET_NAME (non-secret)}"
  : "${B2_S3_ENDPOINT:?live mode needs B2_S3_ENDPOINT (non-secret)}"
  ENV_VARS="${ENV_VARS},B2_BUCKET_NAME=${B2_BUCKET_NAME},B2_S3_ENDPOINT=${B2_S3_ENDPOINT}"
  # Optional non-secret live config.
  [ -n "${B2_REGION:-}" ] && ENV_VARS="${ENV_VARS},B2_REGION=${B2_REGION}"
  [ -n "${NEBIUS_INFERENCE_BASE_URL:-}" ] && \
    ENV_VARS="${ENV_VARS},NEBIUS_INFERENCE_BASE_URL=${NEBIUS_INFERENCE_BASE_URL}"

  # Cloud Run's runtime service account must be allowed to read each secret.
  # Only needed for the IAM grant below, which CI skips, so do not spend an
  # API call (or require the permission for it) on a deploy that will not use it.
  runtime_sa=""
  if [ "${SKIP_PROVISIONING}" != "1" ]; then
    runtime_sa="${RUNTIME_SA:-$(gcloud projects describe "${PROJECT_ID}" \
      --format='value(projectNumber)')-compute@developer.gserviceaccount.com}"
  fi

  # env-var name → Secret Manager secret name (three required).
  secret_map=(
    "B2_APPLICATION_KEY=${B2_APPLICATION_KEY_SECRET}"
    "B2_APPLICATION_KEY_ID=${B2_APPLICATION_KEY_ID_SECRET}"
    "GMI_API_KEY=${GMI_API_KEY_SECRET}"
  )
  # Optional Nebius VLM-fallback rung — wired only if its secret is staged.
  if gcloud secrets describe "${NEBIUS_INFERENCE_API_KEY_SECRET}" \
        --project "${PROJECT_ID}" >/dev/null 2>&1; then
    secret_map+=("NEBIUS_INFERENCE_API_KEY=${NEBIUS_INFERENCE_API_KEY_SECRET}")
  fi

  set_secrets=""
  for pair in "${secret_map[@]}"; do
    env_name="${pair%%=*}"; secret_name="${pair#*=}"
    if ! gcloud secrets describe "${secret_name}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
      echo "✗ Secret Manager secret '${secret_name}' not found." >&2
      echo "  Stage it first:  B2_APPLICATION_KEY_ID=... B2_APPLICATION_KEY=... GMI_API_KEY=... bash deploy/stage-secrets.sh" >&2
      exit 1
    fi
    # Granting the runtime account read access is one-time setup, so CI skips
    # it and never needs admin on a secret. The existence check above still
    # runs in CI (read-only), because "the secret is missing" is worth failing
    # on and is exactly the kind of thing a deploy should not discover late.
    if [ "${SKIP_PROVISIONING}" != "1" ]; then
      gcloud secrets add-iam-policy-binding "${secret_name}" \
        --member="serviceAccount:${runtime_sa}" \
        --role="roles/secretmanager.secretAccessor" \
        --project "${PROJECT_ID}" >/dev/null
    fi
    set_secrets="${set_secrets:+${set_secrets},}${env_name}=${secret_name}:latest"
  done
  SET_SECRETS_ARGS=(--set-secrets "${set_secrets}")
fi

# ── 5. Deploy to Cloud Run (public, port 8000, scales to zero) ───────────────
# --timeout 600 from the start: a live illustration (still + image-to-video
# clip) runs for minutes; the default 300s edge deadline would 504 the
# synchronous POST /cases/render while the case completes server-side.
# --no-cpu-throttling: POST /cases/render/jobs runs the actual two-step
# generation in a background thread AFTER the request that submitted it has
# already returned (see src/claimscene/jobs.py) — by default Cloud Run only
# allocates CPU while a request is in flight, which would starve that thread
# between polls. See deploy/CLOUDRUN.md for the full async-job note.
gcloud run deploy "${SERVICE}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --port 8000 \
  --timeout 600 \
  --cpu 1 --memory 1Gi \
  --no-cpu-throttling \
  --min-instances 0 --max-instances 4 \
  --set-env-vars "${ENV_VARS}" \
  ${SET_SECRETS_ARGS[@]+"${SET_SECRETS_ARGS[@]}"} \
  --project "${PROJECT_ID}"

URL="$(gcloud run services describe "${SERVICE}" --region "${REGION}" \
        --project "${PROJECT_ID}" --format 'value(status.url)')"
echo "✓ deployed: ${URL}"
echo "  health : ${URL}/health"
echo "  webapp : ${URL}/"
