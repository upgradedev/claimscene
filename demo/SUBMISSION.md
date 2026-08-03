# ClaimScene — Devpost submission package

> Field-by-field copy for the Backblaze Generative Media Hackathon form. Draft filled, not submitted. Live app: https://claimscene-147595510158.europe-west1.run.app · Repo: https://github.com/upgradedev/claimscene

## Project name
ClaimScene

## Elevator pitch (tagline, ≤200 chars)
Turn accident photos into an honest, self-disclosing illustration: a factual top-down schematic plus a labeled AI clip, every input and output sealed with verifiable provenance on Backblaze B2.

## Built with (tags)
python, fastapi, react, typescript, genblaze, backblaze-b2, gmi-cloud, ffmpeg, google-cloud-run, provenance, computer-vision, insurtech

## "Try it out" links
- Live app: https://claimscene-147595510158.europe-west1.run.app
- GitHub repo: https://github.com/upgradedev/claimscene

## Video demo link
The narrated source video is committed at **`demo/claimscene-demo.mp4`** (2:42,
1920x1080 H.264, ElevenLabs voiceover, no music, burned captions + `demo/claimscene-demo.en.srt`).
It is built from `demo/claimscene-demo.beats.json` by `scripts/build_video.py` and
re-verified in CI by `scripts/check_video.py` (A/V-sync + caption-order gate).
TODO(owner): upload `demo/claimscene-demo.mp4` to YouTube (public, < 3 min, no
copyrighted music), then paste the URL here and on the Devpost form.

## About the project (Markdown story)

### Inspiration
After a crash, the first job is documentation: which cars, which damage, which road, what happened. People already photograph the scene on their phones. The gap is turning those photos into something a claims handler or an insurer can read and trust.

Generative video makes that easy to fake and hard to trust. A convincing AI "reconstruction" that is actually a guess is worse than nothing in a claim or a courtroom. Insurers are already warning about AI-generated images in fraudulent claims, and courts are debating new rules for machine-generated evidence. So we built the opposite of a black box: an app where the factual layer is deterministic, the generative layer is clearly labeled as illustration, and every input and output is sealed with provenance you can verify yourself.

### What it does
ClaimScene turns accident photos into a documented case:

1. **Extract.** A vision-language model reads the photos into a strict, constrained scene description: vehicles and their damage (by clock position), the road layout, each vehicle's approach and maneuver, and the impact points. No free-form coordinates, only a fixed vocabulary the model must answer in.
2. **Review and adjust.** You see the extracted scene and correct it with simple controls (dropdowns, a 12-position clock picker), and a top-down schematic redraws live as you edit. The AI proposes; you confirm. Nothing is generated from an unreviewed guess.
3. **Render.** A deterministic layout engine turns the confirmed scene into an animated top-down schematic, the factual layer, watermarked "ILLUSTRATION — NOT EVIDENCE" on every frame. Genblaze then animates that same schematic into a short illustration clip: the seed image is the schematic's own impact-frame render, so the clip's geometry is inherited, not invented, and the camera push toward the point of impact is computed by us from the same Timeline, never left to the model. What the model actually contributes is visual style; repeated live renders proved it does not reliably preserve the seeded layout, so the manifest states that division of authorship once, in plain language, and the disclosure is burned into the clip's own pixels after generation, not just requested in the prompt.
4. **Seal.** Every input photo, the schematic, the illustration, and an incident report are stored content-addressed on Backblaze B2 and sealed into a SHA-256 manifest that records, per input, where it came from (uploaded, staged, public-domain, or synthetic) and its license. You can re-verify the seal in the browser.

The public demo runs on synthetic and staged photos only. No real accident imagery or personal data.

### How we built it
Ports and adapters, offline-first, so the whole pipeline including the real SHA-256 provenance runs in CI with zero credentials. The orchestrator depends on four ports: a VisionExtractor, a MediaProvider, a StorageBackend, and a SchematicRenderer. Live adapters wrap a GMI Cloud VLM (through an OpenAI-compatible client) and a real Genblaze Pipeline writing to Backblaze B2; deterministic fakes implement the same ports for tests and the offline demo. A FastAPI service exposes it, a React and TypeScript frontend ships in the same container on Cloud Run, and every backend is used only when its credentials are present, otherwise the app degrades transparently and never 500s. The health endpoint always reports which backends actually served you.

The anti-hallucination core is the constrained vocabulary. We never ask the model for pixel coordinates. We ask for enums (approach direction, maneuver, damage clock position, road template), validate the answer against a strict schema with one repair round-trip, and let a deterministic layout engine place everything. That is what makes the human review step small and the schematic trustworthy.

### How it uses Backblaze B2
- Every artifact lands on B2 under content-addressed keys: input photos, the schematic animation, the illustration still and clip, the report, and the manifest.
- The sealed manifest records **per-input source and license attribution** (uploaded / staged_demo / public_domain / licensed / synthetic_generated), so the case says where every pixel came from. Provenance of the inputs, not just the outputs.
- A durable JSONL index catalogues every object with size and content type, a queryable case ledger across the whole store.
- The live playback route mints fresh presigned GET URLs per request (region and SigV4 correct), so nothing signed is ever stored and the manifest hashes stay canonical.

