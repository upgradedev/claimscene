"""Deterministic camera push (camera.py): compute_camera_path's geometry,
zoompan filter/command construction, and the fail-safe contract.

Mirrors test_watermark.py's structure throughout: pure functions proven
without ffmpeg, the fail-safe contract proven with monkeypatches and
genuinely undecodable input, and the real-success path gated on ffmpeg (and
ffprobe) actually being on PATH. Media stays tiny (96x64, 2 frames).
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from claimscene import camera
from claimscene.adapters.fakes import (
    _scene_left_cross,
    _scene_parking_reverse,
    _scene_rear_end,
    _scene_roundabout_sideswipe,
)
from claimscene.layout import LayoutEngine, TimedPose, Timeline, TimelineVehicle, VehicleTrack
from claimscene.scene import Road, SceneGraph, Vehicle, VehicleColor, VehicleKind
from claimscene.schematic import (
    _vehicle_corners,
    encode_mp4,
    ffmpeg_available,
    impact_frame_index,
    render_frame,
    world_to_pixel,
)

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
needs_ffprobe = pytest.mark.skipif(not camera.ffprobe_available(), reason="ffprobe not on PATH")

ALL_SCENES = [_scene_rear_end, _scene_left_cross, _scene_parking_reverse,
              _scene_roundabout_sideswipe]


def _tiny_timeline(contact_point: tuple[float, float], vehicle_xy: tuple[float, float],
                   *, length_m: float = 4.5, width_m: float = 1.8,
                   heading: float = 0.0) -> Timeline:
    """A minimal, hand-built Timeline (one vehicle, one pose) for testing
    ``compute_camera_path``'s geometry in isolation, with exactly-known
    inputs -- independent of ``LayoutEngine`` so the expected numbers in the
    tests below can be verified by hand rather than by re-deriving the
    system under test's own formula."""
    pose = TimedPose(t=4.0, x=vehicle_xy[0], y=vehicle_xy[1], heading_deg=heading)
    vehicle = TimelineVehicle(id="veh_a", kind=VehicleKind.car, color=VehicleColor.blue,
                              length_m=length_m, width_m=width_m, impact_clock=12)
    track = VehicleTrack(vehicle_id="veh_a", poses=[pose])
    return Timeline(duration_s=6.0, dt_s=0.25, impact_time_s=4.0,
                    road=Road(layout="straight", lanes_per_direction=1, signal="none"),
                    junction_half_extent_m=3.5, contact_point=contact_point,
                    vehicles=[vehicle], tracks=[track])


def _no_impact_timeline() -> Timeline:
    scene = SceneGraph(
        road=Road(layout="parking_lot", lanes_per_direction=1, signal="none"),
        vehicles=[Vehicle(id="veh_a", kind="car", color="blue")])
    timeline = LayoutEngine().build(scene)
    assert timeline.contact_point is None
    return timeline


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


# ── compute_camera_path: exact geometry, hand-verified (no ffmpeg needed) ───
def test_focus_point_maps_to_the_expected_raster_pixel():
    """A hand-built Timeline with a known contact point: the expected pixel
    is computed independently by hand (world_to_pixel(10, -5) = (480 + 80,
    360 + 40) = (560, 400) at the default 960x720x8.0 canvas), not by
    re-deriving the SUT's own formula."""
    timeline = _tiny_timeline((10.0, -5.0), (10.0, -5.0))
    path = camera.compute_camera_path(timeline)
    assert path is not None
    assert path.contact_point_m == (10.0, -5.0)
    assert path.focus_frac == pytest.approx((560 / 960, 400 / 720))


def test_small_vehicle_uses_the_target_zoom_and_stays_unclamped():
    """A normal car-sized vehicle is tiny next to the schematic canvas (a
    few meters against a ~120m x 90m canvas), so the containment constraint
    never binds: ``end_zoom`` is exactly the tasteful default, and the crop
    window centers exactly on the contact point (no edge clamping)."""
    timeline = _tiny_timeline((10.0, -5.0), (10.0, -5.0))
    path = camera.compute_camera_path(timeline)
    assert path is not None
    assert path.end_zoom == pytest.approx(camera.TARGET_END_ZOOM)
    assert path.center_frac == path.focus_frac


