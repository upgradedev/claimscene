// The constrained accident-scene vocabulary, mirrored from the backend
// (src/claimscene/scene.py) — the anti-hallucination core, in the browser.
//
// Every editable control in the review panel is bound to one of these closed
// enums (dropdowns + a 12-position clock picker), so a reviewer can only ever
// produce a scene the backend's Pydantic gate will accept. The edit helpers
// are pure functions (unit-tested) that keep the draft valid *by construction*:
// vehicle ids stay unique and auto-managed, movements/impacts never dangle,
// clock positions stay in 1..12, lanes in 1..3.
import { z } from "zod";

// ── closed enums (const tuples → zod enums → union types) ────────────────────
export const VEHICLE_KINDS = ["car", "van", "truck", "motorcycle", "bicycle"] as const;
export const VEHICLE_COLORS = [
  "white", "black", "silver", "gray", "red", "blue", "green", "yellow",
] as const;
export const DAMAGE_SEVERITIES = ["scratch", "dent", "crush"] as const;
export const ROAD_LAYOUTS = [
  "straight", "t_intersection", "x_intersection", "roundabout", "parking_lot",
] as const;
export const SIGNALS = ["none", "stop_sign", "traffic_light", "yield"] as const;
export const APPROACHES = ["N", "E", "S", "W"] as const;
export const MANEUVERS = [
  "straight", "left_turn", "right_turn", "u_turn", "lane_change", "reversing", "parked",
] as const;
export const SPEED_BANDS = ["stopped", "low", "moderate", "high"] as const;
export const SEQUENCE_EVENTS = [
  "vehicles_approach", "braking", "evasive_steering", "impact",
  "secondary_impact", "vehicles_come_to_rest",
] as const;

// ── zod schema (parses the backend's extract/render scene) ───────────────────
export const DamageZoneSchema = z.object({
  clock_position: z.number().int().min(1).max(12),
  severity: z.enum(DAMAGE_SEVERITIES),
  note: z.string().nullable().optional(),
});
export const VehicleSchema = z.object({
  id: z.string().min(1).max(32),
  kind: z.enum(VEHICLE_KINDS),
  color: z.enum(VEHICLE_COLORS),
  damage: z.array(DamageZoneSchema).default([]),
});
export const RoadSchema = z.object({
  layout: z.enum(ROAD_LAYOUTS),
  lanes_per_direction: z.number().int().min(1).max(3),
  signal: z.enum(SIGNALS).default("none"),
});
export const MovementSchema = z.object({
  vehicle_id: z.string(),
  approach: z.enum(APPROACHES),
  maneuver: z.enum(MANEUVERS),
  speed_band: z.enum(SPEED_BANDS),
});
export const ImpactSchema = z.object({
  vehicle_id: z.string(),
  clock_position: z.number().int().min(1).max(12),
});
export const SceneSchema = z.object({
  schema: z.string().default("claimscene/scene/v1"),
  road: RoadSchema,
  vehicles: z.array(VehicleSchema).min(1),
  movements: z.array(MovementSchema).default([]),
  impacts: z.array(ImpactSchema).default([]),
  sequence: z.array(z.enum(SEQUENCE_EVENTS)).default([]),
  confidence_notes: z.array(z.string()).default([]),
});

export type DamageZone = z.infer<typeof DamageZoneSchema>;
export type Vehicle = z.infer<typeof VehicleSchema>;
export type Road = z.infer<typeof RoadSchema>;
export type Movement = z.infer<typeof MovementSchema>;
export type Impact = z.infer<typeof ImpactSchema>;
export type Scene = z.infer<typeof SceneSchema>;

export type VehicleKind = (typeof VEHICLE_KINDS)[number];
export type VehicleColor = (typeof VEHICLE_COLORS)[number];
export type DamageSeverity = (typeof DAMAGE_SEVERITIES)[number];
export type RoadLayout = (typeof ROAD_LAYOUTS)[number];
export type Signal = (typeof SIGNALS)[number];
export type Approach = (typeof APPROACHES)[number];
export type Maneuver = (typeof MANEUVERS)[number];
export type SpeedBand = (typeof SPEED_BANDS)[number];
export type SequenceEvent = (typeof SEQUENCE_EVENTS)[number];

// ── human labels (dropdown display) ──────────────────────────────────────────
export const LABEL: Record<string, string> = {
  none: "none", stop_sign: "stop sign", traffic_light: "traffic light",
  yield: "yield sign", straight: "straight", t_intersection: "T-intersection",
  x_intersection: "four-way", roundabout: "roundabout", parking_lot: "parking lot",
  left_turn: "left turn", right_turn: "right turn", u_turn: "U-turn",
  lane_change: "lane change", reversing: "reversing", parked: "parked",
  stopped: "stopped", low: "low", moderate: "moderate", high: "high",
  N: "north", E: "east", S: "south", W: "west",
  vehicles_approach: "approach", braking: "braking",
  evasive_steering: "evasive steering", impact: "impact",
  secondary_impact: "secondary impact", vehicles_come_to_rest: "come to rest",
};
export const humanize = (v: string): string => LABEL[v] ?? v.replace(/_/g, " ");

