"""The server-computed AI-proposed → human-confirmed field diff.

The diff is the evidentiary core of the sealed approval receipt, so it must be
deterministic, JSON-serializable (no raw enum objects — they would crash the
canonical-JSON seal), and honest about what the human changed.
"""
from __future__ import annotations

import json

from claimscene.evaluation import align_review_vehicles, diff_scenes, review_counts
from claimscene.scene import (
    DamageZone,
    Impact,
    Movement,
    Road,
    SceneGraph,
    Vehicle,
    VehicleColor,
)


def _scene(**over) -> SceneGraph:
    base = dict(
        road=Road(layout="x_intersection", lanes_per_direction=1, signal="traffic_light"),
        vehicles=[
            Vehicle(id="veh_a", kind="car", color="silver",
                    damage=[DamageZone(clock_position=2, severity="crush")]),
            Vehicle(id="veh_b", kind="van", color="green"),
        ],
        movements=[
            Movement(vehicle_id="veh_a", approach="N", maneuver="left_turn", speed_band="low"),
            Movement(vehicle_id="veh_b", approach="S", maneuver="straight", speed_band="moderate"),
        ],
        impacts=[Impact(vehicle_id="veh_a", clock_position=2)],
    )
    base.update(over)
    return SceneGraph(**base)


def test_identical_scenes_have_zero_human_changes():
    diff = diff_scenes(_scene(), _scene())
    counts = review_counts(diff)
    assert counts["human_changed"] == 0
    assert counts["unchanged"] == counts["proposed_fields"] == len(diff)
    assert all(r["changed"] is False and r["changed_by"] == "unchanged" for r in diff)


def test_counts_invariant_holds():
    proposed = _scene()
    confirmed = proposed.model_copy(deep=True)
    confirmed.vehicles[0].color = VehicleColor.red
    counts = review_counts(diff_scenes(proposed, confirmed))
    assert counts["human_changed"] + counts["unchanged"] == counts["proposed_fields"]


def test_per_field_human_edits_are_flagged_with_before_after():
    proposed = _scene()
    confirmed = proposed.model_copy(deep=True)
    confirmed.vehicles[0].color = VehicleColor.red                      # recolour veh_a
    confirmed.road = Road(layout="x_intersection", lanes_per_direction=2,
                          signal="traffic_light")                       # widen lanes
    diff = diff_scenes(proposed, confirmed)
    by_path = {r["path"]: r for r in diff}

    colour = by_path["vehicles.veh_a.color"]
    assert colour["proposed"] == "silver" and colour["confirmed"] == "red"
    assert colour["changed"] is True and colour["changed_by"] == "human"

    lanes = by_path["road.lanes_per_direction"]
    assert lanes["proposed"] == 1 and lanes["confirmed"] == 2 and lanes["changed"] is True

    assert by_path["vehicles.veh_a.kind"]["changed"] is False           # untouched
    assert review_counts(diff)["human_changed"] == 2


def test_diff_is_json_serializable_no_raw_enums():
    proposed = _scene()
    confirmed = proposed.model_copy(deep=True)
    confirmed.vehicles[1].damage.append(DamageZone(clock_position=9, severity="scratch"))
    diff = diff_scenes(proposed, confirmed)
    json.dumps(diff)  # a raw VehicleKind / RoadLayout here would raise → seal safety
    damage = next(r for r in diff if r["path"] == "vehicles.veh_b.damage_clocks")
    assert damage["proposed"] == [] and damage["confirmed"] == [9] and damage["changed"] is True


def test_row_order_is_deterministic():
    a = [r["path"] for r in diff_scenes(_scene(), _scene())]
    b = [r["path"] for r in diff_scenes(_scene(), _scene())]
    assert a == b
    assert a[:3] == ["road.layout", "road.lanes_per_direction", "road.signal"]


def test_added_and_removed_vehicles_are_honest_not_renamed():
    proposed = _scene()                                    # veh_a, veh_b
    confirmed = proposed.model_copy(deep=True)
    # Human removes veh_b (a van) and adds veh_c (a truck) — dissimilar, so this
    # is an add + remove, never mislabelled a rename.
    confirmed.vehicles = [confirmed.vehicles[0],
                          Vehicle(id="veh_c", kind="truck", color="white")]
    confirmed.movements = [confirmed.movements[0]]
    diff = diff_scenes(proposed, confirmed)
    by_path = {r["path"]: r for r in diff}

    assert by_path["vehicles.veh_b.kind"]["proposed"] == "van"
    assert by_path["vehicles.veh_b.kind"]["confirmed"] is None          # removed
    assert by_path["vehicles.veh_c.kind"]["proposed"] is None           # added
    assert by_path["vehicles.veh_c.kind"]["confirmed"] == "truck"
    json.dumps(diff)  # None sides serialize cleanly


def test_true_rename_pairs_by_similarity():
    proposed = _scene()
    confirmed = proposed.model_copy(deep=True)
    confirmed.vehicles[1].id = "renamed_b"                 # same van, new id
    confirmed.movements[1].vehicle_id = "renamed_b"
    pairs = align_review_vehicles(proposed, confirmed)
    # veh_b (van/green) still pairs with renamed_b (van/green) — kind+color match.
    matched = {(p.id if p else None, c.id if c else None) for p, c in pairs}
    assert ("veh_b", "renamed_b") in matched
    assert (None, "renamed_b") not in matched          # not treated as an add
