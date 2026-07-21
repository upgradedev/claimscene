"""LayoutEngine: determinism + geometric sanity properties."""
from __future__ import annotations

import math

import pytest

from claimscene.adapters.fakes import (
    _scene_left_cross,
    _scene_parking_reverse,
    _scene_rear_end,
    _scene_roundabout_sideswipe,
)
from claimscene.layout import (
    LayoutEngine,
    clock_offset_local,
    clock_point_world,
    timeline_from_json,
    timeline_to_json,
)
from claimscene.scene import Movement, Road, SceneGraph, Vehicle

ALL_SCENES = [_scene_rear_end, _scene_left_cross, _scene_parking_reverse,
              _scene_roundabout_sideswipe]


def _impact_index(timeline) -> int:
    times = [p.t for p in timeline.tracks[0].poses]
    return min(range(len(times)), key=lambda i: abs(times[i] - timeline.impact_time_s))


@pytest.mark.parametrize("builder", ALL_SCENES)
def test_same_input_same_output(builder):
    a = LayoutEngine().build(builder())
    b = LayoutEngine().build(builder())
    assert timeline_to_json(a) == timeline_to_json(b)


@pytest.mark.parametrize("builder", ALL_SCENES)
def test_impact_points_touch_at_contact_frame(builder):
    scene = builder()
    timeline = LayoutEngine().build(scene)
    assert timeline.contact_point is not None
    idx = _impact_index(timeline)
    points = []
    for imp in scene.impacts:
        meta = next(v for v in timeline.vehicles if v.id == imp.vehicle_id)
        track = next(t for t in timeline.tracks if t.vehicle_id == imp.vehicle_id)
        points.append(clock_point_world(track.poses[idx], imp.clock_position,
                                        meta.length_m, meta.width_m))
    assert math.dist(points[0], points[1]) < 0.01


@pytest.mark.parametrize("builder", ALL_SCENES)
def test_vehicles_start_off_junction(builder):
    timeline = LayoutEngine().build(builder())
    j = timeline.junction_half_extent_m
    for track in timeline.tracks:
        p0 = track.poses[0]
        assert math.hypot(p0.x, p0.y) > j - 0.01, track.vehicle_id


@pytest.mark.parametrize("builder", ALL_SCENES)
def test_final_keyframes_are_at_rest(builder):
    timeline = LayoutEngine().build(builder())
    for track in timeline.tracks:
        last, prev = track.poses[-1], track.poses[-2]
        assert (last.x, last.y, last.heading_deg) == (prev.x, prev.y, prev.heading_deg)


def test_parked_vehicle_never_moves():
    timeline = LayoutEngine().build(_scene_parking_reverse())
    track = next(t for t in timeline.tracks if t.vehicle_id == "veh_b")
    first = track.poses[0]
    assert all((p.x, p.y, p.heading_deg) == (first.x, first.y, first.heading_deg)
               for p in track.poses)


def test_frame_grid_matches_configuration():
    engine = LayoutEngine(duration_s=6.0, dt_s=0.25)
    timeline = engine.build(_scene_left_cross())
    assert len(timeline.tracks[0].poses) == 25
    assert timeline.tracks[0].poses[0].t == 0.0
    assert timeline.tracks[0].poses[-1].t == 6.0


def test_different_approaches_start_on_different_arms():
    scene = _scene_roundabout_sideswipe()  # veh_a from N, veh_b from W
    timeline = LayoutEngine().build(scene)
    a0 = next(t for t in timeline.tracks if t.vehicle_id == "veh_a").poses[0]
    b0 = next(t for t in timeline.tracks if t.vehicle_id == "veh_b").poses[0]
    assert a0.y > abs(a0.x)  # N arm: mostly +y
    assert b0.x < -abs(b0.y) * 0.5  # W arm: mostly -x


def test_left_turn_changes_heading_about_90_degrees():
    scene = SceneGraph(
        road=Road(layout="x_intersection", lanes_per_direction=1),
        vehicles=[Vehicle(id="veh_a", kind="car", color="red")],
        movements=[Movement(vehicle_id="veh_a", approach="N", maneuver="left_turn",
                            speed_band="moderate")],
    )
    timeline = LayoutEngine().build(scene)
    poses = timeline.tracks[0].poses
    start_h, end_h = poses[0].heading_deg, poses[-1].heading_deg
    assert abs(start_h - 270.0) < 5.0  # heading south
    assert abs(end_h - 0.0) < 5.0 or abs(end_h - 360.0) < 5.0  # heading east


def test_timeline_json_round_trip():
    timeline = LayoutEngine().build(_scene_rear_end())
    restored = timeline_from_json(timeline_to_json(timeline))
    assert restored.model_dump() == timeline.model_dump()


def test_stopped_anchor_stays_at_stop_line_until_impact():
    timeline = LayoutEngine().build(_scene_rear_end())
    track = next(t for t in timeline.tracks if t.vehicle_id == "veh_a")
    idx = _impact_index(timeline)
    pre = track.poses[:idx + 1]
    assert all((p.x, p.y) == (pre[0].x, pre[0].y) for p in pre)
    # After impact the stopped vehicle is shoved forward.
    assert (track.poses[-1].x, track.poses[-1].y) != (pre[0].x, pre[0].y)


def test_clock_offsets_point_where_stated():
    length, width = 4.5, 1.8
    fx, fy = clock_offset_local(12, length, width)
    assert (round(fx, 3), round(fy, 3)) == (2.25, 0.0)  # front
    rx, ry = clock_offset_local(6, length, width)
    assert (round(rx, 3), round(ry, 3)) == (-2.25, 0.0)  # rear
    sx, sy = clock_offset_local(3, length, width)
    assert round(sy, 3) == -0.9 and abs(sx) < 1e-9  # right side


def test_single_lane_lane_change_noted_and_rendered_straight():
    scene = SceneGraph(
        road=Road(layout="straight", lanes_per_direction=1),
        vehicles=[Vehicle(id="veh_a", kind="car", color="red")],
        movements=[Movement(vehicle_id="veh_a", approach="N", maneuver="lane_change",
                            speed_band="low")],
    )
    timeline = LayoutEngine().build(scene)
    assert any("lane_change" in n for n in timeline.notes)
    xs = {p.x for p in timeline.tracks[0].poses}
    assert len(xs) == 1  # no lateral movement on a single-lane road


def test_invalid_engine_configuration_rejected():
    with pytest.raises(ValueError):
        LayoutEngine(impact_time_s=5.9, rest_time_s=5.5)