// ── pure edit helpers (keep the draft valid by construction) ─────────────────
const IDS = "abcdefghijklmnopqrstuvwxyz";

/** Deep clone via structuredClone with a JSON fallback (jsdom/older engines). */
export function cloneScene(scene: Scene): Scene {
  if (typeof structuredClone === "function") return structuredClone(scene);
  return JSON.parse(JSON.stringify(scene)) as Scene;
}

/** The next free `veh_x` id (a..z, then veh_1, veh_2 …). Never collides. */
export function nextVehicleId(scene: Scene): string {
  const taken = new Set(scene.vehicles.map((v) => v.id));
  for (const c of IDS) if (!taken.has(`veh_${c}`)) return `veh_${c}`;
  let n = 1;
  while (taken.has(`veh_${n}`)) n += 1;
  return `veh_${n}`;
}

export function emptyScene(): Scene {
  return {
    schema: "claimscene/scene/v1",
    road: { layout: "x_intersection", lanes_per_direction: 1, signal: "none" },
    vehicles: [{ id: "veh_a", kind: "car", color: "silver", damage: [] }],
    movements: [
      { vehicle_id: "veh_a", approach: "N", maneuver: "straight", speed_band: "moderate" },
    ],
    impacts: [],
    sequence: ["vehicles_approach", "impact", "vehicles_come_to_rest"],
    confidence_notes: [],
  };
}

export function clampClock(n: number): number {
  if (Number.isNaN(n)) return 12;
  return Math.min(12, Math.max(1, Math.round(n)));
}

export function addVehicle(scene: Scene): Scene {
  const next = cloneScene(scene);
  const id = nextVehicleId(next);
  next.vehicles.push({ id, kind: "car", color: "blue", damage: [] });
  next.movements.push({ vehicle_id: id, approach: "S", maneuver: "straight", speed_band: "moderate" });
  return next;
}

/** Remove a vehicle and cascade-drop its movement + impact (no dangling refs). */
export function removeVehicle(scene: Scene, id: string): Scene {
  const next = cloneScene(scene);
  next.vehicles = next.vehicles.filter((v) => v.id !== id);
  next.movements = next.movements.filter((m) => m.vehicle_id !== id);
  next.impacts = next.impacts.filter((i) => i.vehicle_id !== id);
  return next;
}

export function patchVehicle(scene: Scene, id: string, patch: Partial<Pick<Vehicle, "kind" | "color">>): Scene {
  const next = cloneScene(scene);
  const v = next.vehicles.find((x) => x.id === id);
  if (v) Object.assign(v, patch);
  return next;
}

export function setRoad(scene: Scene, patch: Partial<Road>): Scene {
  const next = cloneScene(scene);
  next.road = { ...next.road, ...patch };
  if (patch.lanes_per_direction !== undefined) {
    next.road.lanes_per_direction = Math.min(3, Math.max(1, patch.lanes_per_direction));
  }
  return next;
}

export function addDamage(scene: Scene, id: string): Scene {
  const next = cloneScene(scene);
  const v = next.vehicles.find((x) => x.id === id);
  if (v) v.damage.push({ clock_position: 12, severity: "dent" });
  return next;
}

export function removeDamage(scene: Scene, id: string, index: number): Scene {
  const next = cloneScene(scene);
  const v = next.vehicles.find((x) => x.id === id);
  if (v) v.damage.splice(index, 1);
  return next;
}

export function patchDamage(scene: Scene, id: string, index: number, patch: Partial<DamageZone>): Scene {
  const next = cloneScene(scene);
  const v = next.vehicles.find((x) => x.id === id);
  const dz = v?.damage[index];
  if (dz) {
    Object.assign(dz, patch);
    if (patch.clock_position !== undefined) dz.clock_position = clampClock(patch.clock_position);
  }
  return next;
}

/** Set (or create) the single movement for a vehicle. */
export function patchMovement(scene: Scene, id: string, patch: Partial<Omit<Movement, "vehicle_id">>): Scene {
  const next = cloneScene(scene);
  let m = next.movements.find((x) => x.vehicle_id === id);
  if (!m) {
    m = { vehicle_id: id, approach: "N", maneuver: "straight", speed_band: "moderate" };
    next.movements.push(m);
  }
  Object.assign(m, patch);
  return next;
}

/** Toggle whether a vehicle is marked as struck, or set its impact clock. */
export function setImpact(scene: Scene, id: string, clock: number | null): Scene {
  const next = cloneScene(scene);
  next.impacts = next.impacts.filter((i) => i.vehicle_id !== id);
  if (clock !== null) next.impacts.push({ vehicle_id: id, clock_position: clampClock(clock) });
  return next;
}

export function impactClock(scene: Scene, id: string): number | null {
  return scene.impacts.find((i) => i.vehicle_id === id)?.clock_position ?? null;
}

export function movementFor(scene: Scene, id: string): Movement | undefined {
  return scene.movements.find((m) => m.vehicle_id === id);
}

/** A scene is renderable once it has at least one vehicle (the backend's own
 *  minimum). All other invariants are held by the edit helpers. */
export function sceneIsRenderable(scene: Scene): boolean {
  return scene.vehicles.length >= 1;
}
