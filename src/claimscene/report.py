"""Deterministic incident report text (template-based for the foundation).

Every sentence is derived from the constrained vocabulary. No LLM narrative
yet — a later phase may add one, clearly labelled, on top of this factual
layer. The report always carries the disclosure line.
"""
from __future__ import annotations

import math

from .layout import TimedPose, Timeline, clock_point_world
from .provenance import DISCLOSURE
from .scene import SceneGraph, Signal

_CLOCK_WORDS = {
    1: "1 o'clock", 2: "2 o'clock", 3: "3 o'clock (right side)", 4: "4 o'clock",
    5: "5 o'clock", 6: "6 o'clock (rear)", 7: "7 o'clock", 8: "8 o'clock",
    9: "9 o'clock (left side)", 10: "10 o'clock", 11: "11 o'clock",
    12: "12 o'clock (front)",
}
_SIGNAL_WORDS = {
    Signal.none: "no traffic control",
    Signal.stop_sign: "a stop sign",
    Signal.traffic_light: "a traffic light",
    Signal.yield_sign: "a yield sign",
}
_LAYOUT_WORDS = {
    "straight": "a straight road section",
    "t_intersection": "a T-intersection",
    "x_intersection": "a four-way intersection",
    "roundabout": "a roundabout",
    "parking_lot": "a parking lot",
}
_MANEUVER_WORDS = {
    "straight": "proceeding straight",
    "left_turn": "turning left",
    "right_turn": "turning right",
    "u_turn": "making a U-turn",
    "lane_change": "changing lanes",
    "reversing": "reversing",
    "parked": "parked",
}
_SPEED_WORDS = {
    "stopped": "stopped",
    "low": "at low speed",
    "moderate": "at moderate speed",
    "high": "at high speed",
}


def build_report(scene: SceneGraph, timeline: Timeline, *, case_id: str,
                 extra_notes: list[str] | None = None) -> str:
    road = scene.road
    lines: list[str] = []
    lines.append(f"# Incident report — case `{case_id}`")
    lines.append("")
    lines.append(f"> **{DISCLOSURE}.** This report and the schematic are the")
    lines.append("> factual layer: derived only from the constrained scene")
    lines.append("> vocabulary extracted from the submitted photos. The cinematic")
    lines.append("> clip is an AI illustration and is sealed as such.")
    lines.append("")
    lines.append("## Location")
    lines.append("")
    lines.append(
        f"The incident occurred at {_LAYOUT_WORDS[road.layout.value]} with "
        f"{road.lanes_per_direction} lane(s) per direction and "
        f"{_SIGNAL_WORDS[road.signal]}."
    )
    lines.append("")
    lines.append("## Parties")
    lines.append("")
    movements = {m.vehicle_id: m for m in scene.movements}
    for v in scene.vehicles:
        m = movements.get(v.id)
        if m is None:
            action = "parked (no reported movement)"
        elif m.maneuver.value == "parked":
            action = "parked"
        else:
            action = (f"approaching from {m.approach.value}, "
                      f"{_MANEUVER_WORDS[m.maneuver.value]}, "
                      f"{_SPEED_WORDS[m.speed_band.value]}")
        lines.append(f"- **{v.id}** — {v.color.value} {v.kind.value}, {action}.")
    lines.append("")
    if scene.impacts:
        lines.append("## Impact")
        lines.append("")
        for imp in scene.impacts:
            lines.append(
                f"- **{imp.vehicle_id}** was struck at "
                f"{_CLOCK_WORDS[imp.clock_position]}."
            )
        if timeline.contact_point is not None:
            cx, cy = timeline.contact_point
            lines.append("")
            lines.append(
                f"The reconstructed contact point lies at ({cx:.1f} m, {cy:.1f} m) "
                "relative to the junction center (schematic frame)."
            )
        lines.append("")
    damage_rows = [(v.id, dz) for v in scene.vehicles for dz in v.damage]
    if damage_rows:
        lines.append("## Reported damage")
        lines.append("")
        for vid, dz in damage_rows:
            note = f" — {dz.note}" if dz.note else ""
            lines.append(
                f"- **{vid}**: {dz.severity.value} at "
                f"{_CLOCK_WORDS[dz.clock_position]}{note}."
            )
        lines.append("")
    if scene.sequence:
        lines.append("## Sequence of events")
        lines.append("")
        for ev in timeline.events:
            lines.append(f"- t=+{ev.t:.2f}s — {ev.label.replace('_', ' ')}")
        lines.append("")
    notes = list(scene.confidence_notes) + list(timeline.notes) + list(extra_notes or [])
    if notes:
        lines.append("## Confidence notes")
        lines.append("")
        for note in notes:
            lines.append(f"- {note}")
        lines.append("")
    lines.append("---")
    lines.append(f"_{DISCLOSURE}._")
    lines.append("")
    return "\n".join(lines)


