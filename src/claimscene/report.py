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


def _vehicle_damage_clauses(scene: SceneGraph) -> list[str]:
    """Per-vehicle rundown clauses shared by both illustration prompts:
    approach/maneuver/speed (or "parked"), then every damage-zone mark, in
    ``scene.vehicles`` order. Pure text assembly from the constrained
    vocabulary -- no geometry, no model call, no register-specific wording,
    so both the still prompt (3D-CGI register) and the clip prompt
    (top-down-diagram register) describe exactly the same cast of vehicles.
    """
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
    return parts


def _forensic_scene_description(scene: SceneGraph, timeline: Timeline) -> str:
    """The shared forensic-reconstruction scene wording for the still-image
    prompt only (see ``illustration_still_prompt``).

    Deterministic, built only from the constrained vocabulary plus the
    Timeline's already-computed geometry, and kept in the computer-generated
    3D-CGI forensic-reconstruction register on purpose: a clean, serious CGI
    accident-reconstruction render, not a toy and not a cartoon, that states
    plainly it is a computer-generated reconstruction and not a real
    recording (self-disclosing). It depicts no people and no injuries, which
    keeps it clear of the sharper content-moderation triggers by
    construction, though unlike the retired toy-diorama register this exact
    wording has not yet been probed against a live provider.

    The impact participants' relative position and orientation (e.g. "the
    red car is directly behind the blue car, nose to tail") come from
    ``_impact_relationship_phrase``, so the prompt states the scene's actual
    layout instead of leaving it for the model to invent. See
    ``_forensic_diagram_description`` for the clip's own, separate
    top-down-diagram register, which shares the vehicle rundown and the
    relationship sentence with this function but not the 3D-CGI framing.
    """
    road = scene.road
    parts = _vehicle_damage_clauses(scene)
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
    """Deterministic prompt for a generated establish-shot still (text -> image).

    No longer called by the pipeline. The illustration clip's seed image is
    now the case's own deterministic schematic raster (the impact-frame
    render, unwatermarked -- see ``schematic.SchematicArtifacts.seed_png``
    and ``pipeline.py`` step 5), not a generated still, so this function has
    no manifest field of its own any more (see ``ILLUSTRATION_SEED_NOTE``
    for what the manifest records instead). Kept here unchanged and still
    unit-tested as the prompt ClaimScene would send if a real generated-still
    path is ever added back.
    """
    return (
        "Clean, serious forensic accident-reconstruction still, "
        "establish-shot framing. "
        + _forensic_scene_description(scene, timeline)
        + " Slightly elevated three-quarter view of the whole scene."
    )


def _forensic_diagram_description(scene: SceneGraph, timeline: Timeline) -> str:
    """The shared top-down-diagram scene wording for the illustration CLIP
    prompt only (see ``illustration_prompt``).

    The clip's seed image is a raster of the case's own deterministic
    schematic (see ``schematic.py`` / ``pipeline.py`` step 5): a flat
    top-down blueprint diagram, not a three-quarter-view 3D CGI establish
    shot. Describing the clip in 3D-CGI terms while feeding it a 2D top-down
    seed is exactly the prompt/seed mismatch this rewrite exists to remove
    -- a live render on 2026-07-30 rendered a stated rear-end collision as a
    head-on collision even with an explicit geometry sentence already in the
    prompt, so this description stays in the top-down-diagram register
    throughout, still self-disclosing (computer-generated, not a real
    recording) and still free of any word that invites photorealism.

    Shares the per-vehicle rundown (``_vehicle_damage_clauses``) and the
    impact-relationship sentence (``_impact_relationship_phrase``) with
    ``_forensic_scene_description`` (the still-prompt's now pipeline-unused
    sibling), so both stay consistent with the same underlying geometry.
    """
    road = scene.road
    parts = _vehicle_damage_clauses(scene)
    relationship = _impact_relationship_phrase(scene, timeline)
    relationship_sentence = f" {relationship}" if relationship else ""
    return (
        "Computer-generated top-down forensic reconstruction diagram, not "
        "a real recording, showing "
        f"{_LAYOUT_WORDS[scene.road.layout.value]} with "
        f"{_SIGNAL_WORDS[road.signal]}: " + "; ".join(parts) + "."
        + relationship_sentence +
        " Flat orthographic bird's-eye view, engineering blueprint style, "
        "not a toy, not a cartoon, no people, no injuries. The rendered "
        "video itself must contain no text, no labels, no captions, and no "
        "watermarks."
    )


def illustration_prompt(scene: SceneGraph, timeline: Timeline) -> str:
    """Deterministic prompt for the illustration clip (image -> video).

    The clip is no longer seeded by a generated establish-shot still: its
    seed image is the case's own deterministic schematic raster (see
    ``pipeline.py`` step 5 / ``schematic.SchematicArtifacts.seed_png``), so
    this prompt asks the model to gently animate THAT top-down diagram --
    camera parallax and a slow push, vehicles held perfectly static --
    instead of describing a scene the model would have to invent from
    scratch. A live render on 2026-07-30 (case
    ``live-forensic-retry-0bb32eeb``) rendered a stated rear-end collision as
    a head-on collision even with an explicit geometry sentence already in
    the prompt: prompt-level control alone was not enough to pin geometry a
    diffusion model is free to reinterpret. Seeding from the schematic makes
    the layout INHERITED rather than requested, so it cannot drift; this
    prompt still restates the same geometry sentence too, so the seed image
    and the text agree (see ``_impact_relationship_phrase``). This used to
    end with an ``Overlay text: '{DISCLOSURE}'`` request; that line is gone
    -- the disclosure is guaranteed instead by
    ``watermark.burn_clip_watermark``, deterministically, after generation.
    """
    return (
        "Gentle camera parallax and a slow push over this top-down forensic "
        "reconstruction diagram. "
        + _forensic_diagram_description(scene, timeline)
        + " The vehicles stay perfectly static in the exact positions "
        "already shown in the diagram; only the camera moves."
    )


#: What the sealed manifest records in ``illustration.still_prompt`` now that
#: the field no longer names a text-to-image generation (see pipeline.py step
#: 5). Plain and truthful rather than a per-scene template: there is no
#: generation content to describe, only the fact that there is none. Carries
#: the same self-disclosure markers a real generation prompt is required to
#: carry (see ``scripts/readiness.py::check_genblaze_illustration_port_sealed``)
#: so that check keeps verifying the illustration's honesty even though
#: nothing here was ever sent to a model.
ILLUSTRATION_SEED_NOTE = (
    "No still-image model or prompt was used to produce this seed. It is "
    "the case's own deterministic top-down schematic raster (the "
    "impact-frame render), computer-generated from the factual Timeline "
    "and not a real recording, stripped of the schematic's own watermark so "
    "the illustration clip's single burned-in disclosure caption is the "
    "only on-image text. The illustration clip below is seeded from exactly "
    "these pixels, so its geometry is inherited, not requested."
)
