"""Deterministic incident report text (template-based for the foundation).

Every sentence is derived from the constrained vocabulary. No LLM narrative
yet — a later phase may add one, clearly labelled, on top of this factual
layer. The report always carries the disclosure line.
"""
from __future__ import annotations

from .layout import Timeline
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


def _forensic_scene_description(scene: SceneGraph) -> str:
    """The shared forensic-reconstruction scene wording (still + clip prompts).

    Deterministic, built only from the constrained vocabulary, and kept in
    the computer-generated forensic-reconstruction register on purpose: a
    clean, serious CGI accident-reconstruction render, not a toy and not a
    cartoon, that states plainly it is a computer-generated reconstruction
    and not a real recording (self-disclosing). It depicts no people and no
    injuries, which keeps it clear of the sharper content-moderation
    triggers by construction, though unlike the retired toy-diorama register
    this exact wording has not yet been probed against a live provider.
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
    return (
        "Computer-generated 3D forensic accident-reconstruction render, not "
        "a real recording, showing "
        f"{_LAYOUT_WORDS[scene.road.layout.value]} with "
        f"{_SIGNAL_WORDS[road.signal]}: " + "; ".join(parts) + ". "
        "Accurate vehicle proportions, real road surface and lane markings, "
        "neutral daylight, professional CGI clarity, not a toy, not a "
        "cartoon, no people, no injuries."
    )


def illustration_still_prompt(scene: SceneGraph) -> str:
    """Deterministic prompt for the establish-shot still (text → image)."""
    return (
        "Clean, serious forensic accident-reconstruction still, "
        "establish-shot framing. "
        + _forensic_scene_description(scene)
        + " Slightly elevated three-quarter view of the whole scene."
    )


def illustration_prompt(scene: SceneGraph) -> str:
    """Deterministic prompt for the illustration clip (still → video).

    Built only from the constrained vocabulary; always ends with the
    self-disclosing instruction.
    """
    return (
        "Slow smooth camera orbit around this forensic "
        "accident-reconstruction scene. "
        + _forensic_scene_description(scene)
        + " The vehicles stay perfectly still; gentle parallax only. "
        f"Overlay text: '{DISCLOSURE}'."
    )
