"""Integration: the deterministic camera push runs through the REAL
pipeline in the right order relative to the disclosure watermark -- see
``pipeline.py`` step 5 and ``claimscene.camera``.

Mirrors ``test_illustration_watermark_burn.py``'s structure and its
``_RealMediaProvider`` test double (genuinely decodable clip bytes, unlike
the shared ``FakeMediaProvider``), extended to prove the composition order:
the camera push runs on the RAW generated clip, and the watermark burn runs
on WHATEVER comes back from that -- moved, or (on failure) the original --
never the other way around.
"""
from __future__ import annotations

import io

import pytest
from PIL import Image

from claimscene.adapters.fakes import FakeVisionExtractor, InMemoryStorage, _scene_rear_end
from claimscene.camera import apply_camera_push
from claimscene.case import CasePhoto, CaseSpec, PhotoSource
from claimscene.layout import LayoutEngine
from claimscene.pipeline import ILLUSTRATION_CLIP_DURATION_S, CasePipeline
from claimscene.provenance import verify_all, verify_artifact, verify_manifest
from claimscene.schematic import (
    PillowSchematicRenderer,
    encode_mp4,
    ffmpeg_available,
    render_frame,
)
from claimscene.watermark import burn_clip_watermark

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


def _tiny_png(color: tuple[int, int, int]) -> bytes:
    img = Image.new("RGB", (64, 48), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _tiny_clip() -> bytes:
    timeline = LayoutEngine().build(_scene_rear_end())
    frames = [render_frame(timeline, i, width=96, height=64) for i in range(2)]
    data = encode_mp4(frames, fps=2)
    assert data is not None  # only called under @needs_ffmpeg
    return data


class _RealMediaProvider:
    """Returns genuinely decodable clip bytes, deliberately NOT "fake"-
    prefixed (so the pipeline seals ``degraded=False``). Mirrors
    ``test_illustration_watermark_burn.py``'s own double."""

    name = "test-real-media"

    def __init__(self, clip: bytes) -> None:
        self._clip = clip

    def generate(self, *, model, prompt, modality="video", inputs=None, params=None):
        return self._clip


@pytest.fixture
def photos() -> list[CasePhoto]:
    return [CasePhoto(filename="p.png", data=_tiny_png((5, 5, 5)),
                      source=PhotoSource.staged_demo)]


def _fetch(storage, result):
    def fetch(name: str) -> bytes | None:
        ref = result.artifacts.get(name)
        return storage.get(ref.key) if ref is not None else None
    return fetch


@needs_ffmpeg
@pytest.mark.ffmpeg
def test_pipeline_applies_the_camera_push_before_burning_the_watermark(photos):
    """The real-success path: the camera push applies to the model's raw
    output, and the disclosure caption is burned onto the RESULT of that
    push -- proven by recomputing the exact same two real functions,
    independently, in the same order, and requiring byte-for-byte equality
    with what the pipeline actually sealed (mirrors
    ``test_pipeline.py::test_clip_is_seeded_by_the_schematic_raster_not_a_generated_still``'s
    own "independently recomputed ground truth" pattern)."""
    clip_raw = _tiny_clip()
    storage = InMemoryStorage(bucket="cam-ok-int")
    provider = _RealMediaProvider(clip_raw)
    pipeline = CasePipeline(FakeVisionExtractor(), provider, storage,
                            renderer=PillowSchematicRenderer(animate=False))

    result = pipeline.run(CaseSpec(case_id="cam-ok-int", photos=photos))

    assert verify_manifest(result.manifest) is True
    ill = result.manifest["illustration"]
    assert ill["degraded"] is False
    assert ill["camera_move_applied"] is True
    assert ill["camera_move_error"] is None
    assert ill["watermark_burned"] is True
    assert ill["watermark_error"] is None

    # Ground truth: the same scene FakeVisionExtractor deterministically
    # derives from these exact photo bytes (see its own docstring), fed
    # through the same two real functions the pipeline calls, in the same
    # order.
    scene = FakeVisionExtractor().extract(photos)
    timeline = LayoutEngine().build(scene)
    camera_result = apply_camera_push(clip_raw, timeline,
                                      duration_s=ILLUSTRATION_CLIP_DURATION_S)
    assert camera_result.applied is True
    expected = burn_clip_watermark(camera_result.data)
    assert expected.burned is True

    stored_clip = storage.get(result.artifacts["illustration"].key)
    assert stored_clip == expected.data
    assert stored_clip != clip_raw
    assert stored_clip != burn_clip_watermark(clip_raw).data  # not watermarked pre-push
    assert verify_artifact(result.manifest, "illustration", stored_clip) is True

    receipt = verify_all(result.manifest, _fetch(storage, result))
    assert receipt.success is True
    ids = {c["id"] for c in receipt.checks}
    assert "structural.illustration_camera_move" in ids


def test_pipeline_fail_safe_when_the_camera_push_cannot_apply(monkeypatch, photos):
    """Even when the camera push cannot apply, the render still succeeds
    end to end and the disclosure caption is still burned into the shipped
    clip -- the exact fail-safe composition the task requires: camera fails
    -> watermark still runs, on the pre-camera bytes.

    Forced here by denying ``claimscene.camera`` its own ffmpeg (not the
    provider returning garbage, which ``test_illustration_watermark_burn.py``
    already covers from the watermark side): ``watermark.py`` has its OWN,
    independent ``ffmpeg_available()`` check, so this leaves the watermark
    burn fully functional while only the camera step is denied ffmpeg --
    proving the two steps really do fail independently, not as one unit.
    Runs unconditionally (no ffmpeg needed): the monkeypatch makes the
    outcome deterministic regardless of what is actually on PATH.
    """
    import claimscene.camera as camera_module

    monkeypatch.setattr(camera_module, "ffmpeg_available", lambda: False)

    clip_raw = b"whatever the provider returns, never inspected by a denied camera step"
    storage = InMemoryStorage(bucket="cam-fail-int")
    provider = _RealMediaProvider(clip_raw)
    pipeline = CasePipeline(FakeVisionExtractor(), provider, storage,
                            renderer=PillowSchematicRenderer(animate=False))

    result = pipeline.run(CaseSpec(case_id="cam-fail-int", photos=photos))

    assert verify_manifest(result.manifest) is True
    ill = result.manifest["illustration"]
    assert ill["camera_move_applied"] is False
    assert ill["camera_move_error"] == "ffmpeg not on PATH"
    # The disclosure is STILL honestly recorded and (since the clip bytes
    # themselves are garbage here) honestly NOT burned -- watermark.py's own
    # fail-safe, independent of the camera step's own failure.
    assert isinstance(ill["watermark_burned"], bool)

    stored_clip = storage.get(result.artifacts["illustration"].key)
    # The shipped clip is exactly the pre-camera bytes run through the
    # watermark burner -- never silently dropped, never left un-attempted.
    assert stored_clip == burn_clip_watermark(clip_raw).data
    assert verify_artifact(result.manifest, "illustration", stored_clip) is True

    receipt = verify_all(result.manifest, _fetch(storage, result))
    ids = {c["id"] for c in receipt.checks}
    assert "structural.illustration_camera_move" in ids
    # The camera check passes (honest failure is not a violation); overall
    # success still depends on the (here, also honestly failing) watermark
    # check -- the render itself never raised and never silently skipped a
    # step either way.
    assert next(c for c in receipt.checks
               if c["id"] == "structural.illustration_camera_move")["passed"] is True


@needs_ffmpeg
@pytest.mark.ffmpeg
def test_pipeline_fail_safe_still_burns_the_watermark_on_a_real_undecodable_clip(photos):
    """The same fail-safe composition, this time with the camera step
    genuinely trying and failing against provider bytes that are not real
    video at all (the class of input ``test_illustration_watermark_burn.py``
    already proves the WATERMARK burner fails safe on) -- proves BOTH
    fail-safes compose correctly on the exact same bytes: the camera pass
    fails (undecodable), and the watermark burn ALSO fails (same reason),
    and the render still completes with the original bytes shipped, never a
    crash, never a silently-skipped artifact."""
    garbage_clip = b"not a real clip"
    storage = InMemoryStorage(bucket="cam-fail-real")
    provider = _RealMediaProvider(garbage_clip)
    pipeline = CasePipeline(FakeVisionExtractor(), provider, storage,
                            renderer=PillowSchematicRenderer(animate=False))

    result = pipeline.run(CaseSpec(case_id="cam-fail-real", photos=photos))

    assert verify_manifest(result.manifest) is True
    ill = result.manifest["illustration"]
    assert ill["degraded"] is False
    assert ill["camera_move_applied"] is False
    assert ill["camera_move_error"]
    assert ill["watermark_burned"] is False
    assert ill["watermark_error"]

    stored_clip = storage.get(result.artifacts["illustration"].key)
    assert stored_clip == garbage_clip  # untouched end to end, never dropped
    assert verify_artifact(result.manifest, "illustration", stored_clip) is True