# ── impact-geometry relational phrasing ──────────────────────────────────────
# Where one impact participant sits relative to another, and how they are
# angled toward each other, derived purely from the Timeline's already
# computed poses at the impact frame (never by re-reading the movement or
# damage vocabulary text) -- the same factual layer the schematic is drawn
# from, so the wording is consistent with it by construction. See
# ``_impact_relationship_phrase`` for the orchestration.
_POSITION_BUCKETS: tuple[tuple[float, float, str], ...] = (
    (-157.5, -112.5, "behind and to the right of"),
    (-112.5, -67.5, "directly to the right of"),
    (-67.5, -22.5, "ahead and to the right of"),
    (-22.5, 22.5, "directly ahead of"),
    (22.5, 67.5, "ahead and to the left of"),
    (67.5, 112.5, "directly to the left of"),
    (112.5, 157.5, "behind and to the left of"),
)
_POSITION_BEHIND = "directly behind"  # the wrap-around bucket past +/-157.5

_ALIGNED_MAX_DEG = 25.0  # heading difference at or below this: "same way"
_RIGHT_ANGLE_MIN_DEG = 65.0
_RIGHT_ANGLE_MAX_DEG = 115.0
_HEAD_ON_MIN_DEG = 155.0  # heading difference at or above this: head-on

_CONTACT_MAX_M = 1.0  # own-clock-point gap at/below this: "in contact"
_ALMOST_TOUCHING_MAX_M = 3.0  # gap at/below this (but over contact): "almost touching"


def _normalize_deg(deg: float) -> float:
    """Normalize an angle to (-180, 180]."""
    return ((deg + 180.0) % 360.0) - 180.0


def _impact_pose(timeline: Timeline, vehicle_id: str) -> TimedPose | None:
    """``vehicle_id``'s pose at the timeline's impact frame.

    ``None`` only when ``timeline`` has no track for this vehicle -- i.e. it
    was not built from the same scene. Defensive rather than raising, so a
    caller mismatch degrades to "no relationship claimed" instead of a
    crash.
    """
    track = next((t for t in timeline.tracks if t.vehicle_id == vehicle_id), None)
    if track is None or not track.poses:
        return None
    return min(track.poses, key=lambda p: abs(p.t - timeline.impact_time_s))


def _bearing_and_heading_diff(a_pose: TimedPose, b_pose: TimedPose) -> tuple[float, float]:
    """(bearing of B in A's own frame, A/B heading difference), in degrees.

    Both poses use the Timeline's world-frame heading convention (0 = east,
    counter-clockwise). A relative bearing of 0 means B sits directly ahead
    of A, in the direction A's nose points; 180 means directly behind;
    +90/-90 means to A's left/right. The heading difference is 0 when A and
    B face the same way and +/-180 when they face directly opposite each
    other.
    """
    dx, dy = b_pose.x - a_pose.x, b_pose.y - a_pose.y
    bearing_world = math.degrees(math.atan2(dy, dx))
    rel_bearing = _normalize_deg(bearing_world - a_pose.heading_deg)
    heading_diff = _normalize_deg(b_pose.heading_deg - a_pose.heading_deg)
    return rel_bearing, heading_diff


def _position_phrase(rel_bearing_deg: float) -> str:
    for lo, hi, phrase in _POSITION_BUCKETS:
        if lo < rel_bearing_deg <= hi:
            return phrase
    return _POSITION_BEHIND


