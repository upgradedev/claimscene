import { describe, expect, it } from "vitest";
import {
  addDamage,
  addVehicle,
  clampClock,
  emptyScene,
  impactClock,
  movementFor,
  nextVehicleId,
  patchDamage,
  patchMovement,
  patchVehicle,
  removeDamage,
  removeVehicle,
  sceneIsRenderable,
  SceneSchema,
  setImpact,
  setRoad,
} from "./scene";

describe("emptyScene", () => {
  it("is a valid scene with one vehicle and its movement", () => {
    const scene = emptyScene();
    expect(SceneSchema.safeParse(scene).success).toBe(true);
    expect(scene.vehicles).toHaveLength(1);
    expect(movementFor(scene, "veh_a")).toBeDefined();
    expect(sceneIsRenderable(scene)).toBe(true);
  });
});

describe("nextVehicleId", () => {
  it("skips taken ids", () => {
    const scene = emptyScene();
    expect(nextVehicleId(scene)).toBe("veh_b");
    const two = addVehicle(scene);
    expect(nextVehicleId(two)).toBe("veh_c");
  });
});

describe("addVehicle / removeVehicle", () => {
  it("adds a vehicle with a unique id and a movement", () => {
    const scene = addVehicle(emptyScene());
    expect(scene.vehicles).toHaveLength(2);
    expect(new Set(scene.vehicles.map((v) => v.id)).size).toBe(2);
    expect(movementFor(scene, scene.vehicles[1]!.id)).toBeDefined();
    expect(SceneSchema.safeParse(scene).success).toBe(true);
  });

  it("removes a vehicle and cascades its movement + impact (no dangling refs)", () => {
    let scene = addVehicle(emptyScene()); // veh_a, veh_b
    scene = setImpact(scene, "veh_b", 6);
    expect(scene.impacts).toHaveLength(1);
    scene = removeVehicle(scene, "veh_b");
    expect(scene.vehicles.map((v) => v.id)).toEqual(["veh_a"]);
    expect(scene.movements.some((m) => m.vehicle_id === "veh_b")).toBe(false);
    expect(scene.impacts.some((i) => i.vehicle_id === "veh_b")).toBe(false);
    expect(SceneSchema.safeParse(scene).success).toBe(true);
  });
});

describe("clampClock", () => {
  it("clamps to 1..12 and rounds", () => {
    expect(clampClock(0)).toBe(1);
    expect(clampClock(13)).toBe(12);
    expect(clampClock(6.4)).toBe(6);
    expect(clampClock(Number.NaN)).toBe(12);
  });
});

describe("damage editing", () => {
  it("adds, patches (clamping the clock) and removes damage", () => {
    let scene = addDamage(emptyScene(), "veh_a");
    expect(scene.vehicles[0]!.damage).toHaveLength(1);
    scene = patchDamage(scene, "veh_a", 0, { clock_position: 99, severity: "crush", note: "hi" });
    expect(scene.vehicles[0]!.damage[0]).toMatchObject({ clock_position: 12, severity: "crush", note: "hi" });
    scene = removeDamage(scene, "veh_a", 0);
    expect(scene.vehicles[0]!.damage).toHaveLength(0);
  });
});

describe("setImpact / impactClock", () => {
  it("sets, reads and toggles off an impact", () => {
    let scene = emptyScene();
    expect(impactClock(scene, "veh_a")).toBeNull();
    scene = setImpact(scene, "veh_a", 15); // clamps to 12
    expect(impactClock(scene, "veh_a")).toBe(12);
    scene = setImpact(scene, "veh_a", null); // toggle off
    expect(impactClock(scene, "veh_a")).toBeNull();
    expect(scene.impacts).toHaveLength(0);
  });
});

describe("patchMovement / patchVehicle / setRoad", () => {
  it("creates a movement if missing and mutates only the target", () => {
    const bare = { ...emptyScene(), movements: [] };
    const scene = patchMovement(bare, "veh_a", { maneuver: "reversing" });
    expect(movementFor(scene, "veh_a")).toMatchObject({ vehicle_id: "veh_a", maneuver: "reversing" });
  });

  it("patchVehicle changes only kind/color", () => {
    const scene = patchVehicle(emptyScene(), "veh_a", { kind: "truck", color: "red" });
    expect(scene.vehicles[0]).toMatchObject({ kind: "truck", color: "red" });
  });

  it("setRoad clamps lanes to 1..3", () => {
    expect(setRoad(emptyScene(), { lanes_per_direction: 9 }).road.lanes_per_direction).toBe(3);
    expect(setRoad(emptyScene(), { lanes_per_direction: 0 }).road.lanes_per_direction).toBe(1);
  });

  it("edits never mutate the input scene (immutability)", () => {
    const scene = emptyScene();
    const before = JSON.stringify(scene);
    addVehicle(scene);
    setImpact(scene, "veh_a", 6);
    patchVehicle(scene, "veh_a", { color: "red" });
    expect(JSON.stringify(scene)).toBe(before);
  });
});
