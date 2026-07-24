"""Integration load / perf smoke for ClaimScene.

Verifies concurrent case processing under load through a thread pool: the
``CasePipeline`` is thread-safe (no shared mutable state leaks across parallel
runs — each run seals its own verifiable, correctly-shaped case) and each
compilation stays within a generous latency bound. Fully offline (fakes only).

The bound is a smoke check, not an SLO: a single offline case compiles in
~0.3-0.5s locally (Pillow schematic + deterministic fakes), so ~3s of headroom
per run absorbs a slower CI runner and thread contention without flaking.
"""
from __future__ import annotations

import concurrent.futures
import time

import pytest

from claimscene.adapters import FakeMediaProvider, FakeVisionExtractor, InMemoryStorage
from claimscene.case import CasePhoto, CaseSpec, PhotoSource
from claimscene.pipeline import CasePipeline
from claimscene.provenance import verify_manifest
from claimscene.schematic import PillowSchematicRenderer

_CONCURRENCY = 20
_MAX_WORKERS = 5
#: Generous per-run latency ceiling (see module docstring): ~7x the observed
#: local average, so a slower CI runner under contention still clears it.
_LATENCY_BOUND_S = 3.0
#: input + scene + timeline + schematic_svg + schematic_hero + illustration_still
#: + illustration + report + manifest + receipt (animate=False → no
#: schematic_animation).
_EXPECTED_ARTIFACTS = 10


def run_single_pipeline(worker_id: int) -> tuple[float, str, int]:
    """Run one full offline case in its own isolated storage; time it."""
    storage = InMemoryStorage(bucket=f"claimscene-load-{worker_id}")
    pipeline = CasePipeline(FakeVisionExtractor(), FakeMediaProvider(), storage,
                            renderer=PillowSchematicRenderer(animate=False))
    photos = [CasePhoto(filename=f"scene-{worker_id}.png",
                        data=b"\x89PNG-load-" + str(worker_id).encode(),
                        source=PhotoSource.staged_demo)]

    start = time.time()
    result = pipeline.run(CaseSpec(case_id=f"load-{worker_id}", photos=photos))
    duration = time.time() - start

    # Thread-safety evidence: each run sealed ITS OWN verifiable case, with the
    # right shape and its own id — no cross-run bleed of scene/manifest state.
    assert verify_manifest(result.manifest), f"worker {worker_id} seal broke"
    assert result.case_id == f"load-{worker_id}"
    return duration, result.case_id, len(result.artifacts)


def test_concurrent_pipeline_generation_load():
    durations: list[float] = []
    case_ids: list[str] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
        futures = {executor.submit(run_single_pipeline, i): i
                   for i in range(_CONCURRENCY)}
        for future in concurrent.futures.as_completed(futures):
            worker_id = futures[future]
            try:
                duration, case_id, n_artifacts = future.result()
            except Exception as exc:  # noqa: BLE001 - surface which worker failed
                pytest.fail(f"worker {worker_id} failed under load: {exc!r}")
            durations.append(duration)
            case_ids.append(case_id)
            assert n_artifacts == _EXPECTED_ARTIFACTS

    # Every worker completed, each with a DISTINCT sealed case id (no collision
    # or overwrite from concurrent runs).
    assert len(durations) == _CONCURRENCY
    assert len(set(case_ids)) == _CONCURRENCY

    avg_duration = sum(durations) / len(durations)
    print(f"\nAverage case-compilation duration under load: {avg_duration:.3f}s")
    assert avg_duration < _LATENCY_BOUND_S