def test_camera_path_clamps_the_window_to_keep_the_vehicle_in_frame():
    """An unrealistic, deliberately far-off contact point (well outside the
    canvas, x=200m against a ~60m half-extent) still yields an in-bounds
    crop window: the center clamps toward the vehicle's own padded bounding
    box rather than centering on the impossible focus point. The expected
    clamp is hand-derived (see the PR description) and pinned exactly:
    585.6 / 960 -- the padded vehicle bbox's own right edge."""
    timeline = _tiny_timeline((200.0, 0.0), (0.0, 0.0))
    path = camera.compute_camera_path(timeline)
    assert path is not None
    assert path.focus_frac != path.center_frac
    assert path.center_frac[0] == pytest.approx(585.6 / 960)
    assert path.center_frac[1] == pytest.approx(0.5)  # y was never out of range


def test_camera_path_widens_the_zoom_to_contain_an_unusually_large_footprint():
    """When the (padded) impact-participant bounding box needs more room
    than the tasteful default target, the push backs off to a wider (never
    tighter) zoom rather than cropping anything out -- proven with a
    deliberately oversized synthetic vehicle, well past any real car, van,
    or truck dimension in ``layout.VEHICLE_DIMS``."""
    timeline = _tiny_timeline((0.0, 0.0), (0.0, 0.0), length_m=80.0, width_m=5.0)
    path = camera.compute_camera_path(timeline)
    assert path is not None
    assert camera.TARGET_END_ZOOM < path.end_zoom < 1.0


def test_compute_camera_path_returns_none_without_a_contact_point():
    assert camera.compute_camera_path(_no_impact_timeline()) is None


def _bystander_track() -> VehicleTrack:
    """A track for a non-impact vehicle, just so ``timeline.tracks`` is
    non-empty -- ``schematic.impact_frame_index`` reads ``tracks[0]``
    unconditionally, so a Timeline built to test what happens when an
    IMPACT vehicle's own track is missing still needs some other track
    present, exactly as any real (non-degenerate) Timeline would have."""
    return VehicleTrack(vehicle_id="veh_bystander",
                        poses=[TimedPose(t=4.0, x=20.0, y=20.0, heading_deg=0.0)])


def test_compute_camera_path_excludes_a_non_impact_bystander_vehicle():
    """A vehicle present in the scene but NOT flagged as an impact
    participant (``impact_clock is None`` -- e.g. a third, uninvolved
    parked car) must never influence the bounding box: a bystander placed
    far from the actual impact vehicle would blow the containment window
    wide open if it were wrongly counted."""
    impact_vehicle = TimelineVehicle(id="veh_a", kind=VehicleKind.car, color=VehicleColor.blue,
                                     length_m=4.5, width_m=1.8, impact_clock=12)
    bystander = TimelineVehicle(id="veh_bystander", kind=VehicleKind.car,
                                color=VehicleColor.red, length_m=4.5, width_m=1.8,
                                impact_clock=None)
    impact_pose = TimedPose(t=4.0, x=0.0, y=0.0, heading_deg=0.0)
    timeline = Timeline(duration_s=6.0, dt_s=0.25, impact_time_s=4.0,
                        road=Road(layout="straight", lanes_per_direction=1, signal="none"),
                        junction_half_extent_m=3.5, contact_point=(0.0, 0.0),
                        vehicles=[impact_vehicle, bystander],
                        tracks=[VehicleTrack(vehicle_id="veh_a", poses=[impact_pose]),
                               _bystander_track()])  # bystander is 20m away
    path = camera.compute_camera_path(timeline)
    assert path is not None
    # Matches the single-impact-vehicle case exactly (test above): the
    # far-off bystander contributed nothing to the bounding box.
    assert path.end_zoom == pytest.approx(camera.TARGET_END_ZOOM)
    assert path.center_frac == path.focus_frac == pytest.approx((0.5, 0.5))


def test_compute_camera_path_skips_an_impact_vehicle_with_no_matching_track():
    """Defensive: an impact-flagged vehicle with no corresponding track in
    ``timeline.tracks`` (a caller-mismatched Timeline) is skipped, not a
    crash -- mirrors ``report._impact_pose``'s own defensive ``None`` return
    for the same class of caller bug."""
    ghost = TimelineVehicle(id="veh_ghost", kind=VehicleKind.car, color=VehicleColor.blue,
                            length_m=4.5, width_m=1.8, impact_clock=12)
    timeline = Timeline(duration_s=6.0, dt_s=0.25, impact_time_s=4.0,
                        road=Road(layout="straight", lanes_per_direction=1, signal="none"),
                        junction_half_extent_m=3.5, contact_point=(0.0, 0.0),
                        vehicles=[ghost], tracks=[_bystander_track()])  # no track for veh_ghost
    assert camera.compute_camera_path(timeline) is None