### How it uses Genblaze
- Illustration generation is a real Genblaze Pipeline: an image-to-video clip (`pixverse-v6-i2v` by default, `Kling-Image2Video-V2.1-Master` as a premium option) seeded by a raster of the case's own deterministic schematic, not a second generated still. A live render proved a diffusion model does not reliably preserve the layout it is seeded with, so the geometry, the camera path and the disclosure caption are all computed by us instead; the model's real contribution is the clip's rendered visual style.
- Genblaze's ObjectStorageSink content-addresses each generated asset, persists it to B2, and seals a per-asset SHA-256 manifest, which ClaimScene folds into the case-level manifest.
- Every Genblaze call is contract-tested in CI against the published SDK, including an assertion that input assets actually reach the pipeline step, so API drift fails the build instead of the demo.
- The sealed manifest states this division of authorship once, in plain language (`illustration.authorship_note`): what we compute versus what the model actually contributes, with no placement guarantee. A judge can read that claim straight from the manifest or the app's Provenance panel, and it is structurally re-verified the same way the disclosure and watermark are.

### AI providers and models used
| Role | Model | Provider |
| --- | --- | --- |
| Scene extraction (primary) | google/gemma-4-31b-it | GMI Cloud |
| Scene extraction (fallback) | google/gemini-3.5-flash | GMI Cloud |
| Scene extraction (independent fallback) | Qwen/Qwen2.5-VL-72B-Instruct | Nebius |
| Illustration seed (top-down schematic raster) | none, deterministic `PillowSchematicRenderer` | in-process (no model call) |
| Illustration clip (image-to-video from the schematic seed) | pixverse-v6-i2v (default) / Kling-Image2Video-V2.1-Master (premium) | GMI Cloud via Genblaze |

### Measured extraction accuracy
The primary VLM (`google/gemma-4-31b-it`) scores **100% weighted field accuracy** on a committed 7-scenario evaluation set: vehicle count, kind and color, road layout and signal, approach and maneuver, and damage and impact clock positions within one hour. The eval set is staged toy-diorama photos (three consistent seedream-rendered views per scenario) with hand-authored ground truth, sealed as synthetic_generated. It is self-consistent by construction, and we say so in the README, but it makes the accuracy claim reproducible from the repo.

### Challenges we ran into
The honest lesson came from the live path. Our contract tests run against the real Genblaze SDK, but its mock provider does no server-side validation, so a step that never attached its input image can pass every test and only fail live. We had hit exactly that class of bug on a sibling project, so here we wrote the contract test to assert the input assets reach the pipeline step, and it held. We also learned that region-less presigned URLs 401 on B2, that a brand-new GCP project needs a minute for its build service agent to provision, and that a multi-minute live render must use the direct Cloud Run origin rather than a 60-second edge proxy.

### Accomplishments that we're proud of
- Provenance that covers inputs, not just outputs: the manifest says where every photo came from and under what license.
- A measured accuracy number (100% on the committed set) instead of a vibe.
- A human-in-the-loop review step that is the trust story and the production-readiness story at once.
- A sealed AI→human approval receipt: the render seals the exact proposed→confirmed scene diff with a recomputable `decision_digest` that self-voids if the confirmed scene drifts, and the whole case re-verifies from stored bytes through a named-check receipt (`GET /cases/{id}/verify`) plus a detached, self-sealed receipt written as its own B2 object.
- 521 backend tests plus 295 frontend tests and 20 real-browser tests, gitleaks, CodeQL and a machine-checkable readiness gate, all runnable with zero credentials.

### What we learned
Constrain the model to a vocabulary and let deterministic code do the geometry. Label the generative layer loudly. And make honesty a data structure: a sealed manifest that records sources and disclosure by construction, not a promise in the footer.

### What's next for ClaimScene
First-notice-of-loss integrations, multi-incident case files, a reviewer audit trail, and more Genblaze providers behind the same port. The wedge is any workflow where a synthetic image must be self-disclosing: insurance, fleet safety, and legal intake.

## Additional info (judges/organizers)
- App URL: https://claimscene-147595510158.europe-west1.run.app
- GitHub repo URL: https://github.com/upgradedev/claimscene
- Providers and models: see the table above (GMI Cloud gemma-4-31b-it / gemini-3.5-flash, Nebius Qwen2.5-VL-72B, pixverse-v6-i2v, Kling-Image2Video-V2.1-Master).
- B2 and Genblaze usage: see the two sections above.

## Owner checklist (not done here)
1. Upload demo video to YouTube (public, < 3 min), paste URL into Project details + this file.
2. Upload gallery images.
3. Accept T&C + Submit project on Devpost before Aug 3, 5:00pm EDT (edits allowed after submit until the deadline).
4. Keep the app reachable through Aug 11, 5:00pm EDT (judging).

Already done (verified 2026-07-23): the `claimscene` B2 bucket and a
write-entitled, scoped application key are provisioned, and the live app is
deployed on Cloud Run rendering real Genblaze output straight to B2
(`storage=B2Storage`) — a live case wrote a real seedream still and pixverse
clip to the bucket (that still-generation step was later replaced by the
schematic-seeded illustration described above; this line is a historical
record of that date's mechanism, not the current one). (If the entitled key
is ever absent, live mode degrades storage to the in-memory object store
rather than failing — a documented fallback, not the current state.)

## Relationship to our other entry
ClaimScene shares an in-house Backblaze B2 storage and provenance-sealing foundation with our other submission, Cinemory (cinematic memory reels). The two are different products: different domain, different data model, different extraction and layout pipeline, and a different UI. This is disclosed here and in the README.
