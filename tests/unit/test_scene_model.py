"""The constrained vocabulary is the anti-hallucination gate — prove it bites."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from claimscene.scene import (
    DamageZone,
    Impact,
    Movement,
    Road,
    SceneGraph,
    Vehicle,
    scene_from_json,
    scene_to_json,
    semantic_warnings,
)


def _minimal_scene(**overrides) -> SceneGraph:
    payload = {
        "road": Road(layout="x_intersection", lanes_per_direction=1),
        "vehicles": [Vehicle(id="veh_a", kind="car", color="red")],
    }
    payload.update(overrides)
    return SceneGraph(**payload)


def test_minimal_scene_constructs_with_defaults():
    scene = _minimal_scene()
    assert scene.schema_id == "claimscene/scene/v1"
    assert scene.movements == [] and scene.impacts == []
    assert scene.vehicles[0].damage == []


def test_json_round_trip_is_lossless():
    scene = _minimal_scene(
        movements=[Movement(vehicle_id="veh_a", approach="N", maneuver="left_turn",
                            speed_band="low")],
        impacts=[Impact(vehicle_id="veh_a", clock_position=2)],
        sequence=["vehicles_approach", "impact"],
        confidence_notes=["test note"],
    )
    payload = scene_to_json(scene)
    assert '"schema":' in payload.replace(" ", "").replace("\n", "")
    restored = scene_from_json(payload)
    assert restored.model_dump() == scene.model_dump()


@pytest.mark.parametrize("clock", [0, 13, -1])
def test_clock_position_out_of_range_rejected(clock):
    with pytest.raises(ValidationError):
        DamageZone(clock_position=clock, severity="dent")
    with pytest.raises(ValidationError):
        Impact(vehicle_id="veh_a", clock_position=clock)


def test_unknown_enum_value_rejected():
    with pytest.raises(ValidationError):
        Vehicle(id="v", kind="spaceship", color="red")
    with pytest.raises(ValidationError):
        Road(layout="motorway", lanes_per_direction=1)


def test_hallucinated_extra_field_rejected():
    with pytest.raises(ValidationError):
        SceneGraph(
            road=Road(layout="straight", lanes_per_direction=1),
            vehicles=[Vehicle(id="v", kind="car", color="red")],
            gps_coordinates=[38.0, 23.7],  # free-form spatial output: forbidden
        )
    with pytest.raises(ValidationError):
        Vehicle(id="v", kind="car", color="red", position_xy=[1.0, 2.0])


def test_free_form_coordinates_have_no_representation():
    payload = scene_to_json(_minimal_scene())
    for banned in ('"x":', '"y":', "coordinate", "latitude", "longitude", "meters"):
        assert banned not in payload


def test_movement_referencing_unknown_vehicle_rejected():
    with pytest.raises(ValidationError, match="unknown vehicle"):
        _minimal_scene(movements=[
            Movement(vehicle_id="ghost", approach="N", maneuver="straight",
                     speed_band="low")])


def test_impact_referencing_unknown_vehicle_rejected():
    with pytest.raises(ValidationError, match="unknown vehicle"):
        _minimal_scene(impacts=[Impact(vehicle_id="ghost", clock_position=6)])


def test_duplicate_vehicle_ids_rejected():
    with pytest.raises(ValidationError, match="duplicate"):
        _minimal_scene(vehicles=[
            Vehicle(id="veh_a", kind="car", color="red"),
            Vehicle(id="veh_a", kind="van", color="blue"),
        ])


def test_multiple_movements_per_vehicle_rejected():
    move = {"vehicle_id": "veh_a", "approach": "N", "maneuver": "straight",
            "speed_band": "low"}
    with pytest.raises(ValidationError, match="multiple movements"):
        _minimal_scene(movements=[Movement(**move), Movement(**move)])


@pytest.mark.parametrize("lanes", [0, 4])
def test_lanes_per_direction_bounds(lanes):
    with pytest.raises(ValidationError):
        Road(layout="straight", lanes_per_direction=lanes)


def test_wrong_schema_string_rejected():
    with pytest.raises(ValidationError):
        scene_from_json(
            '{"schema": "claimscene/scene/v999", "road": {"layout": "straight", '
            '"lanes_per_direction": 1, "signal": "none"}, "vehicles": '
            '[{"id": "v", "kind": "car", "color": "red", "damage": []}]}'
        )


def test_optional_fields_default_none():
    dz = DamageZone(clock_position=6, severity="dent")
    assert dz.note is None


def test_semantic_warnings_soft_flags():
    scene = SceneGraph(
        road=Road(layout="t_intersection", lanes_per_direction=1),
        vehicles=[Vehicle(id="veh_a", kind="car", color="red"),
                  Vehicle(id="veh_b", kind="car", color="blue")],
        movements=[Movement(vehicle_id="veh_a", approach="N", maneuver="straight",
                            speed_band="low")],
    )
    warnings = semantic_warnings(scene)
    assert any("no north arm" in w for w in warnings)
    assert any("veh_b" in w and "parked" in w for w in warnings)