def test_compute_camera_path_returns_none_with_no_participants_at_all():
    """Defensive: a contact point present but no impact-participant corners
    resolvable at all (no vehicle is flagged as an impact participant)
    degrades to None rather than crashing on an empty min()/max()."""
    timeline = Timeline(duration_s=6.0, dt_s=0.25, impact_time_s=4.0,
                        road=Road(layout="straight", lanes_per_direction=1, signal="none"),
                        junction_half_extent_m=3.5, contact_point=(0.0, 0.0),
                        vehicles=[], tracks=[_bystander_track()])
    assert camera.compute_camera_path(timeline) is None


@pytest.mark.parametrize("builder", ALL_SCENES)
def test_compute_camera_path_deterministic_for_same_case(builder):
    a = camera.compute_camera_path(LayoutEngine().build(builder()))
    b = camera.compute_camera_path(LayoutEngine().build(builder()))
    assert a == b


@pytest.mark.parametrize("builder", ALL_SCENES)
def test_both_impact_participants_stay_within_the_tightest_frame(builder):
    """The property the camera push exists to guarantee: at the TIGHTEST
    point of the push (``end_zoom``), every impact participant's full
    footprint -- every corner of its rotated rectangle, at the impact frame
    -- is still inside the crop window. Every wider point of the push
    (anything between the initial full frame and this tightest point)
    trivially also contains it, since the window only shrinks toward this
    one.

    Ground truth is recomputed independently here from the same
    already-tested primitives (``_vehicle_corners``, ``world_to_pixel``,
    ``impact_frame_index``) ``camera.py`` itself uses internally -- this is
    a property/invariant check against real ``LayoutEngine`` output across
    all four scene fixtures, not a re-assertion of the SUT's own internal
    formula.
    """
    scene = builder()
    timeline = LayoutEngine().build(scene)
    path = camera.compute_camera_path(timeline)
    assert path is not None

    impact_i = impact_frame_index(timeline)
    half = path.end_zoom / 2.0
    x_lo, x_hi = path.center_frac[0] - half, path.center_frac[0] + half
    y_lo, y_hi = path.center_frac[1] - half, path.center_frac[1] + half
    eps = 1e-6

    checked_any = False
    for vehicle in timeline.vehicles:
        if vehicle.impact_clock is None:
            continue
        track = next(t for t in timeline.tracks if t.vehicle_id == vehicle.id)
        pose = track.poses[impact_i]
        for wx, wy in _vehicle_corners(pose, vehicle.length_m, vehicle.width_m):
            px, py = world_to_pixel(wx, wy)
            fx, fy = px / 960, py / 720
            assert x_lo - eps <= fx <= x_hi + eps, (
                f"{builder.__name__}: vehicle {vehicle.id!r} corner x={fx} "
                f"outside [{x_lo}, {x_hi}]")
            assert y_lo - eps <= fy <= y_hi + eps, (
                f"{builder.__name__}: vehicle {vehicle.id!r} corner y={fy} "
                f"outside [{y_lo}, {y_hi}]")
            checked_any = True
    assert checked_any  # the fixture actually has impact participants to check


# ── zoompan filter / ffmpeg command: pure construction (no ffmpeg needed) ───
def test_zoompan_filter_is_a_pure_deterministic_string():
    path = camera.CameraPath(contact_point_m=(1.0, 2.0), focus_frac=(0.5, 0.5),
                             center_frac=(0.5, 0.5), end_zoom=0.78)
    a = camera.zoompan_filter(path, duration_s=5.0, output_width=960, output_height=720)
    b = camera.zoompan_filter(path, duration_s=5.0, output_width=960, output_height=720)
    assert a == b
    assert a.startswith("zoompan=")
    assert "s=960x720" in a
    assert "d=1" in a
    assert "0.500000*iw" in a
    assert "0.500000*ih" in a
    assert f"{1 / 0.78:.6f}" in a  # end magnification, formatted to 6dp
    # fps is deliberately NOT part of this pure function's output -- see
    # camera_push_ffmpeg_cmd, which appends it from the probed input clip.
    assert "fps=" not in a
    # drawtext (font-dependent, the exact failure mode watermark.py's own
    # design note avoids) never appears here either.
    assert "drawtext" not in a


