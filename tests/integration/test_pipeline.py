"""CasePipeline against the fakes: full artifact chain + sealed manifest."""
from __future__ import annotations

import hashlib
import json

import pytest

from claimscene.adapters.fakes import (
    FakeMediaProvider,
    FakeVisionExtractor,
    InMemoryStorage,
)
from claimscene.case import CaseSpec
from claimscene.layout import LayoutEngine
from claimscene.pipeline import CasePipeline
from claimscene.provenance import WATERMARK, verify_artifact, verify_manifest
from claimscene.report import ILLUSTRATION_NEGATIVE_PROMPT
from claimscene.schematic import PillowSchematicRenderer
from claimscene.watermark import burn_still_watermark


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
    assert "not a real recording" in ill["prompt"]


def test_illustration_still_sealed_and_chained_into_clip(pipeline, photos):
    """The manifest's 'still' fields now describe the deterministic schematic
    raster that seeds the clip, not a text-to-image generation -- see
    pipeline.py step 5 and report.ILLUSTRATION_SEED_NOTE."""
    result = _run(pipeline, photos)
    ill = result.manifest["illustration"]
    assert ill["still_model"] is None  # no text-to-image model produced it
    assert ill["still_source"] == "schematic:impact_frame"
    still_lower = ill["still_prompt"].lower()
    assert "no still-image model or prompt was used" in still_lower
    assert "computer-generated" in still_lower
    assert "not a real recording" in still_lower
    still_ref = result.artifacts["illustration_still"]
    assert ill["still_sha256"] == still_ref.sha256
    assert still_ref.content_type == "image/png"
    # Both illustration artifacts live under the same storage kind.
    assert still_ref.key.split("/")[1] == "illustration"


class _RecordingMediaProvider:
    """Wraps ``FakeMediaProvider``, recording every ``generate()`` call's
    kwargs -- so a test can assert on exactly what reached the provider
    (which modality, which bytes), not just that the pipeline completed."""

    name = "fake-media"

    def __init__(self) -> None:
        self._inner = FakeMediaProvider()
        self.calls: list[dict] = []

    def generate(self, *, model, prompt, modality="video", inputs=None, params=None):
        self.calls.append({"model": model, "prompt": prompt, "modality": modality,
                           "inputs": list(inputs) if inputs else [],
                           "params": dict(params or {})})
        return self._inner.generate(model=model, prompt=prompt, modality=modality,
                                    inputs=inputs, params=params)


def test_clip_is_seeded_by_the_schematic_raster_not_a_generated_still(photos):
    """The illustration clip step must receive the case's OWN deterministic
    schematic raster as its input image -- never a second generative call.
    Asserts on the actual bytes the provider received (ground-truth
    recomputed independently from the same scene/timeline/renderer), not
    just that some input was present: a live render on 2026-07-30 proved
    prompt text alone cannot pin geometry, so the fix pins it by feeding the
    model the factual raster instead (see pipeline.py step 5)."""
    provider = _RecordingMediaProvider()
    pipeline = CasePipeline(FakeVisionExtractor(), provider, InMemoryStorage(),
                            renderer=PillowSchematicRenderer(animate=False))
    result = _run(pipeline, photos, case_id="seed-check")

    image_calls = [c for c in provider.calls if c["modality"] == "image"]
    video_calls = [c for c in provider.calls if c["modality"] == "video"]
    assert image_calls == []  # no text-to-image generative call at all any more
    assert len(video_calls) == 1
    assert len(video_calls[0]["inputs"]) == 1
    seed_bytes = video_calls[0]["inputs"][0]

    # Ground truth: independently rebuild the exact seed the pipeline itself
    # derived from the SAME scene/timeline/renderer, and require byte
    # equality -- not just "some PNG". (``title`` is irrelevant to seed_png:
    # it is rendered with annotate=False, which never draws it.)
    scene = FakeVisionExtractor().extract(photos)
    timeline = LayoutEngine().build(scene)
    expected_seed = PillowSchematicRenderer(animate=False).render(timeline).seed_png
    assert seed_bytes == expected_seed

    # The fed seed is the UN-watermarked raster: it must differ from the
    # SEALED (watermarked) illustration_still bytes, and burning the
    # disclosure onto it must reproduce those sealed bytes exactly -- the
    # single-on-clip-caption invariant this change depends on.
    stored_still = result.payloads["illustration_still"]
    assert seed_bytes != stored_still
    assert burn_still_watermark(seed_bytes).data == stored_still

    # And it must not be the schematic's OWN sealed hero frame either: that
    # frame already carries "ILLUSTRATION -- NOT EVIDENCE" burned in twice
    # (see schematic.py) -- feeding it forward would double (or, once a
    # generative model reinterprets it, triple and garble) the on-clip text.
    assert seed_bytes != result.payloads["schematic_hero"]