def _orientation_phrase(heading_diff_deg: float) -> str:
    angle = abs(heading_diff_deg)
    if angle <= _ALIGNED_MAX_DEG:
        return "both facing the same way"
    if angle >= _HEAD_ON_MIN_DEG:
        return "facing each other head-on"
    if _RIGHT_ANGLE_MIN_DEG <= angle <= _RIGHT_ANGLE_MAX_DEG:
        return "meeting at right angles"
    return "meeting at an oblique angle"


def _idiom_phrase(position: str, orientation: str) -> str | None:
    """A short, standard collision idiom for the two configurations it
    unambiguously fits -- aligned nose-to-tail or nose-to-nose -- and
    nothing for any other configuration (no idiom is stretched to fit a
    shape it was not built for)."""
    if position not in ("directly ahead of", _POSITION_BEHIND):
        return None
    if orientation == "both facing the same way":
        return "nose to tail"
    if orientation == "facing each other head-on":
        return "nose to nose"
    return None


def _impact_point_distance(
    timeline: Timeline, a_id: str, a_pose: TimedPose, b_id: str, b_pose: TimedPose
) -> float | None:
    """Distance between the two vehicles' own stated impact points.

    The LayoutEngine positions every impact participant so its stated
    clock-position point touches the shared contact point at the impact
    frame (see ``layout.LayoutEngine.build``), so this comes out near zero
    by construction -- computed here rather than assumed, so the wording
    stays honestly derived from the poses rather than from that invariant.
    ``None`` when either vehicle is missing impact metadata (a
    scene/timeline mismatch), never raised.
    """
    a_meta = next((v for v in timeline.vehicles if v.id == a_id), None)
    b_meta = next((v for v in timeline.vehicles if v.id == b_id), None)
    if a_meta is None or b_meta is None:
        return None
    if a_meta.impact_clock is None or b_meta.impact_clock is None:
        return None
    ax, ay = clock_point_world(a_pose, a_meta.impact_clock, a_meta.length_m, a_meta.width_m)
    bx, by = clock_point_world(b_pose, b_meta.impact_clock, b_meta.length_m, b_meta.width_m)
    return math.hypot(bx - ax, by - ay)


def _contact_phrase(distance_m: float | None) -> str | None:
    if distance_m is None:
        return None
    if distance_m <= _CONTACT_MAX_M:
        return "in contact at the point of impact"
    if distance_m <= _ALMOST_TOUCHING_MAX_M:
        return "almost touching"
    return None


def _vehicle_descriptions(scene: SceneGraph) -> dict[str, str]:
    return {v.id: f"{v.color.value} {v.kind.value}" for v in scene.vehicles}


def _unique_impact_vehicle_ids(scene: SceneGraph) -> list[str]:
    """Impact participants in first-seen order, deduplicated by vehicle id."""
    seen: set[str] = set()
    ids: list[str] = []
    for imp in scene.impacts:
        if imp.vehicle_id in seen:
            continue
        seen.add(imp.vehicle_id)
        ids.append(imp.vehicle_id)
    return ids


def _impact_relationship_phrase(scene: SceneGraph, timeline: Timeline) -> str:
    """Plain-language description of how the impact participants are
    positioned and oriented relative to one another at the moment of
    impact -- the fact the independent per-vehicle description never
    states, which is exactly what let a rear-end collision render as two
    cars waiting side by side at a light: nothing in the old prompt said
    which vehicle was where relative to the other.

    Every other impact participant is described relative to the
    first-listed one, which keeps the output well-defined for the common
    two-vehicle case and still well-defined (if more verbose) for more
    participants.

    Returns an empty string when there is no pair to relate: a
    single-vehicle scene, a parked-only scene with no recorded contact, or
    a scene with no impacts at all. Callers must treat that as "nothing to
    add", not an error.
    """
    ids = _unique_impact_vehicle_ids(scene)
    if len(ids) < 2:
        return ""
    descriptions = _vehicle_descriptions(scene)
    anchor_id = ids[0]
    anchor_pose = _impact_pose(timeline, anchor_id)
    if anchor_pose is None:
        return ""
    sentences: list[str] = []
    for other_id in ids[1:]:
        other_pose = _impact_pose(timeline, other_id)
        if other_pose is None:
            continue
        rel_bearing, heading_diff = _bearing_and_heading_diff(anchor_pose, other_pose)
        position = _position_phrase(rel_bearing)
        orientation = _orientation_phrase(heading_diff)
        clauses: list[str] = []
        idiom = _idiom_phrase(position, orientation)
        if idiom:
            clauses.append(idiom)
        clauses.append(orientation)
        distance = _impact_point_distance(timeline, anchor_id, anchor_pose,
                                          other_id, other_pose)
        contact = _contact_phrase(distance)
        if contact:
            clauses.append(contact)
        sentences.append(
            f"The {descriptions[other_id]} is {position} the "
            f"{descriptions[anchor_id]}, " + ", ".join(clauses) + "."
        )
    return " ".join(sentences)


