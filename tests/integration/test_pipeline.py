"""CasePipeline against the fakes: full artifact chain + sealed manifest."""
from __future__ import annotations

import json

import pytest

from claimscene.adapters.fakes import (
    FakeMediaProvider,
    FakeVisionExtractor,
    InMemoryStorage,
)
from claimscene.case import CaseSpec
from claimscene.pipeline import CasePipeline
from claimscene.provenance import WATERMARK, verify_artifact, verify_manifest
from claimscene.schematic import PillowSchematicRenderer


@pytest.fixture
def pipeline():
    return CasePipeline(FakeVisionExtractor(), FakeMediaProvider(), InMemoryStorage(),
                        renderer=PillowSchematicRenderer(animate=False))


def _run(pipeline, photos, case_id="case-int"):
    return pipeline.run(CaseSpec(case_id=case_id, photos=photos))


def test_all_artifact_kinds_stored(pipeline, photos):
    result = _run(pipeline, photos)
    names = set(result.artifacts)
    assert {"scene_graph", "timeline", "schematic_svg", "schematic_hero",
            "illustration", "report", "manifest"} <= names
    kinds = {row["key"].split("/")[1] for row in pipeline.storage.index}
    assert {"inputs", "scene", "timeline", "schematic", "illustration",
            "report", "manifest"} <= kinds


def test_manifest_verifies_and_hashes_match_storage(pipeline, photos):
    result = _run(pipeline, photos)
    assert verify_manifest(result.manifest)
    for name in ("scene_graph", "timeline", "report", "illustration",
                 "schematic_svg", "schematic_hero"):
        ref = result.artifacts[name]
        stored = pipeline.storage.get(ref.key)
        assert verify_artifact(result.manifest, name, stored), name


def test_every_key_is_content_addressed(pipeline, photos):
    _run(pipeline, photos)
    for row in pipeline.storage.index:
        parts = row["key"].split("/")
        assert len(parts) == 5, row["key"]
        assert any(len(p) == 64 for p in parts), row["key"]


def test_illustration_sealed_as_degraded_fake(pipeline, photos):
    result = _run(pipeline, photos)
    ill = result.manifest["illustration"]
    assert ill["provider"] == "fake-media"
    assert ill["degraded"] is True
    assert ill["model"] == "pixverse-v6-i2v"
    assert "non-photorealistic" in ill["prompt"]


def test_illustration_still_sealed_and_chained_into_clip(pipeline, photos):
    result = _run(pipeline, photos)
    ill = result.manifest["illustration"]
    assert ill["still_model"] == "seedream-5.0-lite"
    assert "Miniature diecast toy car diorama" in ill["still_prompt"]
    still_ref = result.artifacts["illustration_still"]
    assert ill["still_sha256"] == still_ref.sha256
    assert still_ref.content_type == "image/png"
    # Both illustration artifacts live under the same storage kind.
    assert still_ref.key.split("/")[1] == "illustration"


def test_manifest_inputs_carry_roles_and_sources(pipeline, photos):
    result = _run(pipeline, photos)
    rows = result.manifest["inputs"]
    assert [r["role"] for r in rows] == ["scene_photo", "damage_photo", "road_photo"]
    assert rows[2]["source"] == "public_domain"
    assert rows[2]["attribution"] == "Municipal survey archive"
    assert all(len(r["sha256"]) == 64 for r in rows)


def test_schematic_watermark_sealed_in_manifest(pipeline, photos):
    result = _run(pipeline, photos)
    assert result.manifest["schematic"]["watermark"] == WATERMARK
    svg = result.payloads["schematic_svg"].decode("utf-8")
    assert WATERMARK in svg


def test_hostile_case_id_and_filenames_produce_safe_keys(photos):
    pipeline = CasePipeline(FakeVisionExtractor(), FakeMediaProvider(),
                            InMemoryStorage(),
                            renderer=PillowSchematicRenderer(animate=False))
    hostile = [p.model_copy(update={"filename": "../../../etc/passwd"})
               for p in photos]
    result = _run(pipeline, hostile, case_id="../../evil\x00case")
    assert result.manifest_hash
    for row in pipeline.storage.index:
        key = row["key"]
        segments = key.split("/")
        assert ".." not in segments
        assert not any(s.startswith(".") for s in segments)
        assert "\x00" not in key and "\\" not in key and not key.startswith("/")


def test_report_mentions_offline_fallback_provider(pipeline, photos):
    result = _run(pipeline, photos)
    report = result.payloads["report"].decode("utf-8")
    assert "fake-media" in report
    assert "offline deterministic fallback" in report


def test_no_credential_material_in_manifest(pipeline, photos):
    result = _run(pipeline, photos)
    blob = json.dumps(result.manifest).lower()
    for banned in ("b2_application_key", "b2_app_key", "gmi_api_key", "secret",
                   "password", "aws_secret"):
        assert banned not in blob
