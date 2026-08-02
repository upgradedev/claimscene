"""Deterministic camera push, computed by us -- never requested from or
produced by the generative model.

Two live renders already proved the model cannot be steered on anything we
need to control:

* Prompting the exact geometry failed (a stated rear-end collision rendered
  as a head-on collision) -- fixed structurally by seeding the clip with the
  deterministic schematic raster instead of asking for it in words (see
  ``pipeline.py`` step 5 / ``schematic.SchematicArtifacts.seed_png``).
* Prompting camera restraint failed too: a live render on 2026-07-31 asked
  for "gentle camera parallax and a slow push" and a ``negative_prompt``
  naming "zoom out"; the clip still ran from an unusably wide shot to an
  extreme close-up. ``pixverse-v6-i2v`` exposes no motion or camera
  parameter at all -- verified against the SDK's own registry, not assumed:
  ``GMICloudVideoProvider.create_registry().get("pixverse-v6-i2v").param_allowlist``
  is exactly ``{aspect_ratio, cfg_scale, duration, image, image_url,
  negative_prompt, prompt, quality, resolution, seed, video, video_url}``.

So we stopped asking. ``report.illustration_prompt`` now asks the model for
a still, locked-off shot with no camera movement of any kind -- the model
supplies the look, we supply the motion. This module supplies the motion: a
slow, deterministic push toward the reconstructed point of impact, computed
purely from the case's own factual ``Timeline`` (``layout.py``), the same
sealed record the schematic itself is drawn from.

Coordinate space and the aspect-ratio assumption
--------------------------------------------------
The push targets the seed raster's own pixel space (``schematic.py``'s
``world_to_pixel``, the exact projection that drew the seed image the clip
was generated from), then re-expresses every coordinate as a FRACTION of
that raster's own width/height. The ffmpeg filter built from those fractions
references the clip's actual runtime frame size (ffmpeg's ``iw``/``ih``), so
it re-targets correctly even if the delivered clip's pixel dimensions differ
from the seed's 960x720. What it CANNOT correct for is a different field of
view: the whole computation assumes the delivered clip frames the same
scene, at the same aspect ratio, as the seed image the model was given (a
locked-off shot of a static diagram, per the prompt). The pipeline states
this assumption structurally by requesting ``aspect_ratio="4:3"`` (the
seed's own ratio) from the provider; if a live render frames the shot
differently anyway, the padding built into :func:`compute_camera_path` is
the mitigation, not a guarantee. This is exactly the thing real-pixel
verification needs to confirm.

Fail-safe composition
----------------------
:func:`apply_camera_push` runs on the RAW generated clip, BEFORE the
disclosure caption is burned in (``watermark.burn_clip_watermark``) -- so
the caption is drawn once, on the final framing, and the push can never
crop or distort it. On any failure (no contact point to target, ffmpeg or
ffprobe absent, undecodable input, a subprocess error, an empty result) this
module NEVER raises and NEVER claims a push it did not achieve: it returns
the ORIGINAL bytes unchanged with ``applied=False`` and an ``error``
explaining why, mirroring ``watermark.BurnResult``'s honest-degrade idiom.
The caller (``pipeline.py``) always still runs the watermark burn on
whatever bytes come back -- moved or not -- so a clip is never shipped
without its disclosure, and the render never fails outright because the
camera pass failed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .layout import Timeline
from .schematic import _vehicle_corners, impact_frame_index, world_to_pixel

#: Must match ``PillowSchematicRenderer``'s own defaults (schematic.py) --
#: these are the dimensions the pipeline's seed image (``art.seed_png``) is
#: actually rendered at, since the camera path targets THAT raster's own
#: pixel space (see module docstring). Trivial to change, but changing one
#: without the other re-introduces the exact mismatch this module exists to
#: avoid.
SEED_WIDTH = 960
SEED_HEIGHT = 720
SEED_SCALE = 8.0

#: How tight the push gets by the end of the clip, as a fraction of the
#: frame still visible (1.0 = no zoom at all; smaller = tighter). 0.78 is a
#: gentle ~22% push-in -- tasteful and subtle, no whip motion. This is a
#: TARGET, not a guarantee: :func:`compute_camera_path` backs off (uses a
#: larger, wider fraction) whenever this value would crop either impact
#: participant out of frame.
TARGET_END_ZOOM = 0.78

#: Extra breathing room around the tight bounding box of both impact
#: participants, as a fraction of that box's own size (0.35 = 35% larger on
#: each axis). Purely defensive for realistic scenes -- two vehicles in
#: contact occupy a few meters, tiny next to the schematic's ~120m x 90m
#: canvas, so :data:`TARGET_END_ZOOM` is the value that actually governs in
#: practice; this only binds for unusually large or spread-out participants.
PADDING_FRAC = 0.35


@dataclass(frozen=True)
class CameraPath:
    """A deterministic push toward the reconstructed point of impact,
    computed purely from the factual Timeline's own geometry.

    All coordinates are FRACTIONS of the seed raster's own canvas (0..1),
    not absolute pixels -- see the module docstring for why, and for the
    aspect-ratio assumption this implies.
    """

    #: The world-frame point (meters, x east / y north) this push targets --
    #: exactly ``Timeline.contact_point``, carried through for the manifest
    #: note and for tests to cross-check against.
    contact_point_m: tuple[float, float]
    #: The contact point's own position, as a fraction of the seed canvas --
    #: the IDEAL push target, before any containment clamping.
    focus_frac: tuple[float, float]
    #: The push's actual crop-window center, as a fraction of the seed
    #: canvas -- clamped so both impact participants (plus padding) stay
    #: fully inside the frame at the tightest point of the push. Equal to
    #: ``focus_frac`` unless clamping was needed.
    center_frac: tuple[float, float]
    #: Fraction of the frame visible at the tightest point of the push
    #: (<=1.0). See :data:`TARGET_END_ZOOM`.
    end_zoom: float


def _impact_participant_corners_px(
    timeline: Timeline, *, width: int, height: int, scale: float
) -> list[tuple[float, float]]:
    """Every impact participant's own footprint corners (rotated rectangle,
    world frame) at the impact frame, projected into the seed raster's pixel
    space -- the exact same geometry (:func:`schematic._vehicle_corners`)
    and the exact same projection (:func:`schematic.world_to_pixel`)
    ``schematic.py`` uses to draw those vehicles into the seed image, so the
    bounding box this yields describes exactly what is visible in the
    pixels the model was actually seeded with. A vehicle is an "impact
    participant" iff ``TimelineVehicle.impact_clock is not None`` -- the
    same signal ``report._unique_impact_vehicle_ids`` derives from
    ``scene.impacts``, but read directly off the Timeline so this module
    never needs the SceneGraph.
    """
    impact_i = impact_frame_index(timeline)
    corners: list[tuple[float, float]] = []
    for vehicle in timeline.vehicles:
        if vehicle.impact_clock is None:
            continue
        track = next((t for t in timeline.tracks if t.vehicle_id == vehicle.id), None)
        if track is None or impact_i >= len(track.poses):
            continue  # a caller-mismatched Timeline: degrade, never crash
        pose = track.poses[impact_i]
        for wx, wy in _vehicle_corners(pose, vehicle.length_m, vehicle.width_m):
            corners.append(world_to_pixel(wx, wy, width=width, height=height, scale=scale))
    return corners


def compute_camera_path(
    timeline: Timeline,
    *,
    width: int = SEED_WIDTH,
    height: int = SEED_HEIGHT,
    scale: float = SEED_SCALE,
    padding_frac: float = PADDING_FRAC,
    target_end_zoom: float = TARGET_END_ZOOM,
) -> CameraPath | None:
    """Compute the deterministic push for ``timeline``, or ``None`` when
    there is nothing to target (no contact point -- a scene with no
    impacts, or a caller-mismatched Timeline with no impact participants).

    Pure and deterministic: the same Timeline always yields the same
    CameraPath, since every input is already deterministic (the Timeline
    itself, per ``layout.LayoutEngine``) and every step here is plain
    arithmetic -- no randomness, no model call.

    The end zoom is the tighter of two things: :data:`target_end_zoom` (the
    tasteful default), or the loosest zoom that still keeps every impact
    participant's full footprint -- plus :data:`padding_frac` breathing room
    -- inside the frame. The crop window's center is the contact point,
    clamped only as far as needed to keep that same footprint-plus-padding
    box fully within the window and within the canvas -- which is always
    possible by construction (the window is sized to contain the box in the
    first place), so both impact participants stay fully inside the frame
    for the whole push, not just at one sampled instant.
    """
    if timeline.contact_point is None:
        return None
    corners = _impact_participant_corners_px(timeline, width=width, height=height, scale=scale)
    if not corners:
        return None

    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    bx0, bx1 = min(xs), max(xs)
    by0, by1 = min(ys), max(ys)
    bbox_w, bbox_h = bx1 - bx0, by1 - by0

    # The loosest zoom (largest fraction) needed so a frame-aspect-ratio
    # window can contain the padded box on BOTH axes at once.
    contain_zoom = max(bbox_w / width, bbox_h / height) * (1.0 + padding_frac)
    end_zoom = min(1.0, max(target_end_zoom, contain_zoom))

    # The padded box, clipped to the canvas -- the hard containment target
    # for the clamped centering below.
    cx_bbox, cy_bbox = (bx0 + bx1) / 2.0, (by0 + by1) / 2.0
    pad_w, pad_h = bbox_w * padding_frac / 2.0, bbox_h * padding_frac / 2.0
    pbx0 = max(0.0, cx_bbox - bbox_w / 2.0 - pad_w)
    pbx1 = min(float(width), cx_bbox + bbox_w / 2.0 + pad_w)
    pby0 = max(0.0, cy_bbox - bbox_h / 2.0 - pad_h)
    pby1 = min(float(height), cy_bbox + bbox_h / 2.0 + pad_h)

    focus_x, focus_y = world_to_pixel(*timeline.contact_point, width=width,
                                      height=height, scale=scale)

    # Clamp the window's center as close to the focus point as possible
    # while (a) staying on-canvas and (b) fully containing the padded box.
    # Non-empty by construction: end_zoom was chosen so the window is at
    # least as large as the padded box on both axes.
    crop_w, crop_h = end_zoom * width, end_zoom * height
    hw, hh = crop_w / 2.0, crop_h / 2.0
    lo_x, hi_x = max(pbx1 - hw, hw), min(pbx0 + hw, width - hw)
    lo_y, hi_y = max(pby1 - hh, hh), min(pby0 + hh, height - hh)
    center_x = min(max(focus_x, lo_x), hi_x)
    center_y = min(max(focus_y, lo_y), hi_y)

    return CameraPath(
        contact_point_m=timeline.contact_point,
        focus_frac=(focus_x / width, focus_y / height),
        center_frac=(center_x / width, center_y / height),
        end_zoom=end_zoom,
    )


# ── ffmpeg: pure filter/command construction (no subprocess) ────────────────
def _fmt(value: float) -> str:
    """Fixed precision so the same CameraPath always formats to the exact
    same filter string -- makes :func:`zoompan_filter` testable with a plain
    string comparison instead of a fuzzy/parsed one."""
    return f"{value:.6f}"


def zoompan_filter(path: CameraPath, *, duration_s: float,
                   output_width: int, output_height: int) -> str:
    """The ffmpeg ``zoompan`` filter graph for ``path``.

    A pure string -- no subprocess, no filesystem -- so the exact filter is
    unit-testable without ffmpeg on PATH (mirrors
    ``watermark.overlay_filter_complex``).

    ``zoompan``'s own ``z`` is a MAGNIFICATION (1 = no zoom, >1 = tighter),
    the reciprocal of :attr:`CameraPath.end_zoom`'s "fraction of frame
    visible" convention. ``z`` ramps from 1.0 to ``1/end_zoom`` over
    ``duration_s`` via a smoothstep ease (``u*u*(3-2*u)``, the same easing
    ``layout._smoothstep`` uses elsewhere in this codebase) so the push
    eases in and out rather than moving at a constant rate. ``x``/``y``
    (the crop window's top-left corner) are expressed in terms of ffmpeg's
    own ``iw``/``ih``/``zoom`` so the same filter re-targets correctly
    regardless of the clip's actual runtime pixel dimensions (see the
    module docstring's aspect-ratio note for what this does and does not
    protect against).

    ``d=1`` (one output frame per input frame -- the default of 90 is built
    for looping a single still, not a real video) and an explicit ``s=``
    (the default is a hard-coded 1280x720 otherwise) are both required:
    each is a documented zoompan trap that silently resamples or
    force-resizes when left implicit. ``fps=`` (default 25, the third such
    trap) is deliberately NOT set here -- :func:`camera_push_ffmpeg_cmd`
    appends it from the clip's own PROBED frame rate, so this function stays
    a pure function of the CameraPath alone (never of a probed fact).
    """
    dur = _fmt(max(duration_s, 1e-6))  # guard against a division by zero
    end_mag = _fmt(1.0 / path.end_zoom)
    cx, cy = _fmt(path.center_frac[0]), _fmt(path.center_frac[1])
    # Inner commas in a single expression must be backslash-escaped so
    # ffmpeg's filtergraph parser does not read them as filter separators.
    u = f"clip(time/{dur}\\,0\\,1)"
    smoothstep = f"({u})*({u})*(3-2*({u}))"
    zoom_expr = f"1+({end_mag}-1)*{smoothstep}"
    x_expr = f"{cx}*iw-(iw/zoom)/2"
    y_expr = f"{cy}*ih-(ih/zoom)/2"
    return (
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':"
        f"d=1:s={output_width}x{output_height}"
    )


def camera_push_ffmpeg_cmd(input_path: Path, output_path: Path, filter_str: str,
                           *, fps: str) -> list[str]:
    """The exact ffmpeg argv used to apply ``filter_str`` to a clip.

    A pure function of its arguments -- no subprocess -- so the command is
    unit-testable without ffmpeg on PATH (mirrors
    ``watermark.burn_clip_ffmpeg_cmd``). ``fps`` is appended onto the
    zoompan filter here (rather than baked into ``filter_str``) so
    :func:`zoompan_filter` stays a pure function of the CameraPath alone --
    the frame rate is a property of the INPUT clip (probed at call time),
    not of the camera path. The optional audio stream (``0:a?``) passes
    through untouched; the filtered video is re-encoded (a filter graph
    cannot be stream-copied).
    """
    return [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(input_path),
        "-vf", f"{filter_str}:fps={fps}",
        "-map", "0:v", "-map", "0:a?",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(output_path),
    ]


# ── ffprobe: input-clip dimensions + frame rate (fail-closed) ───────────────
def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def _probe_video_params(data: bytes, *, timeout: float = 30.0
                        ) -> tuple[int, int, str] | None:
    """Return ``(width, height, r_frame_rate)`` for ``data``, or ``None`` on
    any failure -- never raises. ``r_frame_rate`` is ffprobe's own "N/D"
    string, passed straight through to ffmpeg's ``fps=`` (which accepts that
    form directly), so no floating-point frame-rate rounding is introduced
    by this module.

    The output size/frame-rate must be probed rather than assumed: the
    delivered clip's actual pixel dimensions are a live-provider fact this
    module cannot know offline (see the module docstring's aspect-ratio
    note) -- guessing them wrong would silently resample or distort the
    clip, exactly the class of silent failure this project's fail-safe
    conventions exist to avoid.

    Parsed as JSON (``-of json``), not a positional CSV: ffprobe's ``csv``
    writer orders fields by its own fixed internal stream-field order, NOT
    by the order requested in ``-show_entries`` (verified empirically -- a
    ``duration,nb_frames`` request came back as ``duration`` THEN
    ``nb_frames`` regardless), so a positional split is one silent SDK/ffmpeg
    version bump away from mis-assigning width to height. JSON keys are
    unambiguous by name.
    """
    if not ffprobe_available():
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="claimscene-camera-probe-") as tmp:
            in_path = Path(tmp) / "in.mp4"
            in_path.write_bytes(data)
            cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
                   "-show_entries", "stream=width,height,r_frame_rate",
                   "-of", "json", str(in_path)]
            proc = subprocess.run(cmd, check=True, capture_output=True,
                                  timeout=timeout, text=True)
    except (subprocess.SubprocessError, OSError):
        return None
    try:
        stream = json.loads(proc.stdout)["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
        r_frame_rate = str(stream["r_frame_rate"])
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0 or not r_frame_rate:
        return None
    return width, height, r_frame_rate


# ── the fail-safe orchestrator ───────────────────────────────────────────────
@dataclass
class CameraMoveResult:
    """The outcome of a camera-push attempt.

    Never raises; always honest. ``data`` is the pushed bytes when
    ``applied`` is True, otherwise the ORIGINAL, unmodified input bytes.
    ``error`` is a short human-readable reason, set only when ``applied`` is
    False. Mirrors ``watermark.BurnResult``.
    """

    data: bytes
    applied: bool
    error: str | None = None


def apply_camera_push(
    data: bytes,
    timeline: Timeline,
    *,
    duration_s: float,
    seed_width: int = SEED_WIDTH,
    seed_height: int = SEED_HEIGHT,
    seed_scale: float = SEED_SCALE,
    padding_frac: float = PADDING_FRAC,
    target_end_zoom: float = TARGET_END_ZOOM,
    timeout: float = 120.0,
) -> CameraMoveResult:
    """Apply the deterministic push computed from ``timeline`` to the clip
    ``data``.

    Fail-safe: no contact point to target, ffmpeg/ffprobe absent, an
    unprobeable/undecodable clip, a subprocess error/timeout, or no output
    produced all return the ORIGINAL ``data`` unchanged with
    ``applied=False`` and an ``error`` -- never raises, never ships a false
    positive. Callers (``pipeline.py``) always still run the disclosure
    watermark burn on whatever bytes come back, so a failed push here never
    means an unwatermarked clip and never fails the render outright.
    """
    path = compute_camera_path(timeline, width=seed_width, height=seed_height,
                               scale=seed_scale, padding_frac=padding_frac,
                               target_end_zoom=target_end_zoom)
    if path is None:
        return CameraMoveResult(data=data, applied=False,
                                error="no contact point in timeline "
                                "(no impact participants to target)")
    if not ffmpeg_available():
        return CameraMoveResult(data=data, applied=False, error="ffmpeg not on PATH")
    probe = _probe_video_params(data, timeout=timeout)
    if probe is None:
        return CameraMoveResult(data=data, applied=False,
                                error="could not probe clip dimensions/frame "
                                "rate (ffprobe absent or the clip is not "
                                "decodable video)")
    width, height, fps = probe
    filter_str = zoompan_filter(path, duration_s=duration_s,
                                output_width=width, output_height=height)
    try:
        with tempfile.TemporaryDirectory(prefix="claimscene-camera-") as tmp:
            tmp_path = Path(tmp)
            in_path = tmp_path / "in.mp4"
            out_path = tmp_path / "out.mp4"
            in_path.write_bytes(data)
            cmd = camera_push_ffmpeg_cmd(in_path, out_path, filter_str, fps=fps)
            try:
                subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
            except (subprocess.SubprocessError, OSError) as exc:
                return CameraMoveResult(data=data, applied=False,
                                        error=f"ffmpeg camera push failed: "
                                        f"{type(exc).__name__}: {exc}")
            out_bytes = out_path.read_bytes() if out_path.exists() else b""
    except OSError as exc:  # pragma: no cover - temp-dir/IO failure
        return CameraMoveResult(data=data, applied=False,
                                error=f"temp file handling failed: "
                                f"{type(exc).__name__}: {exc}")
    if not out_bytes:
        return CameraMoveResult(data=data, applied=False,
                                error="ffmpeg produced no output")
    return CameraMoveResult(data=out_bytes, applied=True)
