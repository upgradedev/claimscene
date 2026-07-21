"""Extraction-accuracy scorer: field rules, vehicle alignment, aggregation."""
from __future__ import annotations

from claimscene.adapters.fakes import _scene_left_cross, _scene_rear_end
from claimscene.evaluation import (
    FIELD_WEIGHTS,
    aggregate,
    clock_within,
    match_vehicles,
    score_scene,
)
from claimscene.scene import SceneGraph


def _mutated(scene: SceneGraph, **overrides) -> SceneGraph:
    payload = scene.model_dump(by_alias=True)
    payload.update(overrides)
    return SceneGraph.model_validate(payload)


# ── clock arithmetic ─────────────────────────────────────────────────────────
def test_clock_within_is_circular():
    assert clock_within(12, 1)      # one hour across midnight
    assert clock_within(1, 12)
    assert clock_within(6, 7)
    assert not clock_within(12, 2)  # two hours
    assert not clock_within(3, 9)   # opposite sides


# ── perfect / failed extraction ──────────────────────────────────────────────
def test_perfect_prediction_scores_100():
    truth = _scene_rear_end()
    score = score_scene("s", truth, _scene_rear_end())
    assert score.pct == 100.0
    assert score.earned == score.possible > 0


def test_failed_extraction_scores_zero_but_counts_all_fields():
    truth = _scene_rear_end()
    score = score_scene("s", truth, None)
    assert score.earned == 0.0
    assert score.pct == 0.0
    # Every scoreable field is still counted in the denominator.
    assert score.fields["road_layout"].total == 1
    assert score.fields["vehicle_kind"].total == len(truth.vehicles)
    assert "extraction failed — all fields scored as misses" in score.notes


# ── vehicle alignment ────────────────────────────────────────────────────────
def test_vehicle_ids_are_labels_not_identities():
    truth = _scene_rear_end()
    pred = _scene_rear_end()
    payload = pred.model_dump(by_alias=True)
    # Rename + reorder the vehicles; movements/impacts follow the new names.
    for section in ("vehicles", "movements", "impacts"):
        for row in payload[section]:
            key = "id" if section == "vehicles" else "vehicle_id"
            row[key] = {"veh_a": "car_1", "veh_b": "car_2"}[row[key]]
    payload["vehicles"].reverse()
    pred = SceneGraph.model_validate(payload)
    assert score_scene("s", truth, pred).pct == 100.0


def test_match_vehicles_pairs_by_kind_and_color():
    truth = _scene_left_cross()   # silver car + green van
    pred = _scene_left_cross()
    pairs = match_vehicles(truth, pred)
    assert [(t.id, p.id) for t, p in pairs] == [("veh_a", "veh_a"),
                                                ("veh_b", "veh_b")]


def test_missing_predicted_vehicle_zeroes_its_fields():
    truth = _scene_rear_end()
    only_one = _mutated(
        truth,
        vehicles=[truth.vehicles[0].model_dump()],
        movements=[truth.movements[0].model_dump()],
        impacts=[truth.impacts[0].model_dump()],
    )
    score = score_scene("s", truth, only_one)
    assert score.fields["vehicle_count"].hits == 0
    assert score.fields["vehicle_kind"].hits == 1  # the matched one still scores
    assert 0 < score.pct < 100


# ── per-field rules ──────────────────────────────────────────────────────────
def test_wrong_color_costs_exactly_the_color_weight():
    truth = _scene_rear_end()
    payload = truth.model_dump(by_alias=True)
    payload["vehicles"][0]["color"] = "black"  # truth says blue
    pred = SceneGraph.model_validate(payload)
    score = score_scene("s", truth, pred)
    assert score.fields["vehicle_color"].hits == 1
    assert score.fields["vehicle_color"].total == 2
    assert score.possible - score.earned == FIELD_WEIGHTS["vehicle_color"]


def test_damage_and_impact_clocks_score_within_tolerance():
    truth = _scene_rear_end()
    payload = truth.model_dump(by_alias=True)
    payload["vehicles"][0]["damage"][0]["clock_position"] = 5   # truth 6 → ±1 ok
    payload["impacts"][0]["clock_position"] = 7                  # truth 6 → ±1 ok
    payload["impacts"][1]["clock_position"] = 3                  # truth 12 → miss
    pred = SceneGraph.model_validate(payload)
    score = score_scene("s", truth, pred)
    assert score.fields["damage_clock"].hits == 2
    assert score.fields["impact_clock"].hits == 1
    assert score.fields["impact_clock"].total == 2


# ── aggregation ──────────────────────────────────────────────────────────────
def test_aggregate_rolls_up_overall_and_per_field():
    truth_a, truth_b = _scene_rear_end(), _scene_left_cross()
    scores = [score_scene("a", truth_a, _scene_rear_end()),
              score_scene("b", truth_b, None)]
    report = aggregate(scores)
    assert 0 < report["overall_pct"] < 100
    assert report["scenarios"][0]["pct"] == 100.0
    assert report["scenarios"][1]["pct"] == 0.0
    layout = report["per_field"]["road_layout"]
    assert layout["hits"] == 1 and layout["total"] == 2
    assert layout["weight"] == FIELD_WEIGHTS["road_layout"]
    # Weighted overall equals earned/possible over both scenarios.
    expected = 100.0 * scores[0].earned / (scores[0].possible + scores[1].possible)
    assert abs(report["overall_pct"] - round(expected, 1)) < 0.11
