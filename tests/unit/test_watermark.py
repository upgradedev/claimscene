"""Disclosure burn-in (watermark.py): caption rendering, still compositing,
clip overlay command construction, and the fail-safe contract.

The clip path deliberately renders the caption with Pillow and overlays it
via ffmpeg's ``overlay`` filter rather than ``drawtext`` (see watermark.py's
module docstring for why), so "the overlay command/filter is constructed
with the exact DISCLOSURE text" is proven in two complementary ways here:
the caption PNG itself changes deterministically with the text
(``test_render_caption_png_*``), and the clip burner is proven to render the
caption with the EXACT sealed text before ffmpeg is ever invoked
(``test_burn_clip_watermark_renders_the_caption_with_the_exact_disclosure_text``),
independent of whether ffmpeg happens to be installed.
"""
from __future__ import annotations

import io

import pytest
from PIL import Image

from claimscene import watermark
from claimscene.adapters.fakes import _scene_rear_end
from claimscene.layout import LayoutEngine
from claimscene.provenance import DISCLOSURE
from claimscene.schematic import encode_mp4, ffmpeg_available, render_frame

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


def _tiny_png(color: tuple[int, int, int] = (40, 90, 140),
             size: tuple[int, int] = (64, 48)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _tiny_clip() -> bytes:
    timeline = LayoutEngine().build(_scene_rear_end())
    frames = [render_frame(timeline, i, width=96, height=64) for i in range(2)]
    data = encode_mp4(frames, fps=2)
    assert data is not None  # only called under @needs_ffmpeg
    return data


# ── caption rendering (pure Pillow, no ffmpeg) ───────────────────────────────
def test_render_caption_png_deterministic_for_same_text():
    assert watermark.render_caption_png(DISCLOSURE) == watermark.render_caption_png(DISCLOSURE)


def test_render_caption_png_default_is_exactly_disclosure():
    assert watermark.render_caption_png() == watermark.render_caption_png(text=DISCLOSURE)


def test_render_caption_png_changes_with_different_text():
    short = watermark.render_caption_png("short")
    longer = watermark.render_caption_png("a much longer caption")
    assert short != longer


def test_render_caption_png_is_a_valid_rgba_image():
    img = Image.open(io.BytesIO(watermark.render_caption_png(DISCLOSURE)))
    img.load()
    assert img.mode == "RGBA"
    assert img.width > 0 and img.height > 0


def test_render_safe_text_substitutes_em_dash_only_for_rendering():
    """Pillow's bundled default font has no glyph for U+2014 and silently
    draws a tofu box -- the substitution is presentation-only and must never
    touch the sealed DISCLOSURE constant itself."""
    assert watermark._render_safe_text("AI-generated illustration — not evidence") == (
        "AI-generated illustration - not evidence")
    assert DISCLOSURE == "AI-generated illustration — not evidence"  # constant untouched


# ── still burn-in (Pillow only) ──────────────────────────────────────────────
def test_burn_still_watermark_succeeds_on_real_png_and_changes_bytes():
    original = _tiny_png()
    result = watermark.burn_still_watermark(original)
    assert result.burned is True
    assert result.error is None
    assert result.data != original
    before = Image.open(io.BytesIO(original))
    after = Image.open(io.BytesIO(result.data))
    assert after.size == before.size  # overlay, not a resize/replace


def test_burn_still_watermark_output_changes_with_the_text_argument():
    original = _tiny_png()
    a = watermark.burn_still_watermark(original, text="caption one")
    b = watermark.burn_still_watermark(original, text="a totally different caption")
    assert a.burned is True and b.burned is True
    assert a.data != b.data


def test_burn_still_watermark_fail_safe_on_undecodable_input():
    garbage = b"not a real image file"
    result = watermark.burn_still_watermark(garbage)
    assert result.burned is False
    assert result.data == garbage
    assert result.error
    assert "could not decode" in result.error


# ── clip: pure command/filter construction (no ffmpeg needed) ───────────────
def test_overlay_filter_complex_is_a_fixed_pure_string():
    a = watermark.overlay_filter_complex()
    b = watermark.overlay_filter_complex()
    assert a == b
    assert "overlay" in a
    assert "[outv]" in a


def test_burn_clip_ffmpeg_cmd_is_pure_and_wires_the_given_paths():
    from pathlib import Path

    cmd = watermark.burn_clip_ffmpeg_cmd(Path("in.mp4"), Path("caption.png"), Path("out.mp4"))
    assert cmd[0] == "ffmpeg"
    assert str(Path("in.mp4")) in cmd
    assert str(Path("caption.png")) in cmd
    assert str(Path("out.mp4")) in cmd
    assert cmd[-1] == str(Path("out.mp4"))
    assert watermark.overlay_filter_complex() in cmd
    # drawtext (font-dependent, the failure mode this design avoids) never appears.
    assert not any("drawtext" in part for part in cmd)


def test_burn_clip_watermark_renders_the_caption_with_the_exact_disclosure_text(monkeypatch):
    """The clip burner renders its caption image with the EXACT sealed
    DISCLOSURE text -- proven independent of whether ffmpeg is actually
    installed on this machine (the availability gate is monkeypatched open;
    the subsequent, inevitable subprocess failure on non-video input is
    caught by the normal fail-safe path and does not affect this assertion)."""
    monkeypatch.setattr(watermark, "ffmpeg_available", lambda: True)
    calls: list[str] = []
    original_render = watermark.render_caption_png

    def spy(text: str = DISCLOSURE) -> bytes:
        calls.append(text)
        return original_render(text)

    monkeypatch.setattr(watermark, "render_caption_png", spy)
    watermark.burn_clip_watermark(b"not a real clip", timeout=5)
    assert calls == [DISCLOSURE]


# ── clip: fail-safe contract ──────────────────────────────────────────────────
def test_burn_clip_watermark_fail_safe_on_undecodable_input():
    garbage = b"not a real media file"
    result = watermark.burn_clip_watermark(garbage, timeout=5)
    assert result.burned is False
    assert result.data == garbage
    assert result.error


def test_burn_clip_watermark_ffmpeg_absent_is_fail_safe(monkeypatch):
    monkeypatch.setattr(watermark, "ffmpeg_available", lambda: False)
    original = b"whatever bytes, never inspected when ffmpeg is absent"
    result = watermark.burn_clip_watermark(original)
    assert result.burned is False
    assert result.data == original
    assert result.error == "ffmpeg not on PATH"


def test_burn_clip_watermark_never_raises_when_ffmpeg_binary_vanishes(monkeypatch):
    """Even if availability was true a moment ago (TOCTOU / a broken PATH
    entry), a subprocess launch failure must still fail closed, not raise."""
    monkeypatch.setattr(watermark, "ffmpeg_available", lambda: True)

    def boom(*_a, **_k):
        raise FileNotFoundError("ffmpeg vanished")

    monkeypatch.setattr(watermark.subprocess, "run", boom)
    original = b"clip bytes"
    result = watermark.burn_clip_watermark(original)
    assert result.burned is False
    assert result.data == original
    assert "ffmpeg overlay failed" in result.error


# ── clip: real success path (needs a real ffmpeg binary) ────────────────────
@needs_ffmpeg
@pytest.mark.ffmpeg
def test_burn_clip_watermark_succeeds_on_a_real_tiny_clip():
    original = _tiny_clip()
    result = watermark.burn_clip_watermark(original)
    assert result.burned is True
    assert result.error is None
    assert result.data != original
    assert result.data[4:8] == b"ftyp"  # ISO BMFF magic, mirrors test_video.py
