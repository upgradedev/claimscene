# Phase 2 progress breadcrumbs (working notes — delete before merge if desired)

Branch: `feat/phase2-vlm-eval-genblaze`. Plan of record: VLM extractor + eval
harness (measured %) + Genblaze provider + pipeline integration + readiness
hardening + README. Budget cap ~$6.

## Done (code, all offline-tested)
- `src/claimscene/adapters/vlm_extractor.py` — VLM ladder (GMI gemma-4-31b-it
  → GMI gemini-3.5-flash → Nebius Qwen2.5-VL), strict-JSON prompt from the
  enums, null-safe normalise, 1 repair round-trip, retry/backoff, usage log.
- `src/claimscene/evaluation.py` — field-level scorer (clock ±1 circular,
  greedy vehicle alignment, weighted aggregate).
- `src/claimscene/adapters/genblaze_provider.py` — Genblaze Pipeline adapter,
  image+video modalities, external_inputs hosting, still→clip chaining via
  GMI-hosted URLs, `backend=None` sentinel = NO sink (see incident below).
- Pipeline: establish-shot still (seedream-5.0-lite) + i2v clip
  (pixverse-v6-i2v) chained from the still; manifest `illustration` section
  now seals still_model/still_prompt/still_sha256 too; CLI writes
  illustration.png; config wires live adapters with degrade-to-fake.
- Tests: unit test_vlm_extractor (canned transports), test_evaluation,
  integration test_genblaze_contract (real SDK MockProvider + input-
  attachment assertions + sentinel regression), pipeline/e2e extended.
  Unit+integration+e2e all green locally; ruff clean.
- Readiness: new checks utility.vlm_adapter_constrained,
  utility.eval_scoreboard_floor (floor 60%), genblaze.sdk_adapter (real
  Pipeline drive), genblaze.live_illustration (committed-evidence re-hash);
  user-gated now ONLY production.live_deploy + b2.live_objects_written.
  CI readiness job bumped to `--min 95` (gating).

## Incident log
- Genblaze sink transfer fails with env B2 creds (PutObject entitlement) —
  fixed by explicit `backend=None` sentinel; b2.live_objects_written
  user-gated note updated. Cost of incident: ~$0.04.

## Milestones landed (2026-07-22)
- Eval set COMMITTED: 21 seedream images (7 scenarios x 3 views, all
  <300KB, visually verified against truths via thumbnails) + manifest.
- Canonical scoreboard COMMITTED (eval/results/2026-07-21_extraction_eval):
  **gemma-4-31b-it 100.0%** (7/7 scenarios, all fields); gemini-3.5-flash
  71.4% (every ANSWERED scenario 100%; two scenarios zeroed by GMI 429
  capacity — recorded in JSON; capacity figure, not accuracy).
  One live repair round-trip observed (s06 run 1) — the loop works.
- Live evidence COMMITTED (eval/evidence/live_illustration): full live
  pipeline run (mode=live, B2 unset): gemma-4 extraction + seedream still +
  pixverse clip, provider=genblaze, degraded=False, VERIFY PASS; readiness
  re-hashes the committed bytes.
- Readiness: automatable 100.0%, GATE PASS at --min 95 (run locally).
- A/B (bonus lever): seedream sequential_image_generation=auto returned 3
  coherent views in ONE request (133s, same $/image) — viable faster path
  for future set builds; verdict in scratchpad ab_sequential_result.json,
  images not committed.
- README updated: measured headline, ladder table, methodology + limits,
  flash capacity note, roadmap Phase 3.

## Remaining
1. Final validation (ruff + full pytest + readiness) → commit → PR → CI
   green → squash-merge.

## Spend ledger (USD, cumulative — this task)
- sink-incident lost master: 0.035
- DNS-crash lost s02 master: 0.035
- eval set (21 images, recorded): 0.735
- A/B sequential probe (3 images): 0.105
- live evidence case (still+clip): 0.060
- VLM tokens (live smoke + 2 full eval runs + evidence): ~0.05 est
  (gemma 7 calls = 11.7K prompt + 1.4K completion tokens/run; flash 20.7K +
  9.7K reasoning-heavy)
- TOTAL: ~$1.07 of the $6 cap
