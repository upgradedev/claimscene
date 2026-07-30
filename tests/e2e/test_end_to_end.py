"""Offline E2E: photos in → every artifact + sealed manifest + verify chain."""
from __future__ import annotations

import io
import json

import pytest

from claimscene.adapters.fakes import (
    FakeMediaProvider,
    FakeVisionExtractor,
    InMemoryStorage,
)
from claimscene.case import CasePhoto, CaseSpec, PhotoSource
from claimscene.pipeline import CasePipeline
from claimscene.provenance import verify_artifact, verify_manifest
from claimscene.schematic import PillowSchematicRenderer


def _real_photo(name: str, color: tuple[int, int, int]) -> CasePhoto:
    from PIL import Image

    img = Image.new("RGB", (64, 48), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return CasePhoto(filename=name, data=buf.getvalue(),
                     source=PhotoSource.staged_demo)


@pytest.fixture(scope="module")
def run():
    storage = InMemoryStorage(bucket="e2e")
    pipeline = CasePipeline(FakeVisionExtractor(), FakeMediaProvider(), storage,
                            renderer=PillowSchematicRenderer(animate=False))
    photos = [_real_photo("front.png", (200, 30, 30)),
              _real_photo("damage_rear.png", (30, 30, 200)),
              _real_photo("road_wide.png", (30, 200, 30))]
    result = pipeline.run(CaseSpec(case_id="e2e-case", photos=photos,
                                   context="two vehicles at a junction"))
    return storage, result


def test_full_chain_verifies(run):
    storage, result = run
    manifest = json.loads(storage.get(result.artifacts["manifest"].key))
    assert verify_manifest(manifest)
    for name in ("scene_graph", "timeline", "report", "illustration",
                 "illustration_still", "schematic_svg", "schematic_hero"):
        data = storage.get(result.artifacts[name].key)
        assert verify_artifact(manifest, name, data), name


def test_tamper_detection_end_to_end(run):
    storage, result = run
    manifest = json.loads(storage.get(result.artifacts["manifest"].key))
    tampered = json.loads(json.dumps(manifest))
    tampered["inputs"][0]["source"] = "user_upload"
    assert verify_manifest(manifest) is True
    assert verify_manifest(tampered) is False
    # Substituted artifact bytes are detected too.
    assert verify_artifact(manifest, "report", b"forged report") is False


def test_index_lists_every_artifact(run):
    storage, result = run
    index_keys = {row["key"] for row in storage.index}
    for ref in result.artifacts.values():
        assert ref.key in index_keys
    # 3 inputs + 9 artifacts (scene, timeline, schematic svg+hero, illustration
    # still+clip, report, manifest, detached receipt; no animation offline).
    assert len(index_keys) == 12


def test_illustration_layer_sealed_with_still_and_clip(run):
    """The illustration section must honestly record BOTH layers: the clip's
    generative provenance (model/prompt/hash/provider/degraded) and the
    seed's schematic provenance (no model, no prompt -- see pipeline.py step
    5) -- and both artifacts must be in storage."""
    storage, result = run
    manifest = json.loads(storage.get(result.artifacts["manifest"].key))
    ill = manifest["illustration"]
    for field in ("provider", "model", "prompt", "sha256",
                  "still_model", "still_source", "still_prompt",
                  "still_sha256", "degraded"):
        assert field in ill, field
    assert ill["degraded"] is True  # offline fakes must never claim otherwise
    assert ill["model"] == "pixverse-v6-i2v"
    assert ill["still_model"] is None  # no text-to-image model produced it
    assert ill["still_source"] == "schematic:impact_frame"
    assert verify_artifact(manifest, "illustration_still",
                           storage.get(result.artifacts["illustration_still"].key))


def test_input_attribution_fields_survive_to_manifest(run):
    storage, result = run
    manifest = json.loads(storage.get(result.artifacts["manifest"].key))
    rows = manifest["inputs"]
    assert len(rows) == 3
    for row in rows:
        assert row["source"] == "staged_demo"
        assert set(row) == {"filename", "sha256", "media_type", "role",
                            "source", "attribution", "license"}


def test_scene_and_timeline_json_load_back_validated(run):
    from claimscene.layout import timeline_from_json
    from claimscene.scene import scene_from_json

    storage, result = run
    scene = scene_from_json(storage.get(result.artifacts["scene_graph"].key))
    timeline = timeline_from_json(storage.get(result.artifacts["timeline"].key))
    assert scene.schema_id == "claimscene/scene/v1"
    assert timeline.schema_id == "claimscene/timeline/v1"
    assert {t.vehicle_id for t in timeline.tracks} == {v.id for v in scene.vehicles}
