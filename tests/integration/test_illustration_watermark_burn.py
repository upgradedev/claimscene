"""Integration: the illustration disclosure is burned into the pixels through
the REAL pipeline (not merely requested in the prompt) -- see
``claimscene.watermark`` and ``pipeline.py`` step 5.

Uses a media-provider test double that returns genuinely decodable bytes
(unlike the shared, production ``FakeMediaProvider``, whose synthetic bytes
are not real media by design and so cannot exercise a successful burn) so
the pipeline's burn-in step has real pixels to overlay. The extractor and
storage are still the project's real offline fakes -- only the media
provider is swapped, and it deliberately does NOT use a "fake"-prefixed
name, so ``degraded=False`` and the manifest's live-illustration burn
requirement actually applies.
"""
from __future__ import annotations

import io

import pytest
from PIL import Image

from claimscene.adapters.fakes import FakeVisionExtractor, InMemoryStorage, _scene_rear_end
from claimscene.case import CasePhoto, CaseSpec, PhotoSource
from claimscene.layout import LayoutEngine
from claimscene.pipeline import CasePipeline
from claimscene.provenance import DISCLOSURE, verify_all, verify_artifact, verify_manifest
from claimscene.schematic import (
    PillowSchematicRenderer,
    encode_mp4,
    ffmpeg_available,
    render_frame,
)

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
    prefixed (so the pipeline seals ``degraded=False``, exercising the
    live/non-degraded illustration-watermark requirement).

    Only a video/clip call reaches this double now: the establish-shot still
    step is gone (see pipeline.py step 5) -- the clip's input image is the
    pipeline's own deterministic schematic raster, never anything this
    provider returns. ``calls``/``video_inputs`` record exactly what this
    double actually saw.
    """

    name = "test-real-media"

    def __init__(self, clip: bytes) -> None:
        self._clip = clip
        self.calls: list[str] = []
        self.video_inputs: list[bytes] | None = None

    def generate(self, *, model, prompt, modality="video", inputs=None, params=None):
        self.calls.append(modality)
        self.video_inputs = inputs
        return self._clip


@pytest.fixture
def photos() -> list[CasePhoto]:
    return [CasePhoto(filename="p.png", data=_tiny_png((5, 5, 5)),
                      source=PhotoSource.staged_demo)]


@needs_ffmpeg
@pytest.mark.ffmpeg
def test_pipeline_seals_the_post_overlay_bytes_for_still_and_clip(photos):
    clip_raw = _tiny_clip()
    storage = InMemoryStorage(bucket="wm-int")
    provider = _RealMediaProvider(clip_raw)
    pipeline = CasePipeline(FakeVisionExtractor(), provider, storage,
                            renderer=PillowSchematicRenderer(animate=False))

    result = pipeline.run(CaseSpec(case_id="wm-int", photos=photos))

    assert verify_manifest(result.manifest) is True
    ill = result.manifest["illustration"]
    assert ill["provider"] == "test-real-media"
    assert ill["degraded"] is False
    assert ill["watermark_text"] == DISCLOSURE
    assert ill["watermark_burned"] is True
    assert ill["watermark_error"] is None
    assert ill["still_watermark_burned"] is True
    assert ill["still_watermark_error"] is None

    # Only the clip reaches this provider now -- the seed is the pipeline's
    # OWN deterministic schematic raster (unwatermarked), never provider
    # output, and it is genuinely decodable (never opaque/garbage bytes).
    assert provider.calls == ["video"]
    assert provider.video_inputs is not None and len(provider.video_inputs) == 1
    seed_bytes = provider.video_inputs[0]
    Image.open(io.BytesIO(seed_bytes)).load()  # genuinely decodable

    # The sealed hashes are the POST-overlay bytes, not the raw generated /
    # rendered ones -- "everything downstream seals the watermarked bytes".
    stored_clip = storage.get(result.artifacts["illustration"].key)
    stored_still = storage.get(result.artifacts["illustration_still"].key)
    assert stored_clip != clip_raw
    assert stored_still != seed_bytes  # the burn actually changed the pixels
    assert verify_artifact(result.manifest, "illustration", stored_clip) is True
    assert verify_artifact(result.manifest, "illustration_still", stored_still) is True

    def fetch(name: str) -> bytes | None:
        ref = result.artifacts.get(name)
        return storage.get(ref.key) if ref is not None else None

    receipt = verify_all(result.manifest, fetch)
    assert receipt.success is True
    ids = {c["id"] for c in receipt.checks}
    assert "structural.illustration_watermark_burned" in ids
    assert "structural.illustration_still_watermark_burned" in ids


def test_pipeline_fail_safe_when_the_provider_returns_undecodable_bytes(photos):
    """A live (non-degraded) CLIP provider that returns bytes the burner
    cannot process must still seal a full case -- with an honest
    ``burned=False`` and the ORIGINAL bytes shipped, never a raise, never a
    false positive.

    The STILL burn is independent of the clip provider now: the seed is
    always the pipeline's own genuine, Pillow-rendered schematic PNG (never
    provider-supplied), so it keeps succeeding even while the clip fails --
    only the clip's disclosure burn is exposed to whatever garbage a live
    provider might return. The still burner's OWN fail-safe path (what
    happens when IT is handed undecodable bytes) stays covered at the unit
    level, see
    test_watermark.py::test_burn_still_watermark_fail_safe_on_undecodable_input.
    """
    garbage_clip = b"not a real clip"
    storage = InMemoryStorage(bucket="wm-int-fail")
    provider = _RealMediaProvider(garbage_clip)
    pipeline = CasePipeline(FakeVisionExtractor(), provider, storage,
                            renderer=PillowSchematicRenderer(animate=False))

    result = pipeline.run(CaseSpec(case_id="wm-int-fail", photos=photos))

    assert verify_manifest(result.manifest) is True
    ill = result.manifest["illustration"]
    assert ill["degraded"] is False
    assert ill["watermark_burned"] is False
    assert ill["watermark_error"]
    assert ill["still_watermark_burned"] is True
    assert ill["still_watermark_error"] is None

    # The clip artifact was NOT dropped, and its sealed hash matches exactly
    # what shipped (the untouched original bytes) -- never a silent mismatch.
    # The still DID burn successfully (see above), so its stored bytes differ
    # from what fed the provider call.
    stored_clip = storage.get(result.artifacts["illustration"].key)
    stored_still = storage.get(result.artifacts["illustration_still"].key)
    assert stored_clip == garbage_clip
    assert stored_still != provider.video_inputs[0]
    assert verify_artifact(result.manifest, "illustration", stored_clip) is True
    assert verify_artifact(result.manifest, "illustration_still", stored_still) is True

    # The aggregate receipt makes the remaining gap LOUD: the clip's missing
    # disclosure still fails the case overall, even though the still is fine.
    def fetch(name: str) -> bytes | None:
        ref = result.artifacts.get(name)
        return storage.get(ref.key) if ref is not None else None

    receipt = verify_all(result.manifest, fetch)
    assert receipt.success is False
    checks_by_id = {c["id"]: c for c in receipt.checks}
    assert checks_by_id["structural.illustration_watermark_burned"]["passed"] is False
    assert checks_by_id["structural.illustration_still_watermark_burned"]["passed"] is True
