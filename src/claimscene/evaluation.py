"""Extraction-accuracy scoring: predicted SceneGraph vs ground truth.

The eval set (``eval/scenarios/``) is *synthetic and self-consistent by
construction*: each scenario's photos were generated from a scene we
authored, and the ground truth is that same scene in SceneGraph form. The
scorer compares field-by-field inside the constrained vocabulary:

  field           rule                                   weight (per unit)
  --------------  -------------------------------------  -----------------
  road_layout     exact enum match                       2 per scenario
  signal          exact enum match                       1 per scenario
  vehicle_count   exact count match                      2 per scenario
  vehicle_kind    exact, per matched vehicle             1 per truth vehicle
  vehicle_color   exact, per matched vehicle             1 per truth vehicle
  damage_clock    within +/-1 clock hour (circular)      1 per truth vehicle with damage
  approach        exact, per matched vehicle w/ movement 1
  maneuver        exact, per matched vehicle w/ movement 1
  impact_clock    within +/-1 clock hour (circular)      1 per truth vehicle with impact

Predicted vehicles are aligned to truth vehicles by greedy best match on
(kind, color, damage clock) — vehicle ids are labels, not identities, so an
extractor that calls the same car ``veh_1`` instead of ``veh_a`` is not
punished. Unmatched truth vehicles score zero on their per-vehicle fields.
The overall score is earned/possible weighted points across all scenarios.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .scene import SceneGraph

FIELD_WEIGHTS = {
    "road_layout": 2,
    "signal": 1,
    "vehicle_count": 2,
    "vehicle_kind": 1,
    "vehicle_color": 1,
    "damage_clock": 1,
    "approach": 1,
    "maneuver": 1,
    "impact_clock": 1,
}

#: Clock positions are circular: |12 - 1| is 1 hour, not 11.
CLOCK_TOLERANCE = 1


def clock_within(a: int, b: int, tolerance: int = CLOCK_TOLERANCE) -> bool:
    diff = abs(a - b) % 12
    return min(diff, 12 - diff) <= tolerance


@dataclass
class FieldTally:
    hits: int = 0
    total: int = 0

    def add(self, hit: bool) -> bool:
        self.total += 1
        if hit:
            self.hits += 1
        return hit


@dataclass
class ScenarioScore:
    scenario_id: str
    earned: float = 0.0
    possible: float = 0.0
    fields: dict[str, FieldTally] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def pct(self) -> float:
        return round(100.0 * self.earned / self.possible, 1) if self.possible else 0.0

    def score(self, field_name: str, hit: bool) -> None:
        tally = self.fields.setdefault(field_name, FieldTally())
        tally.add(hit)
        weight = FIELD_WEIGHTS[field_name]
        self.possible += weight
        if hit:
            self.earned += weight
        else:
            self.notes.append(f"miss: {field_name}")


def _vehicle_similarity(truth_vehicle, pred_vehicle) -> int:
    """Alignment heuristic only (never contributes to the score itself)."""
    score = 0
    if truth_vehicle.kind == pred_vehicle.kind:
        score += 2
    if truth_vehicle.color == pred_vehicle.color:
        score += 2
    truth_clocks = {d.clock_position for d in truth_vehicle.damage}
    pred_clocks = {d.clock_position for d in pred_vehicle.damage}
    if truth_clocks and pred_clocks and any(
        clock_within(t, p) for t in truth_clocks for p in pred_clocks
    ):
        score += 1
    return score


def match_vehicles(truth: SceneGraph, pred: SceneGraph) -> list[tuple]:
    """Greedy best-similarity alignment truth-vehicle → predicted-vehicle."""
    pairs: list[tuple] = []
    remaining = list(pred.vehicles)
    for tv in truth.vehicles:
        if not remaining:
            pairs.append((tv, None))
            continue
        best = max(remaining, key=lambda pv: _vehicle_similarity(tv, pv))
        remaining.remove(best)
        pairs.append((tv, best))
    return pairs


def score_scene(scenario_id: str, truth: SceneGraph,
                pred: SceneGraph | None) -> ScenarioScore:
    """Score one scenario. ``pred=None`` (extraction failed) scores zero."""
    result = ScenarioScore(scenario_id=scenario_id)

    truth_movements = {m.vehicle_id: m for m in truth.movements}
    truth_impacts = {i.vehicle_id: i for i in truth.impacts}

    if pred is None:
        result.notes.append("extraction failed — all fields scored as misses")

    result.score("road_layout",
                 pred is not None and pred.road.layout == truth.road.layout)
    result.score("signal",
                 pred is not None and pred.road.signal == truth.road.signal)
    result.score("vehicle_count",
                 pred is not None and len(pred.vehicles) == len(truth.vehicles))

    pairs = match_vehicles(truth, pred) if pred is not None else [
        (tv, None) for tv in truth.vehicles
    ]
    pred_movements = {m.vehicle_id: m for m in pred.movements} if pred else {}
    pred_impacts = {i.vehicle_id: i for i in pred.impacts} if pred else {}

    for tv, pv in pairs:
        result.score("vehicle_kind", pv is not None and pv.kind == tv.kind)
        result.score("vehicle_color", pv is not None and pv.color == tv.color)

        if tv.damage:
            hit = pv is not None and any(
                clock_within(td.clock_position, pd.clock_position)
                for td in tv.damage for pd in pv.damage
            )
            result.score("damage_clock", hit)

        tm = truth_movements.get(tv.id)
        if tm is not None:
            pm = pred_movements.get(pv.id) if pv is not None else None
            result.score("approach", pm is not None and pm.approach == tm.approach)
            result.score("maneuver", pm is not None and pm.maneuver == tm.maneuver)

        ti = truth_impacts.get(tv.id)
        if ti is not None:
            pi = pred_impacts.get(pv.id) if pv is not None else None
            result.score("impact_clock", pi is not None and clock_within(
                ti.clock_position, pi.clock_position))

    return result


def aggregate(scores: list[ScenarioScore]) -> dict:
    """Roll scenario scores up into overall + per-field accuracy."""
    per_field: dict[str, FieldTally] = {}
    earned = possible = 0.0
    for s in scores:
        earned += s.earned
        possible += s.possible
        for name, tally in s.fields.items():
            agg = per_field.setdefault(name, FieldTally())
            agg.hits += tally.hits
            agg.total += tally.total
    return {
        "overall_pct": round(100.0 * earned / possible, 1) if possible else 0.0,
        "earned": earned,
        "possible": possible,
        "per_field": {
            name: {
                "hits": tally.hits,
                "total": tally.total,
                "pct": round(100.0 * tally.hits / tally.total, 1)
                if tally.total else 0.0,
                "weight": FIELD_WEIGHTS[name],
            }
            for name, tally in sorted(per_field.items())
        },
        "scenarios": [
            {"id": s.scenario_id, "pct": s.pct, "earned": s.earned,
             "possible": s.possible, "notes": s.notes}
            for s in scores
        ],
    }
