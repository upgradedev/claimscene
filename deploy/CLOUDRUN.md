# ClaimScene on Google Cloud Run

One container, one port. FastAPI serves both the JSON API **and** the compiled
web client (the `frontend/` React SPA, built with Vite and mounted as static
files — the Dockerfile builds it into the image). Cloud Run scales it to
**zero** when idle.

| Setting | Value |
|---|---|
| GCP project | `upgradegr-claimscene` (parameterise `PROJECT_ID`) |
| Region | `europe-west1` |
| Service | `claimscene` |
| Artifact Registry repo | `claimscene` (docker) |
| Port | `8000` |
| Auth | public (`--allow-unauthenticated`) |
| Request timeout | `600s` (`--timeout 600` — see note) |

> **Why 600s:** a live illustration is a two-step generation (an establish-shot
> still, then an image-to-video clip chained from it) that runs for minutes.
> Cloud Run's 300s default would return **504** to the client while the case
> completes server-side. The deploy script pins `--timeout 600` from the start
> so a synchronous `POST /cases/render` outlives the real generation path.
> (In offline mode render is near-instant.)

## Prerequisites (one-time)

```bash
gcloud auth login
gcloud config set project upgradegr-claimscene   # or your PROJECT_ID
```

The deploy script enables the required APIs itself (`run`, `cloudbuild`,
`artifactregistry`) and creates the Artifact Registry repo if missing.

## Deploy — OFFLINE (default, zero credentials)

Runs the full review-adjust flow with deterministic fakes; no B2 / GMI creds
needed. Good for a working public URL and to validate the hosting pipeline.

```bash
bash deploy/deploy-cloudrun.sh
```

The script prints the service URL. Verify it serves:

```bash
URL="$(gcloud run services describe claimscene --region europe-west1 \
        --format 'value(status.url)')"

curl -s "$URL/health"        # {"status":"ok","mode":"offline",...}
curl -s "$URL/scenarios" | head -c 200      # committed sample scenarios
curl -s -o /dev/null -w '%{http_code}\n' "$URL/"   # 200 (React SPA index)
```

## Deploy — LIVE cutover (gated)

The live path extracts scenes with the VLM ladder, renders real illustrations
with Genblaze/GMI Cloud, and stores every artifact on Backblaze B2. It is a
one-command redeploy with creds attached:

```bash
CLAIMSCENE_MODE=live \
B2_APPLICATION_KEY_ID='<b2 key id>' \
B2_APPLICATION_KEY='<b2 app key>' \
B2_BUCKET_NAME='claimscene' \
B2_S3_ENDPOINT='https://s3.<region>.backblazeb2.com' \
GMI_API_KEY='<gmi cloud key>' \
  bash deploy/deploy-cloudrun.sh
```

`ffmpeg` is already installed in the image, so the animated schematic MP4
encodes in both modes.

> The script **rebuilds the image from local source**. Run `git pull` on `main`
> before the cutover so the rebuilt image carries the latest code.

### Done: the `claimscene` B2 bucket (provisioned + live)

The Backblaze B2 bucket `claimscene` and an application key **scoped to it**
with `writeFiles` / `readFiles` are provisioned (the presign path needs SigV4 +
the correct region — both are wired in `b2_storage.py`). The live deploy runs
against them: verified 2026-07-23, health reports `mode=live`,
`storage=B2Storage`, and a live case wrote real objects — a seedream
establishing still and a pixverse illustration clip — to the bucket.

Fallback behaviour (documented, not the current state): if the entitled key is
ever absent, live mode transparently degrades storage to the offline object
store (health then reports `storage=InMemoryStorage`) — so a live deploy never
500s, it simply would not persist to B2.

## Optional: Firebase Hosting mirror

`firebase.json` + `.firebaserc` mirror the single-origin contract on Firebase
Hosting, rewriting `/health`, `/scenarios` and `/cases/**` to the Cloud Run
service. Build the client, then deploy:

```bash
cd frontend && npm run build && cd ..
firebase deploy --only hosting     # uses .firebaserc project
```

The Cloud Run container already serves the client itself, so the Firebase
mirror is optional (a CDN front door, not required).

## Cost profile

Scales to zero — **no idle cost**. Practical demo traffic on Cloud Run costs
**~$0–2/month**; Artifact Registry ~$0.10/GB-month for one small image.