def test_zoompan_filter_changes_with_a_different_center_or_zoom():
    base = camera.CameraPath(contact_point_m=(0.0, 0.0), focus_frac=(0.5, 0.5),
                             center_frac=(0.5, 0.5), end_zoom=0.78)
    moved = camera.CameraPath(contact_point_m=(0.0, 0.0), focus_frac=(0.6, 0.4),
                              center_frac=(0.6, 0.4), end_zoom=0.78)
    tighter = camera.CameraPath(contact_point_m=(0.0, 0.0), focus_frac=(0.5, 0.5),
                                center_frac=(0.5, 0.5), end_zoom=0.5)
    a = camera.zoompan_filter(base, duration_s=5.0, output_width=960, output_height=720)
    b = camera.zoompan_filter(moved, duration_s=5.0, output_width=960, output_height=720)
    c = camera.zoompan_filter(tighter, duration_s=5.0, output_width=960, output_height=720)
    assert a != b
    assert a != c


def test_camera_push_ffmpeg_cmd_is_pure_and_wires_the_given_paths():
    cmd = camera.camera_push_ffmpeg_cmd(Path("in.mp4"), Path("out.mp4"),
                                        "zoompan=z='1'", fps="25/1")
    assert cmd[0] == "ffmpeg"
    assert str(Path("in.mp4")) in cmd
    assert str(Path("out.mp4")) in cmd
    assert cmd[-1] == str(Path("out.mp4"))
    # fps is appended onto the filter string here, not baked into
    # zoompan_filter -- see that function's own docstring for why.
    assert "zoompan=z='1':fps=25/1" in cmd
    assert not any("drawtext" in part for part in cmd)


# ── _probe_video_params: fail-closed dimension/frame-rate probing ───────────
def test_probe_video_params_returns_none_on_garbage():
    assert camera._probe_video_params(b"not a real clip") is None


def test_probe_video_params_returns_none_when_ffprobe_absent(monkeypatch):
    monkeypatch.setattr(camera, "ffprobe_available", lambda: False)
    assert camera._probe_video_params(b"irrelevant, never inspected") is None


class _FakeCompletedProcess:
    """A minimal stand-in for ``subprocess.CompletedProcess`` -- just the
    ``stdout`` attribute ``_probe_video_params`` reads."""

    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def test_probe_video_params_returns_none_on_a_stream_with_no_video(monkeypatch):
    """ffprobe can exit 0 with well-formed JSON that simply has no video
    stream (e.g. an audio-only file, or ``-select_streams v:0`` matching
    nothing) -- the empty ``streams`` list must degrade to None, not raise
    an uncaught IndexError."""
    monkeypatch.setattr(camera, "ffprobe_available", lambda: True)
    monkeypatch.setattr(camera.subprocess, "run",
                        lambda *a, **k: _FakeCompletedProcess('{"streams": []}'))
    assert camera._probe_video_params(b"irrelevant, never really decoded") is None


def test_probe_video_params_returns_none_on_non_positive_dimensions(monkeypatch):
    monkeypatch.setattr(camera, "ffprobe_available", lambda: True)
    monkeypatch.setattr(
        camera.subprocess, "run",
        lambda *a, **k: _FakeCompletedProcess(
            '{"streams": [{"width": 0, "height": 64, "r_frame_rate": "2/1"}]}'))
    assert camera._probe_video_params(b"irrelevant, never really decoded") is None


@needs_ffmpeg
@needs_ffprobe
def test_probe_video_params_returns_real_dimensions_on_a_real_tiny_clip():
    assert camera._probe_video_params(_tiny_clip()) == (96, 64, "2/1")


# ── apply_camera_push: fail-safe contract ────────────────────────────────────
def test_apply_camera_push_no_contact_point_is_fail_safe():
    original = b"whatever bytes, never inspected without a contact point"
    result = camera.apply_camera_push(original, _no_impact_timeline(), duration_s=5.0)
    assert result.applied is False
    assert result.data == original
    assert "no contact point" in result.error


def test_apply_camera_push_fail_safe_on_undecodable_input():
    timeline = LayoutEngine().build(_scene_rear_end())
    garbage = b"not a real clip"
    result = camera.apply_camera_push(garbage, timeline, duration_s=5.0)
    assert result.applied is False
    assert result.data == garbage
    assert result.error


def test_apply_camera_push_ffmpeg_absent_is_fail_safe(monkeypatch):
    monkeypatch.setattr(camera, "ffmpeg_available", lambda: False)
    timeline = LayoutEngine().build(_scene_rear_end())
    original = b"whatever bytes, never inspected when ffmpeg is absent"
    result = camera.apply_camera_push(original, timeline, duration_s=5.0)
    assert result.applied is False
    assert result.data == original
    assert result.error == "ffmpeg not on PATH"


