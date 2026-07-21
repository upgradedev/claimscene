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

## In flight
- `scripts/generate_eval_scenarios.py` background run generating 7 scenarios
  x 3 seedream views (~85 s/image, ~$0.74). s01 done (3 jpgs <300KB, scene
  matches truth). Resume-safe: rerun the script, done scenarios skip.

## Next (in order)
1. Wait for generation → commit eval/scenarios (jpgs + manifest.json).
2. `scripts/eval_extraction.py --live` (gemma-4 + gemini-3.5-flash) →
   commit dated scoreboard to eval/results; iterate prompt (≤3) if <60%.
3. Live evidence: CLAIMSCENE_MODE=live CLI run on s01 photos with B2 env
   UNSET (sink entitlement!), copy illustration.png/mp4 + scene.json +
   manifest.json → eval/evidence/live_illustration/ (readiness verifies).
4. Optional $0.11 A/B: seedream sequential_image_generation=auto — record
   verdict JSON only, do not commit extra images.
5. README: measured % headline + models table + eval methodology + budget.
6. Validate: ruff + pytest + readiness --min 95 in fresh venv → PR → CI
   green → squash-merge.

## Spend ledger (USD, cumulative)
- probes (pre-task, separate session): 0.13
- failed s01 master (sink incident): 0.035 (+1 spare probe1 retry 0.005)
- eval set so far: s01 3 images = 0.105; rest pending (~0.63)
- VLM live smoke test (pytest): ~0.005