def test_illustration_prompts_avoid_photorealism_cues(pipeline, photos):
    """The forensic-reconstruction register must never nudge the generative
    model toward photorealism: none of the risky cue words may appear in
    either prompt, and the not-a-real-recording marker must be present in
    both (the honesty guarantee stays test-enforced even though the wording
    changed)."""
    result = _run(pipeline, photos)
    ill = result.manifest["illustration"]
    forbidden = ("photorealistic", "photograph", "dashcam", "real footage",
                "cinematic film still", "documentary footage")
    for prompt in (ill["prompt"], ill["still_prompt"]):
        lowered = prompt.lower()
        for word in forbidden:
            assert word not in lowered, f"{word!r} found in prompt"
        assert "not a real recording" in prompt


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


def test_clip_step_sends_the_camera_suppression_negative_prompt(photos):
    """The clip call must carry ``negative_prompt``.

    A live render on 2026-07-30 obeyed the seeded geometry but pulled the
    camera out until the vehicles were specks on an empty road for about half
    the clip. ``pixverse-v6-i2v`` exposes no motion or camera parameter (its
    SDK ``param_allowlist`` is image/seed/aspect_ratio/duration/video/
    negative_prompt/video_url/resolution/quality/image_url/prompt/cfg_scale),
    so naming the unwanted behaviour is the lever we actually have. This
    pins that it is sent, and that it names the specific failure."""
    provider = _RecordingMediaProvider()
    pipeline = CasePipeline(FakeVisionExtractor(), provider, InMemoryStorage(),
                            renderer=PillowSchematicRenderer(animate=False))
    _run(pipeline, photos, case_id="negative-prompt-check")

    video_calls = [c for c in provider.calls if c["modality"] == "video"]
    assert len(video_calls) == 1
    negative = video_calls[0]["params"]["negative_prompt"]
    assert negative == ILLUSTRATION_NEGATIVE_PROMPT
    # The measured failure mode, and the no-invented-text rule the burned-in
    # disclosure depends on.
    for unwanted in ("zoom out", "wide shot", "empty road", "text", "watermark"):
        assert unwanted in negative


def test_clip_seed_is_deterministic_and_derived_from_the_factual_raster(photos):
    """The provider ``seed`` must be a pure function of the seed raster.

    The raster is itself a pure function of the factual Timeline, so the same
    sealed case asks the provider for the same clip every time: reproducible
    from the record rather than from a stored random number."""
    def run_once(case_id: str) -> tuple[int, bytes]:
        provider = _RecordingMediaProvider()
        pipeline = CasePipeline(FakeVisionExtractor(), provider, InMemoryStorage(),
                                renderer=PillowSchematicRenderer(animate=False))
        _run(pipeline, photos, case_id=case_id)
        call = next(c for c in provider.calls if c["modality"] == "video")
        return call["params"]["seed"], call["inputs"][0]

    seed_a, raster_a = run_once("seed-determinism-a")
    seed_b, raster_b = run_once("seed-determinism-b")

    # Same scene, so the same factual raster and therefore the same seed --
    # even though the case ids differ.
    assert raster_a == raster_b
    assert seed_a == seed_b
    # Exactly the documented derivation, and inside the range these APIs take.
    expected = int(hashlib.sha256(raster_a).hexdigest()[:8], 16) % (2**31 - 1)
    assert seed_a == expected
    assert 0 <= seed_a < 2**31 - 1
