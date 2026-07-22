# ClaimScene — single deployable container (Cloud Run / Container Apps / Fly).
#
# One container serves BOTH the FastAPI API AND the compiled web client on one
# port. Stage 1 builds the React/Vite SPA (frontend/ → dist); stage 2 is the
# Python runtime, and FastAPI serves the built client as static files (see
# claimscene.api: it mounts CLAIMSCENE_WEB_DIR). ffmpeg is installed so the
# animated schematic MP4 (the factual layer) encodes in every deployment.
#
# The image builds with ZERO credentials and boots OFFLINE by default
# (health → mode=offline). The SAME image runs live when B2/GMI creds are set
# at runtime (CLAIMSCENE_MODE=live) — the [live] extra is installed so it can.

# ── Stage 1: build the web client (Vite React SPA) ───────────────────────────
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
RUN npm run build          # tsc --noEmit && vite build → /web/dist

# ── Stage 2: Python runtime (FastAPI + ffmpeg for the schematic animation) ───
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md requirements.txt ./
COPY src ./src
# [server] = FastAPI + uvicorn + python-multipart (the API surface).
# [live]   = boto3 + genblaze[gmicloud] + openai (real B2 / VLM / Genblaze).
# Installing both lets one image run either mode, chosen at runtime.
RUN pip install --no-cache-dir ".[server,live]"

# Committed synthetic scenarios (served by /scenarios; the zero-photo demo).
COPY eval/scenarios ./eval/scenarios
# Compiled web client (index.html lives inside dist/).
COPY --from=web /web/dist ./web

ENV CLAIMSCENE_MODE=offline \
    CLAIMSCENE_WEB_DIR=/app/web \
    CLAIMSCENE_SCENARIOS_DIR=/app/eval/scenarios \
    PORT=8000
EXPOSE 8000

# Shell form so ${PORT} (set by Cloud Run) is honoured; defaults to 8000 local.
CMD ["sh", "-c", "uvicorn claimscene.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