def test_apply_camera_push_ffprobe_absent_is_fail_safe(monkeypatch):
    monkeypatch.setattr(camera, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(camera, "ffprobe_available", lambda: False)
    timeline = LayoutEngine().build(_scene_rear_end())
    original = b"whatever bytes, never inspected when ffprobe is absent"
    result = camera.apply_camera_push(original, timeline, duration_s=5.0)
    assert result.applied is False
    assert result.data == original
    assert "probe" in result.error.lower()


def test_apply_camera_push_never_raises_when_ffmpeg_binary_vanishes(monkeypatch):
    """Even if availability was true a moment ago (TOCTOU / a broken PATH
    entry), a subprocess launch failure must still fail closed, not raise --
    exercised here at the probe step (mirrors watermark.py's own version of
    this test); the equivalent failure at the push step itself is exercised
    by the test below with a real probe succeeding first."""
    monkeypatch.setattr(camera, "ffmpeg_available", lambda: True)

    def boom(*_a, **_k):
        raise FileNotFoundError("ffmpeg vanished")

    monkeypatch.setattr(camera.subprocess, "run", boom)
    timeline = LayoutEngine().build(_scene_rear_end())
    original = b"clip bytes"
    result = camera.apply_camera_push(original, timeline, duration_s=5.0)
    assert result.applied is False
    assert result.data == original
    assert result.error


@needs_ffmpeg
@needs_ffprobe
def test_apply_camera_push_fails_safe_when_ffmpeg_vanishes_after_a_successful_probe(
        monkeypatch):
    """The probe (ffprobe) succeeds for real; the SECOND subprocess call
    (the actual camera-push ffmpeg invocation) is the one that vanishes --
    proves the push step's OWN try/except (distinct from the probe's) also
    fails closed rather than raising.

    ``original`` is built BEFORE the monkeypatch is installed: ``_tiny_clip``
    itself shells out to ffmpeg to encode the fixture (via
    ``schematic.encode_mp4``), and that module imports the very same
    ``subprocess`` module object camera.py does -- patching
    ``camera.subprocess.run`` patches it everywhere, so building the fixture
    first keeps the call count below meaning exactly "ffprobe, then ffmpeg".
    """
    real_run = camera.subprocess.run
    original = _tiny_clip()
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_run(*args, **kwargs)  # the real ffprobe call
        raise FileNotFoundError("ffmpeg vanished mid-push")

    monkeypatch.setattr(camera, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(camera.subprocess, "run", flaky)
    timeline = LayoutEngine().build(_scene_rear_end())
    result = camera.apply_camera_push(original, timeline, duration_s=5.0)
    assert result.applied is False
    assert result.data == original
    assert "ffmpeg camera push failed" in result.error


# ── apply_camera_push: real success path (needs ffmpeg + ffprobe) ───────────
@needs_ffmpeg
@needs_ffprobe
@pytest.mark.ffmpeg
def test_apply_camera_push_succeeds_on_a_real_tiny_clip():
    timeline = LayoutEngine().build(_scene_rear_end())
    original = _tiny_clip()
    result = camera.apply_camera_push(original, timeline, duration_s=5.0)
    assert result.applied is True
    assert result.error is None
    assert result.data != original
    assert result.data[4:8] == b"ftyp"  # ISO BMFF magic, mirrors test_watermark.py

    # Genuinely re-decodable, and at the SAME pixel dimensions as the input
    # -- the push changes framing over time, never the encoded resolution.
    probed = camera._probe_video_params(result.data)
    assert probed is not None
    width, height, _fps = probed
    assert (width, height) == (96, 64)


@needs_ffmpeg
@needs_ffprobe
@pytest.mark.ffmpeg
def test_apply_camera_push_preserves_duration_and_frame_count():
    import json as _json
    import subprocess
    import tempfile

    timeline = LayoutEngine().build(_scene_rear_end())
    original = _tiny_clip()
    result = camera.apply_camera_push(original, timeline, duration_s=5.0)
    assert result.applied is True

    def _probe_frames_and_duration(data: bytes) -> tuple[str, str]:
        # JSON, not positional CSV: see camera._probe_video_params's own
        # docstring for why a positional split is not safe to rely on here.
        with tempfile.TemporaryDirectory(prefix="claimscene-camera-test-") as tmp:
            path = Path(tmp) / "clip.mp4"
            path.write_bytes(data)
            proc = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=nb_frames,duration",
                 "-of", "json", str(path)],
                check=True, capture_output=True, text=True, timeout=30)
        stream = _json.loads(proc.stdout)["streams"][0]
        return stream["nb_frames"], stream["duration"]

    before = _probe_frames_and_duration(original)
    after = _probe_frames_and_duration(result.data)
    assert after == before
