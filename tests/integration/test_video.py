"""Animation encoding via ffmpeg (skipped when ffmpeg is absent)."""
from __future__ import annotations

import pytest

from claimscene.adapters.fakes import _scene_rear_end
from claimscene.layout import LayoutEngine
from claimscene.schematic import (
    PillowSchematicRenderer,
    encode_mp4,
    ffmpeg_available,
    render_frame,
)

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


@needs_ffmpeg
@pytest.mark.ffmpeg
def test_encode_mp4_produces_playable_container():
    timeline = LayoutEngine().build(_scene_rear_end())
    frames = [render_frame(timeline, i) for i in range(0, 8)]
    data = encode_mp4(frames, fps=8)
    assert data is not None
    assert data[4:8] == b"ftyp"  # ISO BMFF magic


@needs_ffmpeg
@pytest.mark.ffmpeg
def test_renderer_attaches_animation_when_enabled():
    timeline = LayoutEngine().build(_scene_rear_end())
    art = PillowSchematicRenderer(animate=True).render(timeline)
    assert art.animation_mp4 is not None
    assert art.animation_mp4[4:8] == b"ftyp"
    assert art.fps == 8


def test_encode_mp4_empty_frames_is_none():
    assert encode_mp4([], fps=8) is None
