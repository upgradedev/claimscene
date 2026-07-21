"""Deterministic LayoutEngine: SceneGraph → typed keyframe timeline.

Pure math, no randomness, no LLM. The engine maps the constrained vocabulary
onto a road template in a metric world frame (x east, y north, meters, origin
at the junction/conflict center; headings in degrees, 0 = east, CCW):

  1. Each moving vehicle gets a canonical path (built for a North approach,
     then rotated to its compass approach): approach lane → maneuver curve
     through the template → exit.
  2. Vehicles are time-scheduled by speed band so that impact participants
     reach the conflict zone exactly at ``impact_time_s``.
  3. The first impact participant (preferring a stopped one) is the *anchor*:
     the world contact point C is the anchor's own damage clock-position
     point at impact. Every other impact participant is positionally
     corrected (blended over the approach) so its stated clock-position
     point touches C at the impact frame — the schematic can then honestly
     draw the stated damage zones in contact.
  4. After impact, participants skid a short, speed-derived distance and
     come to rest; the final keyframes are static.

Same input → byte-identical Timeline (all floats rounded on construction).
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .scene import (
    Approach,
    DamageZone,
    Maneuver,
    Movement,
    Road,
    RoadLayout,
    SceneGraph,
    SequenceEvent,
    SpeedBand,
    VehicleColor,
    VehicleKind,
)

TIMELINE_SCHEMA = "claimscene/timeline/v1"

LANE_WIDTH = 3.5
# (length, width) in meters per vehicle kind.
VEHICLE_DIMS: dict[VehicleKind, tuple[float, float]] = {
    VehicleKind.car: (4.5, 1.8),
    VehicleKind.van: (5.4, 2.0),
    VehicleKind.truck: (7.5, 2.5),
    VehicleKind.motorcycle: (2.2, 0.8),
    VehicleKind.bicycle: (1.8, 0.6),
}
SPEED_MS: dict[SpeedBand, float] = {
    SpeedBand.stopped: 0.0,
    SpeedBand.low: 4.0,
    SpeedBand.moderate: 9.0,
    SpeedBand.high: 15.0,
}
_REVERSING_SPEED = 1.5
_SAMPLE_STEP = 0.5  # path polyline sampling resolution (m)


# ── timeline models (the factual layer's data artifact) ──────────────────────
class TimedPose(BaseModel):
    model_config = ConfigDict(extra="forbid")

    t: float
    x: float
    y: float
    heading_deg: float


class VehicleTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vehicle_id: str
    poses: list[TimedPose]


class TimelineVehicle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: VehicleKind
    color: VehicleColor
    length_m: float
    width_m: float
    damage: list[DamageZone] = Field(default_factory=list)
    impact_clock: int | None = None


class TimelineEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    t: float
    label: str


class Timeline(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_id: Literal["claimscene/timeline/v1"] = Field(
        default=TIMELINE_SCHEMA, alias="schema"
    )
    duration_s: float
    dt_s: float
    impact_time_s: float
    road: Road
    junction_half_extent_m: float
    contact_point: tuple[float, float] | None = None
    vehicles: list[TimelineVehicle]
    tracks: list[VehicleTrack]
    events: list[TimelineEvent] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def timeline_to_json(timeline: Timeline, *, indent: int | None = 2) -> str:
    return timeline.model_dump_json(indent=indent, by_alias=True)


def timeline_from_json(payload: str | bytes) -> Timeline:
    return Timeline.model_validate_json(payload)


# ── small vector helpers ─────────────────────────────────────────────────────
def _rot(p: tuple[float, float], deg: float) -> tuple[float, float]:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return (p[0] * c - p[1] * s, p[0] * s + p[1] * c)


_APPROACH_ROT = {Approach.N: 0.0, Approach.E: -90.0, Approach.S: 180.0, Approach.W: 90.0}


def _sample_line(a: tuple[float, float], b: tuple[float, float]) -> list[tuple[float, float]]:
    dist = math.hypot(b[0] - a[0], b[1] - a[1])
    n = max(2, int(math.ceil(dist / _SAMPLE_STEP)) + 1)
    return [
        (a[0] + (b[0] - a[0]) * i / (n - 1), a[1] + (b[1] - a[1]) * i / (n - 1))
        for i in range(n)
    ]


def _sample_arc(
    center: tuple[float, float], radius: float, deg_from: float, deg_to: float
) -> list[tuple[float, float]]:
    sweep = abs(deg_to - deg_from)
    arc_len = math.radians(sweep) * radius
    n = max(3, int(math.ceil(arc_len / _SAMPLE_STEP)) + 1)
    pts = []
    for i in range(n):
        a = math.radians(deg_from + (deg_to - deg_from) * i / (n - 1))
        pts.append((center[0] + radius * math.cos(a), center[1] + radius * math.sin(a)))
    return pts


def _join(*segments: list[tuple[float, float]]) -> list[tuple[float, float]]:
    path: list[tuple[float, float]] = []
    for seg in segments:
        for p in seg:
            if path and math.hypot(p[0] - path[-1][0], p[1] - path[-1][1]) < 1e-9:
                continue
            path.append(p)
    return path


def clock_offset_local(clock: int, length_m: float, width_m: float) -> tuple[float, float]:
    """Body point for a clock position, in vehicle frame (x fwd, y left)."""
    theta = math.radians(-(clock % 12) * 30.0)
    return (math.cos(theta) * length_m / 2.0, math.sin(theta) * width_m / 2.0)


def clock_point_world(
    pose: TimedPose, clock: int, length_m: float, width_m: float
) -> tuple[float, float]:
    ox, oy = clock_offset_local(clock, length_m, width_m)
    wx, wy = _rot((ox, oy), pose.heading_deg)
    return (pose.x + wx, pose.y + wy)


def _smoothstep(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3.0 - 2.0 * u)


# ── the engine ───────────────────────────────────────────────────────────────
class _PathPlan:
    """A sampled path + schedule for one vehicle (internal working state)."""

    def __init__(
        self,
        points: list[tuple[float, float]],
        *,
        reverse_facing: bool = False,
        static_pose: tuple[float, float, float] | None = None,
        speed: float = 0.0,
    ) -> None:
        self.points = points
        self.reverse_facing = reverse_facing
        self.static_pose = static_pose  # (x, y, heading) for parked vehicles
        self.speed = speed
        self.cum: list[float] = [0.0]
        for i in range(1, len(points)):
            a, b = points[i - 1], points[i]
            self.cum.append(self.cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
        self.total = self.cum[-1] if self.cum else 0.0
        self.s_ref = 0.0  # arc length that must be reached at the reference time
        self.t_ref = 0.0
        self.delta: tuple[float, float] = (0.0, 0.0)  # impact-contact correction
        self.is_impact = False

    def s_at_min_center_distance(self) -> float:
        best_i = min(range(len(self.points)), key=lambda i: math.hypot(*self.points[i]))
        return self.cum[best_i]

    def pose_at_s(self, s: float) -> tuple[float, float, float]:
        if self.static_pose is not None:
            return self.static_pose
        s = min(max(s, 0.0), self.total)
        i = 1
        while i < len(self.cum) and self.cum[i] < s:
            i += 1
        i = min(i, len(self.points) - 1)
        a, b = self.points[i - 1], self.points[i]
        seg = self.cum[i] - self.cum[i - 1]
        u = 0.0 if seg <= 1e-12 else (s - self.cum[i - 1]) / seg
        x = a[0] + (b[0] - a[0]) * u
        y = a[1] + (b[1] - a[1]) * u
        heading = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
        if self.reverse_facing:
            heading += 180.0
        return (x, y, heading % 360.0)


class LayoutEngine:
    def __init__(
        self,
        *,
        duration_s: float = 6.0,
        dt_s: float = 0.25,
        impact_time_s: float = 4.0,
        rest_time_s: float = 5.5,
        start_distance_m: float = 40.0,
    ) -> None:
        if impact_time_s >= rest_time_s or rest_time_s > duration_s:
            raise ValueError("require impact_time_s < rest_time_s <= duration_s")
        self.duration_s = duration_s
        self.dt_s = dt_s
        self.impact_time_s = impact_time_s
        self.rest_time_s = rest_time_s
        self.start_distance_m = start_distance_m

    # ── canonical path construction (North approach, then rotated) ────────────
    def _junction_half(self, road: Road) -> float:
        if road.layout is RoadLayout.parking_lot:
            return 6.0
        return road.lanes_per_direction * LANE_WIDTH

    def _canonical_path(
        self, road: Road, movement: Movement, notes: list[str]
    ) -> tuple[list[tuple[float, float]], bool]:
        """Path for a North approach (entering heading south). Returns
        (points, reverse_facing)."""
        j = self._junction_half(road)
        w = LANE_WIDTH * 0.5  # innermost-lane center offset (right-hand traffic)
        d0 = self.start_distance_m
        m = movement.maneuver

        if m is Maneuver.reversing:
            # Backing toward the conflict zone while facing away from it.
            return _sample_line((-w, j + 5.0), (-w, j - 1.5)), True

        if road.layout is RoadLayout.roundabout and m in (
            Maneuver.straight,
            Maneuver.left_turn,
            Maneuver.right_turn,
            Maneuver.u_turn,
        ):
            return self._roundabout_path(j, w, d0, m), False

        if m is Maneuver.straight:
            return _sample_line((-w, d0), (-w, -d0)), False
        if m is Maneuver.left_turn:
            r = j + w
            entry = _sample_line((-w, d0), (-w, j))
            arc = _sample_arc((-w + r, j), r, 180.0, 270.0)
            exit_ = _sample_line((j, -w), (d0, -w))
            return _join(entry, arc, exit_), False
        if m is Maneuver.right_turn:
            r = max(j - w, 1.0)
            entry = _sample_line((-w, d0), (-w, j))
            arc = _sample_arc((-w - r, j), r, 0.0, -90.0)
            exit_ = _sample_line((-j, w), (-d0, w))
            return _join(entry, arc, exit_), False
        if m is Maneuver.u_turn:
            entry = _sample_line((-w, d0), (-w, j))
            arc = _sample_arc((0.0, j), w, 180.0, 360.0)
            exit_ = _sample_line((w, j), (w, d0)) if d0 > j else [(w, j)]
            return _join(entry, arc, exit_), False
        if m is Maneuver.lane_change:
            if road.lanes_per_direction < 2:
                notes.append(
                    f"vehicle {movement.vehicle_id!r}: lane_change on a single-lane "
                    "road rendered as straight"
                )
                return _sample_line((-w, d0), (-w, -d0)), False
            w2 = w + LANE_WIDTH
            pts: list[tuple[float, float]] = []
            y_hi, y_lo = d0 * 0.75, d0 * 0.35
            for base in _sample_line((0.0, d0), (0.0, -d0)):
                y = base[1]
                if y >= y_hi:
                    x = -w
                elif y <= y_lo:
                    x = -w2
                else:
                    u = (y_hi - y) / (y_hi - y_lo)
                    x = -w - (w2 - w) * _smoothstep(u)
                pts.append((x, y))
            return pts, False
        # Maneuver.parked is handled by the caller (static pose).
        raise AssertionError(f"unhandled maneuver {m}")  # pragma: no cover

    def _roundabout_path(
        self, j: float, w: float, d0: float, m: Maneuver
    ) -> list[tuple[float, float]]:
        ring_r = j + w
        y_e = math.sqrt(max(ring_r**2 - w**2, 0.0))
        entry_deg = math.degrees(math.atan2(y_e, -w))
        exit_specs = {
            Maneuver.right_turn: (math.degrees(math.atan2(w, -y_e)), (-y_e, w), (-d0, w)),
            Maneuver.straight: (math.degrees(math.atan2(-y_e, -w)) + 360.0, (-w, -y_e), (-w, -d0)),
            Maneuver.left_turn: (math.degrees(math.atan2(-w, y_e)) + 360.0, (y_e, -w), (d0, -w)),
            Maneuver.u_turn: (math.degrees(math.atan2(y_e, w)) + 360.0, (w, y_e), (w, d0)),
        }
        exit_deg, ring_exit, road_exit = exit_specs[m]
        entry = _sample_line((-w, d0), (-w, y_e))
        arc = _sample_arc((0.0, 0.0), ring_r, entry_deg, exit_deg)
        exit_ = _sample_line(ring_exit, road_exit)
        return _join(entry, arc, exit_)

    def _parked_pose(self, road: Road, approach: Approach) -> tuple[float, float, float]:
        j = self._junction_half(road)
        w = LANE_WIDTH * 0.5
        if road.layout is RoadLayout.parking_lot:
            local = (-(w + 2.8), j + 2.0)
            heading = 0.0  # perpendicular bay, canonical: facing east
        else:
            local = (-(w + LANE_WIDTH), j + 4.0)
            heading = 270.0  # roadside, canonical: facing along the road (south)
        rot = _APPROACH_ROT[approach]
        x, y = _rot(local, rot)
        return (x, y, (heading + rot) % 360.0)

    # ── plan construction ────────────────────────────────────────────────────
    def _plan_for(self, scene: SceneGraph, movement: Movement | None,
                  notes: list[str]) -> _PathPlan:
        road = scene.road
        if movement is None or movement.maneuver is Maneuver.parked:
            approach = movement.approach if movement else Approach.N
            pose = self._parked_pose(road, approach)
            return _PathPlan([(pose[0], pose[1])], static_pose=pose)

        canonical, reverse_facing = self._canonical_path(road, movement, notes)
        rot = _APPROACH_ROT[movement.approach]
        points = [_rot(p, rot) for p in canonical]
        speed = (
            _REVERSING_SPEED
            if movement.maneuver is Maneuver.reversing
            else SPEED_MS[movement.speed_band]
        )
        return _PathPlan(points, reverse_facing=reverse_facing, speed=speed)

    def _schedule(self, plan: _PathPlan, road: Road, movement: Movement | None) -> None:
        """Fix (s_ref, t_ref): where the vehicle must be at its reference time."""
        if plan.static_pose is not None:
            return
        j = self._junction_half(road)
        s_conflict = plan.s_at_min_center_distance()
        stopped = movement is not None and movement.speed_band is SpeedBand.stopped
        if stopped and movement.maneuver is not Maneuver.reversing:
            plan.s_ref = max(0.0, s_conflict - (j + 2.0))  # holding at the stop line
        else:
            plan.s_ref = s_conflict
        plan.t_ref = self.impact_time_s if plan.is_impact else self.impact_time_s - 1.5

    def _s_at(self, plan: _PathPlan, t: float) -> float:
        v = plan.speed
        if t <= plan.t_ref or not plan.is_impact:
            s = plan.s_ref - v * (plan.t_ref - t)
        else:
            skid = 0.6 * v + 0.8
            u = min(1.0, (t - self.impact_time_s) / (self.rest_time_s - self.impact_time_s))
            s = plan.s_ref + skid * (1.0 - (1.0 - u) ** 2)
        return min(max(s, 0.0), plan.total)

    # ── main ─────────────────────────────────────────────────────────────────
    def build(self, scene: SceneGraph) -> Timeline:
        notes: list[str] = []
        movements = {m.vehicle_id: m for m in scene.movements}
        impact_by_vehicle = {i.vehicle_id: i for i in scene.impacts}

        plans: dict[str, _PathPlan] = {}
        for vehicle in scene.vehicles:
            plan = self._plan_for(scene, movements.get(vehicle.id), notes)
            plan.is_impact = vehicle.id in impact_by_vehicle
            plans[vehicle.id] = plan
        for vehicle in scene.vehicles:
            self._schedule(plans[vehicle.id], scene.road, movements.get(vehicle.id))

        # Anchor: first stopped/static impact participant, else first impact entry.
        contact: tuple[float, float] | None = None
        if scene.impacts:
            def _is_static(vid: str) -> bool:
                p = plans[vid]
                return p.static_pose is not None or p.speed == 0.0

            anchor_id = next(
                (i.vehicle_id for i in scene.impacts if _is_static(i.vehicle_id)),
                scene.impacts[0].vehicle_id,
            )
            dims = {v.id: VEHICLE_DIMS[v.kind] for v in scene.vehicles}
            a_plan = plans[anchor_id]
            ax, ay, ah = a_plan.pose_at_s(self._s_at(a_plan, self.impact_time_s))
            a_pose = TimedPose(t=self.impact_time_s, x=ax, y=ay, heading_deg=ah)
            a_clock = impact_by_vehicle[anchor_id].clock_position
            contact = clock_point_world(a_pose, a_clock, *dims[anchor_id])

            for imp in scene.impacts:
                if imp.vehicle_id == anchor_id:
                    continue
                plan = plans[imp.vehicle_id]
                nx, ny, nh = plan.pose_at_s(self._s_at(plan, self.impact_time_s))
                length_m, width_m = dims[imp.vehicle_id]
                ox, oy = clock_offset_local(imp.clock_position, length_m, width_m)
                wx, wy = _rot((ox, oy), nh)
                plan.delta = (contact[0] - (nx + wx), contact[1] - (ny + wy))

        # Sample the keyframe grid.
        n_frames = int(round(self.duration_s / self.dt_s)) + 1
        tracks: list[VehicleTrack] = []
        for vehicle in scene.vehicles:
            plan = plans[vehicle.id]
            poses: list[TimedPose] = []
            for i in range(n_frames):
                t = min(i * self.dt_s, self.duration_s)
                t_eff = min(t, self.rest_time_s)  # frozen at rest
                x, y, heading = plan.pose_at_s(self._s_at(plan, t_eff))
                dx, dy = plan.delta
                if plan.delta != (0.0, 0.0):
                    if plan.speed == 0.0 or plan.static_pose is not None:
                        f = 1.0
                    else:
                        f = _smoothstep(t_eff / self.impact_time_s)
                    x, y = x + dx * f, y + dy * f
                poses.append(
                    TimedPose(
                        t=round(t, 2),
                        x=round(x, 3),
                        y=round(y, 3),
                        heading_deg=round(heading % 360.0, 2),
                    )
                )
            tracks.append(VehicleTrack(vehicle_id=vehicle.id, poses=poses))

        events = self._events(scene)
        vehicles_meta = [
            TimelineVehicle(
                id=v.id,
                kind=v.kind,
                color=v.color,
                length_m=VEHICLE_DIMS[v.kind][0],
                width_m=VEHICLE_DIMS[v.kind][1],
                damage=v.damage,
                impact_clock=(
                    impact_by_vehicle[v.id].clock_position
                    if v.id in impact_by_vehicle
                    else None
                ),
            )
            for v in scene.vehicles
        ]
        return Timeline(
            duration_s=self.duration_s,
            dt_s=self.dt_s,
            impact_time_s=self.impact_time_s,
            road=scene.road,
            junction_half_extent_m=self._junction_half(scene.road),
            contact_point=(
                (round(contact[0], 3), round(contact[1], 3)) if contact else None
            ),
            vehicles=vehicles_meta,
            tracks=tracks,
            events=events,
            notes=notes,
        )

    def _events(self, scene: SceneGraph) -> list[TimelineEvent]:
        t_i, t_r = self.impact_time_s, self.rest_time_s
        when = {
            SequenceEvent.vehicles_approach: 0.25,
            SequenceEvent.braking: t_i - 1.0,
            SequenceEvent.evasive_steering: t_i - 0.75,
            SequenceEvent.impact: t_i,
            SequenceEvent.secondary_impact: t_i + 0.5,
            SequenceEvent.vehicles_come_to_rest: t_r,
        }
        seq = scene.sequence or (
            [SequenceEvent.vehicles_approach]
            + ([SequenceEvent.impact] if scene.impacts else [])
            + [SequenceEvent.vehicles_come_to_rest]
        )
        return [
            TimelineEvent(t=round(when[e], 2), label=e.value)
            for e in sorted(set(seq), key=lambda e: when[e])
        ]
