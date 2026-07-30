"""Deterministic, post-generation disclosure burn-in for illustration media.

``report.illustration_prompt`` already asks the generative model to overlay
the disclosure text (``Overlay text: '{DISCLOSURE}'``), but that is a
request, not a guarantee: a live render on 2026-07-30 (case
``live-forensic-retry-0bb32eeb``, provider ``genblaze``, ``degraded: false``)
produced a clip that never carried the disclosure in any of four sampled
frames. The generative model is free to ignore prompt instructions, and it
did.

This module closes that gap by burning the disclosure into the actual pixels
*after* generation, using only what the project already depends on:

* Pillow (already a hard dependency -- the schematic renderer's own engine)
  draws the caption once as a small, self-contained RGBA image.
* For the still, Pillow composites that caption directly onto the decoded
  image.
* For the clip, ffmpeg (already a container dependency -- see
  ``schematic.encode_mp4``) overlays that same pre-rendered caption image
  onto every frame via the ``overlay`` filter.

ffmpeg's ``drawtext`` filter was deliberately NOT used for the clip: it
requires a font resolvable via ``fontfile=`` or fontconfig, and the
production image installs ffmpeg with no font package
(``apt-get install --no-install-recommends ffmpeg``) -- ``drawtext`` would
very likely fail there with "Cannot find a valid font", which is exactly the
class of silent, looks-right-in-code failure this module exists to close.
Rendering the caption once with Pillow (which ships its own bundled font)
and compositing it as a plain image sidesteps that risk entirely, for both
the still and the clip.

Both burn functions are fail-safe: they NEVER raise and NEVER claim success
they did not achieve. On any failure (undecodable input, ffmpeg absent, a
subprocess error, an empty result) they return the ORIGINAL bytes unchanged
with ``burned=False`` and an ``error`` explaining why -- mirroring the
project's existing honest-degrade idiom (``illustration.degraded`` in
``provenance.py`` / ``config.py``) rather than raising. The caller
(``pipeline.py``) seals whichever bytes come back, plus the honest
``burned``/``error`` flags, so the manifest can never imply a watermark that
is not in the pixels.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .provenance import DISCLOSURE

_CAPTION_FONT_SIZE = 22
_CAPTION_PAD_X = 18
_CAPTION_PAD_Y = 10
_CAPTION_MARGIN_BOTTOM = 24
_CAPTION_CORNER_RADIUS = 8
_TEXT_COLOR = (255, 255, 255, 235)
_BACKING_COLOR = (10, 12, 14, 165)  # semi-transparent near-black backing bar

#: Pillow's bundled default font (Aileron, embedded since Pillow 10.1) has no
#: glyph for U+2014 (em dash) and silently draws a tofu box instead. This
#: substitution is presentation-only -- it affects what gets DRAWN, never the
#: sealed ``DISCLOSURE`` constant/``watermark_text`` manifest field, which
#: always carries the real string unchanged.
_RENDER_SAFE = {"—": "-"}


def _render_safe_text(text: str) -> str:
    for bad, good in _RENDER_SAFE.items():
        text = text.replace(bad, good)
    return text


@dataclass
class BurnResult:
    """The outcome of a watermark burn-in attempt.

    Never raises; always honest. ``data`` is the watermarked bytes when
    ``burned`` is True, otherwise the ORIGINAL, unmodified input bytes.
    ``error`` is a short human-readable reason, set only when ``burned`` is
    False.
    """

    data: bytes
    burned: bool
    error: str | None = None


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


@lru_cache(maxsize=4)
def _caption_font(size: int = _CAPTION_FONT_SIZE):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - Pillow < 10.1 fallback
        return ImageFont.load_default()


def render_caption_png(text: str = DISCLOSURE) -> bytes:
    """Render ``text`` as a standalone RGBA PNG caption: a legible,
    semi-transparent bar sized to fit the text plus padding, white text on a
    dark, semi-transparent backing (readable on light and dark footage
    alike).

    Independent of any target photo/video resolution -- both burn functions
    below place this bar centred at the bottom of their own canvas. Pure and
    deterministic: the same ``text`` always renders the same bytes.
    """
    font = _caption_font()
    render_text = _render_safe_text(text)
    scratch = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bbox = scratch.textbbox((0, 0), render_text, font=font)
    width = (bbox[2] - bbox[0]) + 2 * _CAPTION_PAD_X
    height = (bbox[3] - bbox[1]) + 2 * _CAPTION_PAD_Y
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle([0, 0, width - 1, height - 1],
                           radius=_CAPTION_CORNER_RADIUS, fill=_BACKING_COLOR)
    draw.text((_CAPTION_PAD_X - bbox[0], _CAPTION_PAD_Y - bbox[1]), render_text,
              font=font, fill=_TEXT_COLOR)
    buf = io.BytesIO()
    layer.save(buf, format="PNG")
    return buf.getvalue()


# ── still: Pillow-only compositing ───────────────────────────────────────────
def burn_still_watermark(data: bytes, *, text: str = DISCLOSURE) -> BurnResult:
    """Composite the disclosure caption onto a still image with Pillow.

    Fail-safe: any decode or compose error returns the original ``data``
    unchanged with ``burned=False`` -- never raises, never guesses.
    """
    try:
        base = Image.open(io.BytesIO(data))
        base.load()
        base = base.convert("RGBA")
    except Exception as exc:
        return BurnResult(data=data, burned=False,
                          error=f"could not decode still image: {type(exc).__name__}: {exc}")
    try:
        caption = Image.open(io.BytesIO(render_caption_png(text)))
        x = max(0, (base.width - caption.width) // 2)
        y = max(0, base.height - caption.height - _CAPTION_MARGIN_BOTTOM)
        composited = base.copy()
        composited.alpha_composite(caption, dest=(x, y))
        buf = io.BytesIO()
        composited.convert("RGB").save(buf, format="PNG")
        return BurnResult(data=buf.getvalue(), burned=True)
    except Exception as exc:
        return BurnResult(data=data, burned=False,
                          error=f"caption compose failed: {type(exc).__name__}: {exc}")


# ── clip: ffmpeg overlay of the pre-rendered caption image ──────────────────
def overlay_filter_complex() -> str:
    """The ffmpeg filter_complex graph that burns the caption onto a clip.

    A pure string -- no subprocess, no filesystem -- so the exact filter is
    unit-testable without ffmpeg on PATH. Always the same fixed graph: input
    1 (the caption PNG, pre-rendered by :func:`render_caption_png`) is
    centred ``_CAPTION_MARGIN_BOTTOM`` px above the bottom edge of input 0
    (the clip), sized however the caption was rendered (not scaled to the
    clip, so it stays legible regardless of the clip's resolution).
    """
    return (
        "[0:v][1:v]overlay=(main_w-overlay_w)/2:"
        f"main_h-overlay_h-{_CAPTION_MARGIN_BOTTOM}:format=auto[outv]"
    )


def burn_clip_ffmpeg_cmd(input_path: Path, caption_path: Path, output_path: Path
                         ) -> list[str]:
    """The exact ffmpeg argv used to burn the caption onto a clip.

    A pure function of its paths -- no subprocess -- so the command is
    unit-testable without ffmpeg on PATH. The optional audio stream on input
    0 (``0:a?``) passes through untouched; the filtered video is re-encoded
    (a filter graph cannot be stream-copied).
    """
    return [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(input_path), "-i", str(caption_path),
        "-filter_complex", overlay_filter_complex(),
        "-map", "[outv]", "-map", "0:a?",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(output_path),
    ]


def burn_clip_watermark(data: bytes, *, text: str = DISCLOSURE,
                        timeout: float = 120.0) -> BurnResult:
    """Overlay the disclosure caption onto every frame of a clip via ffmpeg.

    Fail-safe: ffmpeg absent, a caption-render error, a subprocess
    error/timeout, or no output produced all return the ORIGINAL ``data``
    unchanged with ``burned=False`` and an ``error`` -- never raises, never
    ships a false positive.
    """
    if not ffmpeg_available():
        return BurnResult(data=data, burned=False, error="ffmpeg not on PATH")
    try:
        caption_png = render_caption_png(text)
    except Exception as exc:
        return BurnResult(data=data, burned=False,
                          error=f"caption render failed: {type(exc).__name__}: {exc}")
    try:
        with tempfile.TemporaryDirectory(prefix="claimscene-watermark-") as tmp:
            tmp_path = Path(tmp)
            in_path = tmp_path / "in.mp4"
            cap_path = tmp_path / "caption.png"
            out_path = tmp_path / "out.mp4"
            in_path.write_bytes(data)
            cap_path.write_bytes(caption_png)
            cmd = burn_clip_ffmpeg_cmd(in_path, cap_path, out_path)
            try:
                subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
            except (subprocess.SubprocessError, OSError) as exc:
                return BurnResult(data=data, burned=False,
                                  error=f"ffmpeg overlay failed: {type(exc).__name__}: {exc}")
            out_bytes = out_path.read_bytes() if out_path.exists() else b""
    except OSError as exc:  # pragma: no cover - temp-dir/IO failure
        return BurnResult(data=data, burned=False,
                          error=f"temp file handling failed: {type(exc).__name__}: {exc}")
    if not out_bytes:
        return BurnResult(data=data, burned=False, error="ffmpeg produced no output")
    return BurnResult(data=out_bytes, burned=True)
