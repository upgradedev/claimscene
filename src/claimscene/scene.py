"""Constrained accident-scene vocabulary — the anti-hallucination core.

The VLM never emits free-form spatial coordinates. It can only fill this
closed vocabulary: enum vehicle kinds and colors, clock-position damage
zones (1..12), a small set of road templates, compass approaches, a fixed
maneuver list, and speed *bands*. Everything geometric (positions, paths,
contact points) is derived later by the deterministic LayoutEngine.

Strictness rules (all enforced by Pydantic v2):
  * ``extra="forbid"`` on every model — a hallucinated field fails validation.
  * Every optional field has an explicit ``= None`` default (Pydantic v2
    treats ``T | None`` without a default as *required*).
  * Cross-references are validated: a movement or impact that names an
    unknown vehicle id is rejected, as are duplicate vehicle ids.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCENE_SCHEMA = "claimscene/scene/v1"


# ── closed enums ─────────────────────────────────────────────────────────────
class VehicleKind(str, Enum):
    car = "car"
    van = "van"
    truck = "truck"
    motorcycle = "motorcycle"
    bicycle = "bicycle"


class VehicleColor(str, Enum):
    white = "white"
    black = "black"
    silver = "silver"
    gray = "gray"
    red = "red"
    blue = "blue"
    green = "green"
    yellow = "yellow"


class DamageSeverity(str, Enum):
    scratch = "scratch"
    dent = "dent"
    crush = "crush"


class RoadLayout(str, Enum):
    straight = "straight"
    t_intersection = "t_intersection"
    x_intersection = "x_intersection"
    roundabout = "roundabout"
    parking_lot = "parking_lot"


class Signal(str, Enum):
    none = "none"
    stop_sign = "stop_sign"
    traffic_light = "traffic_light"
    yield_sign = "yield"


class Approach(str, Enum):
    N = "N"
    E = "E"
    S = "S"
    W = "W"


class Maneuver(str, Enum):
    straight = "straight"
    left_turn = "left_turn"
    right_turn = "right_turn"
    u_turn = "u_turn"
    lane_change = "lane_change"
    reversing = "reversing"
    parked = "parked"


class SpeedBand(str, Enum):
    stopped = "stopped"
    low = "low"
    moderate = "moderate"
    high = "high"


class SequenceEvent(str, Enum):
    """Coarse, enumerated incident phases — never free-form narrative."""

    vehicles_approach = "vehicles_approach"
    braking = "braking"
    evasive_steering = "evasive_steering"
    impact = "impact"
    secondary_impact = "secondary_impact"
    vehicles_come_to_rest = "vehicles_come_to_rest"


# ── models ───────────────────────────────────────────────────────────────────
class DamageZone(BaseModel):
    """Damage located by clock position on the vehicle body (12 = front)."""

    model_config = ConfigDict(extra="forbid")

    clock_position: int = Field(ge=1, le=12)
    severity: DamageSeverity
    note: str | None = None


class Vehicle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=32)
    kind: VehicleKind
    color: VehicleColor
    damage: list[DamageZone] = Field(default_factory=list)


class Road(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layout: RoadLayout
    lanes_per_direction: int = Field(ge=1, le=3)
    signal: Signal = Signal.none


class Movement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vehicle_id: str
    approach: Approach
    maneuver: Maneuver
    speed_band: SpeedBand


class Impact(BaseModel):
    """Where the vehicle was struck, by clock position on its own body."""

    model_config = ConfigDict(extra="forbid")

    vehicle_id: str
    clock_position: int = Field(ge=1, le=12)


class SceneGraph(BaseModel):
    """The complete constrained description of one incident scene."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_id: Literal["claimscene/scene/v1"] = Field(default=SCENE_SCHEMA, alias="schema")
    road: Road
    vehicles: list[Vehicle] = Field(min_length=1)
    movements: list[Movement] = Field(default_factory=list)
    impacts: list[Impact] = Field(default_factory=list)
    sequence: list[SequenceEvent] = Field(default_factory=list)
    confidence_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_cross_references(self) -> SceneGraph:
        ids = [v.id for v in self.vehicles]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate vehicle ids")
        known = set(ids)
        for movement in self.movements:
            if movement.vehicle_id not in known:
                raise ValueError(f"movement references unknown vehicle {movement.vehicle_id!r}")
        moved = [m.vehicle_id for m in self.movements]
        if len(set(moved)) != len(moved):
            raise ValueError("multiple movements for the same vehicle")
        for impact in self.impacts:
            if impact.vehicle_id not in known:
                raise ValueError(f"impact references unknown vehicle {impact.vehicle_id!r}")
        return self


# ── JSON round-trip helpers (single serialization convention) ────────────────
def scene_to_json(scene: SceneGraph, *, indent: int | None = 2) -> str:
    """Serialize with the wire name ``schema`` (alias) — the canonical form."""
    return scene.model_dump_json(indent=indent, by_alias=True)


def scene_from_json(payload: str | bytes) -> SceneGraph:
    return SceneGraph.model_validate_json(payload)


def semantic_warnings(scene: SceneGraph) -> list[str]:
    """Non-fatal template/vocabulary consistency notes.

    These do not reject the scene (the vocabulary already did the hard
    gating); they surface soft inconsistencies for the confidence notes.
    """
    warnings: list[str] = []
    if scene.road.layout is RoadLayout.t_intersection:
        for m in scene.movements:
            if m.approach is Approach.N:
                warnings.append(
                    f"vehicle {m.vehicle_id!r} approaches from N but the "
                    "t_intersection template has no north arm"
                )
    if scene.road.layout is RoadLayout.parking_lot and scene.road.signal is not Signal.none:
        warnings.append("parking_lot template ignores traffic signals")
    moved = {m.vehicle_id for m in scene.movements}
    for v in scene.vehicles:
        if v.id not in moved:
            warnings.append(f"vehicle {v.id!r} has no movement; treated as parked")
    return warnings
