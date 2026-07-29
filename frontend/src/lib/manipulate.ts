// Pure geometry for the direct-manipulation placement panel (VehiclePlacementPanel).
//
// The SceneGraph has no continuous position field by design (see scene.py) —
// every geometric position is derived server-side by the deterministic
// LayoutEngine, which the browser never runs. So this module does NOT try to
// reproduce that physics. It defines a small, self-contained, frontend-only
// coordinate system (a compass rose around a junction, percentages of a
// square panel) purely so a vehicle can be clicked, dragged and rotated —
// and every function here returns, or snaps into, one of the vocabulary's
// existing legal enum values (an `Approach` or a clock position 1..12).
// Nothing here ever produces or accepts a raw pixel/continuous coordinate as
// output; pixels only ever come IN (as a drag vector) and an enum value goes
// OUT. That is the anti-hallucination contract this module exists to keep.
import { APPROACHES, type Approach } from "./scene";

export interface Pct {
  left: number;
  top: number;
}

// Keep every rendered element's CENTER inside this band so a 44px (~11%
// of the smallest supported 327px-wide mobile panel) handle can never be
// clipped by the panel's own edge, regardless of which slot/stagger/handle
// radius produced the raw value.
const CLAMP_MIN = 9;
const CLAMP_MAX = 91;

export function clampPct(v: number): number {
  if (!Number.isFinite(v)) return 50;
  return Math.min(CLAMP_MAX, Math.max(CLAMP_MIN, v));
}

export const PANEL_CENTER: Pct = { left: 50, top: 50 };

// Compass anchors, matching the backend's own approach convention (N=up,
// E=right, S=down, W=left — see layout.py's _APPROACH_ROT and the docstring
// "x east, y north"; screen-space simply renders +y as up).
const SLOT: Record<Approach, Pct> = {
  N: { left: 50, top: 26 },
  E: { left: 74, top: 50 },
  S: { left: 50, top: 74 },
  W: { left: 26, top: 50 },
};

const STAGGER_STEP = 16;
export const HANDLE_RADIUS_PCT = 16;
export const DRAG_THRESHOLD_PX = 6;

/** N/S share a horizontal (left) stagger axis; E/W share a vertical (top) one
 *  — vehicles queued at the same approach fan out perpendicular to it. */
function staggerAxis(approach: Approach): "left" | "top" {
  return approach === "N" || approach === "S" ? "left" : "top";
}

/** Where the `index`-th of `count` vehicles sharing `approach` is drawn. */
export function markerPosition(approach: Approach, index: number, count: number): Pct {
  const base = SLOT[approach];
  if (count <= 1) return { left: clampPct(base.left), top: clampPct(base.top) };
  const offset = (index - (count - 1) / 2) * STAGGER_STEP;
  const axis = staggerAxis(approach);
  return {
    left: clampPct(axis === "left" ? base.left + offset : base.left),
    top: clampPct(axis === "top" ? base.top + offset : base.top),
  };
}

/** Angle convention shared with ClockPicker.pos(): 0deg = up/north, clockwise
 *  positive, screen dx = right+, dy = down+. Always finite, always in
 *  [0, 360). */
function angleDeg(dx: number, dy: number): number {
  if (!Number.isFinite(dx) || !Number.isFinite(dy)) return 0;
  const deg = (Math.atan2(dx, -dy) * 180) / Math.PI;
  return (deg + 360) % 360;
}

/** Snap a drag vector to the nearest of the 4 legal `Approach` values. Total
 *  function: every real number pair (including huge, tiny, or non-finite
 *  ones) maps to a member of `APPROACHES` — never a raw angle or coordinate. */
export function angleToApproach(dx: number, dy: number): Approach {
  const idx = Math.round(angleDeg(dx, dy) / 90) % 4;
  return APPROACHES[idx] ?? "N";
}

/** Snap a drag vector to the nearest of the 12 legal clock positions
 *  (1..12, never 0). Total function, same guarantee as `angleToApproach`. */
export function angleToClock(dx: number, dy: number): number {
  const raw = Math.round(angleDeg(dx, dy) / 30) % 12;
  return raw === 0 ? 12 : raw;
}

/** The rotation handle's offset from its vehicle marker's own center, for a
 *  given clock position — the exact inverse of `angleToClock`'s convention,
 *  so dragging the handle to a screen position and reading it back off always
 *  agrees with where the handle is drawn for that clock value. */
export function clockHandleOffset(clock: number, radius: number = HANDLE_RADIUS_PCT): Pct {
  const rad = ((clock % 12) * 30 * Math.PI) / 180;
  return { left: Math.sin(rad) * radius, top: -Math.cos(rad) * radius };
}

export function handlePosition(marker: Pct, clock: number, radius: number = HANDLE_RADIUS_PCT): Pct {
  const off = clockHandleOffset(clock, radius);
  return { left: clampPct(marker.left + off.left), top: clampPct(marker.top + off.top) };
}

/** One keyboard/step nudge of a clock position. Starting from "not struck"
 *  (null) always lands on 12 first, in either direction — you "turn the
 *  impact on" before you fine-tune it. Always wraps within 1..12. */
export function clockStep(current: number | null, delta: 1 | -1): number {
  if (current === null) return 12;
  return ((current - 1 + delta + 12) % 12) + 1;
}

const CLOCK_MEANING: Record<number, string> = {
  12: "front",
  3: "right side",
  6: "rear",
  9: "left side",
};

/** Human label for a live readout, e.g. "3 o'clock (right side)". */
export function describeClock(clock: number | null): string {
  if (clock === null) return "not struck";
  const meaning = CLOCK_MEANING[clock];
  return meaning ? `${clock} o'clock (${meaning})` : `${clock} o'clock`;
}
