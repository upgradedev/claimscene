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

#: Min ``_vehicle_similarity`` to treat an id-mismatch as a rename rather than
#: an add + remove — kind (+2) AND color (+2) must both match.
_RENAME_SIMILARITY = 4


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


#: The review-diff field set, in the deterministic order the seal hashes them.
#: Per-vehicle rows follow :func:`align_review_vehicles` order; the road rows
#: always come first. Order is load-bearing — it is part of ``decision_digest``.


def _movement_for(scene: SceneGraph, vehicle_id: str):
    return next((m for m in scene.movements if m.vehicle_id == vehicle_id), None)


def _impact_for(scene: SceneGraph, vehicle_id: str):
    return next((i for i in scene.impacts if i.vehicle_id == vehicle_id), None)


def _damage_clocks(vehicle) -> list[int] | None:
    """Sorted clock positions of a vehicle's damage zones (``None`` if absent)."""
    if vehicle is None:
        return None
    return sorted(d.clock_position for d in vehicle.damage)


def align_review_vehicles(proposed: SceneGraph, confirmed: SceneGraph) -> list[tuple]:
    """Pair proposed→confirmed vehicles for a review diff.

    Vehicles are matched by id first — the review UI edits the scene in place,
    so ids are stable across the AI proposal and the human confirmation. Any
    proposed vehicle whose id vanished is matched to the most similar leftover
    confirmed vehicle (a rename), reusing the eval ``_vehicle_similarity``
    primitive. Confirmed vehicles never matched are human-*added* (proposed
    side ``None``); proposed vehicles never matched are human-*removed*
    (confirmed side ``None``). Never yields ``(None, None)``.
    """
    confirmed_by_id = {v.id: v for v in confirmed.vehicles}
    matched_confirmed: set[str] = set()
    pairs: list[tuple] = []

    pending = []
    for pv in proposed.vehicles:
        cv = confirmed_by_id.get(pv.id)
        if cv is not None:
            pairs.append((pv, cv))
            matched_confirmed.add(cv.id)
        else:
            pending.append(pv)

    leftover = [v for v in confirmed.vehicles if v.id not in matched_confirmed]
    for pv in pending:
        best = max(leftover, key=lambda cv: _vehicle_similarity(pv, cv), default=None)
        # Only treat an id-mismatch as a *rename* when kind AND color both match
        # (a confident same-vehicle signal); otherwise it is a genuine
        # add + remove, not a rename — so the diff stays honest.
        if best is not None and _vehicle_similarity(pv, best) >= _RENAME_SIMILARITY:
            leftover.remove(best)
            pairs.append((pv, best))
        else:
            pairs.append((pv, None))  # human-removed

    for cv in leftover:
        pairs.append((None, cv))  # human-added

    return pairs


def diff_scenes(proposed: SceneGraph, confirmed: SceneGraph) -> list[dict]:
    """Field-level diff of an AI-proposed scene vs the human-confirmed scene.

    Emits one row per constrained field — road (layout/lanes/signal), then per
    aligned vehicle its kind/color/damage-clocks, movement (approach/maneuver/
    speed) and impact clock. Every ``proposed``/``confirmed`` value is a plain
    JSON scalar (enum ``.value``, int, sorted-int list, or ``None`` for an
    added/removed side) so the diff seals cleanly through ``canonical_json``.
    ``changed_by`` is ``"human"`` when the human altered the field, else
    ``"unchanged"`` (the only actor between proposal and confirmation is the
    reviewer). Row order is deterministic and part of the sealed digest.
    """
    rows: list[dict] = []

    def add(path: str, prop, conf) -> None:
        changed = prop != conf
        rows.append({
            "path": path,
            "proposed": prop,
            "confirmed": conf,
            "changed": changed,
            "changed_by": "human" if changed else "unchanged",
        })

    add("road.layout", proposed.road.layout.value, confirmed.road.layout.value)
    add("road.lanes_per_direction",
        proposed.road.lanes_per_direction, confirmed.road.lanes_per_direction)
    add("road.signal", proposed.road.signal.value, confirmed.road.signal.value)

    for pv, cv in align_review_vehicles(proposed, confirmed):
        vid = (cv or pv).id
        add(f"vehicles.{vid}.kind",
            None if pv is None else pv.kind.value,
            None if cv is None else cv.kind.value)
        add(f"vehicles.{vid}.color",
            None if pv is None else pv.color.value,
            None if cv is None else cv.color.value)
        add(f"vehicles.{vid}.damage_clocks", _damage_clocks(pv), _damage_clocks(cv))

        pm = _movement_for(proposed, pv.id) if pv is not None else None
        cm = _movement_for(confirmed, cv.id) if cv is not None else None
        add(f"movements.{vid}.approach",
            None if pm is None else pm.approach.value,
            None if cm is None else cm.approach.value)
        add(f"movements.{vid}.maneuver",
            None if pm is None else pm.maneuver.value,
            None if cm is None else cm.maneuver.value)
        add(f"movements.{vid}.speed_band",
            None if pm is None else pm.speed_band.value,
            None if cm is None else cm.speed_band.value)

        pi = _impact_for(proposed, pv.id) if pv is not None else None
        ci = _impact_for(confirmed, cv.id) if cv is not None else None
        add(f"impacts.{vid}.clock_position",
            None if pi is None else pi.clock_position,
            None if ci is None else ci.clock_position)

    return rows


def review_counts(diff: list[dict]) -> dict:
    """Honesty tally over a :func:`diff_scenes` result.

    ``human_changed + unchanged == proposed_fields`` by construction, so the
    UI's "N proposed · M corrected · K unchanged" line is always consistent.
    """
    changed = sum(1 for row in diff if row["changed"])
    return {
        "proposed_fields": len(diff),
        "human_changed": changed,
        "unchanged": len(diff) - changed,
    }


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
