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
| CPU allocation | always-on (`--no-cpu-throttling` — see async-job note below) |

> **Why 600s:** a live illustration is a two-step generation (an establish-shot
> still, then an image-to-video clip chained from it) that runs for minutes.
> Cloud Run's 300s default would return **504** to the client while the case
> completes server-side. The deploy script pins `--timeout 600` from the start
> so a synchronous `POST /cases/render` outlives the real generation path.
> (In offline mode render is near-instant.)

> **Why `--no-cpu-throttling`:** by default Cloud Run only allocates CPU to an
> instance while it has a request in flight — the moment a response is sent,
> CPU is throttled to near-zero. `POST /cases/render/jobs` (async submit +
> poll — see below) returns its `202` immediately and keeps rendering in a
> **background thread** after that response has already gone out. Without
> `--no-cpu-throttling` that thread would barely progress between polls. This
> only affects billing while a job is actually in flight — Cloud Run still
> scales to zero (and bills nothing) when idle.

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

## Async job submission (submit + poll)

`POST /cases/render/jobs` / `GET /cases/render/jobs/{job_id}` exist alongside
the synchronous `POST /cases/render` for the same reason `--timeout 600`
does: edge proxies in front of Cloud Run — Firebase Hosting's rewrite proxy in
particular — cap a single request at **~60s**, well under a real two-step
generation's several minutes. The async pair splits that into a fast submit
(`202` + a job id) and a cheap poll (`GET /cases/render/jobs/{job_id}` →
`queued` / `running` / `done` / `failed`), so no single HTTP request needs to
stay open for the full render. See `src/claimscene/jobs.py` for the job-store
design (status objects live in the same B2/fake storage backend the rest of
the app already uses, under `jobs/<job_id>/status.json` — not a separate
database).

**Honest limitation:** the background worker runs **in-process, on the Cloud
Run instance that accepted the submit** — there is no external queue and no
separate worker fleet. A client that keeps polling keeps that instance warm
(a request in flight resets Cloud Run's idle-scale-down clock), so in
practice the job completes. But if that specific instance is scaled down
mid-job, the in-flight generation is lost with no automatic retry — the
stored status simply stops advancing. This is a deliberate, acceptable
tradeoff for a demo, not a production job queue; a production version would
hand the work to a durable queue (e.g. Cloud Tasks) consumed by a Cloud Run
**Job** (not a request-serving instance), so the work outlives any one
instance. `--no-cpu-throttling` (above) is required even for this demo
version — without it, the worker thread barely progresses between polls.

## Deploy — LIVE cutover (secrets via Secret Manager)

The live path extracts scenes with the VLM ladder, renders real illustrations
with Genblaze/GMI Cloud, and stores every artifact on Backblaze B2. Secret
values are **never** passed on the command line or written to the Cloud Run
service spec — they live in **Google Secret Manager** and are staged once (and
on every rotation) with `deploy/stage-secrets.sh`; the deploy then references
them by name. That way `gcloud run services describe` — and anyone with only
`run.services.get` IAM on the project — never sees a raw key.

**Step 1 — stage the secrets** (the only step that handles raw values;
`NEBIUS_INFERENCE_API_KEY` is optional — the VLM fallback rung):

```bash
B2_APPLICATION_KEY_ID='<b2 key id>' \
B2_APPLICATION_KEY='<b2 app key>' \
GMI_API_KEY='<gmi cloud key>' \
  bash deploy/stage-secrets.sh
```

It creates/updates the secrets (`claimscene-b2-application-key-id`,
`claimscene-b2-application-key`, `claimscene-gmi-api-key`, and
`claimscene-nebius-inference-api-key` if you pass one). Prefer not to put keys in
a shell? Create the same secrets in the Cloud Console (Secret Manager → Create
secret) instead.

**Step 2 — deploy** (no secret values here — only the non-secret live config):

```bash
CLAIMSCENE_MODE=live \
B2_BUCKET_NAME=claimscene \
B2_S3_ENDPOINT='https://s3.<region>.backblazeb2.com' \
  bash deploy/deploy-cloudrun.sh
```

The deploy script grants the Cloud Run runtime service account
`roles/secretmanager.secretAccessor` on each secret, wires them with
`--set-secrets`, and fails fast with a pointer back to step 1 if one is missing.
`ffmpeg` is already in the image, so the animated schematic MP4 encodes in both
modes.

> The script **rebuilds the image from local source**. Run `git pull` on `main`
> before the cutover so the rebuilt image carries the latest code.

**To rotate keys:** issue new keys in the GMI Cloud / Backblaze dashboards,
re-run step 1 with the new values (adds a new secret version), re-run step 2,
verify `/health`, then revoke the old keys. Cloud Run reads `:latest`, so the
redeploy picks up the new version with no code change.

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