def _forensic_scene_description(scene: SceneGraph, timeline: Timeline) -> str:
    """The shared forensic-reconstruction scene wording (still + clip prompts).

    Deterministic, built only from the constrained vocabulary plus the
    Timeline's already-computed geometry, and kept in the computer-generated
    forensic-reconstruction register on purpose: a clean, serious CGI
    accident-reconstruction render, not a toy and not a cartoon, that states
    plainly it is a computer-generated reconstruction and not a real
    recording (self-disclosing). It depicts no people and no injuries, which
    keeps it clear of the sharper content-moderation triggers by
    construction, though unlike the retired toy-diorama register this exact
    wording has not yet been probed against a live provider.

    The impact participants' relative position and orientation (e.g. "the
    red car is directly behind the blue car, nose to tail") come from
    ``_impact_relationship_phrase``, so the prompt states the scene's actual
    layout instead of leaving it for the model to invent.
    """
    road = scene.road
    movements = {m.vehicle_id: m for m in scene.movements}
    parts: list[str] = []
    for v in scene.vehicles:
        m = movements.get(v.id)
        if m is None or m.maneuver.value == "parked":
            phrase = f"a parked {v.color.value} {v.kind.value}"
        else:
            phrase = (
                f"a {v.color.value} {v.kind.value} that arrived from the "
                f"{m.approach.value}, {_MANEUVER_WORDS[m.maneuver.value]} "
                f"{_SPEED_WORDS[m.speed_band.value]}"
            )
        for dz in v.damage:
            phrase += (
                f", with a {dz.severity.value} mark at its "
                f"{_CLOCK_WORDS[dz.clock_position]}"
            )
        parts.append(phrase)
    relationship = _impact_relationship_phrase(scene, timeline)
    relationship_sentence = f" {relationship}" if relationship else ""
    return (
        "Computer-generated 3D forensic accident-reconstruction render, not "
        "a real recording, showing "
        f"{_LAYOUT_WORDS[scene.road.layout.value]} with "
        f"{_SIGNAL_WORDS[road.signal]}: " + "; ".join(parts) + "."
        + relationship_sentence +
        " Accurate vehicle proportions, real road surface and lane markings, "
        "neutral daylight, professional CGI clarity, not a toy, not a "
        "cartoon, no people, no injuries. The rendered image itself must "
        "contain no text, no labels, no captions, and no watermarks."
    )


def illustration_still_prompt(scene: SceneGraph, timeline: Timeline) -> str:
    """Deterministic prompt for the establish-shot still (text → image)."""
    return (
        "Clean, serious forensic accident-reconstruction still, "
        "establish-shot framing. "
        + _forensic_scene_description(scene, timeline)
        + " Slightly elevated three-quarter view of the whole scene."
    )


def illustration_prompt(scene: SceneGraph, timeline: Timeline) -> str:
    """Deterministic prompt for the illustration clip (still → video).

    Built only from the constrained vocabulary plus the Timeline's computed
    geometry. This used to end with an ``Overlay text: '{DISCLOSURE}'``
    request; that line is gone. A live render on 2026-07-30 (case
    ``live-forensic-retry-0bb32eeb``) proved the model is free to ignore
    such a request, and asking for that text overlay would now directly
    contradict the "no text" instruction inside
    ``_forensic_scene_description`` above -- a contradictory prompt is
    worse than no prompt. The disclosure is guaranteed instead by
    ``watermark.burn_clip_watermark``, deterministically, after generation.
    """
    return (
        "Slow smooth camera orbit around this forensic "
        "accident-reconstruction scene. "
        + _forensic_scene_description(scene, timeline)
        + " The vehicles stay perfectly still; gentle parallax only."
    )
