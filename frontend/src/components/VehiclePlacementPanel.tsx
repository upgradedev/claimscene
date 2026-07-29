import { useRef, useState } from "react";
import { Move3d } from "lucide-react";
import {
  humanize,
  impactClock,
  movementFor,
  patchMovement,
  setImpact,
  type Approach,
  type RoadLayout,
  type Scene,
  type Vehicle,
  type VehicleColor,
} from "@/lib/scene";
import {
  DRAG_THRESHOLD_PX,
  angleToApproach,
  angleToClock,
  clockStep,
  describeClock,
  handlePosition,
  markerPosition,
  type Pct,
} from "@/lib/manipulate";
import { cn } from "@/lib/utils";

const VEHICLE_HEX: Record<VehicleColor, string> = {
  white: "#e8e8e8",
  black: "#33343a",
  silver: "#b8bcc2",
  gray: "#7c8288",
  red: "#d1495b",
  blue: "#3d7dd8",
  green: "#4c9f70",
  yellow: "#e3b23c",
};

const APPROACH_KEY: Record<string, Approach> = {
  ArrowUp: "N",
  ArrowRight: "E",
  ArrowDown: "S",
  ArrowLeft: "W",
};

type DragState =
  | { id: string; mode: "move"; candidate: Approach | null }
  | { id: string; mode: "rotate"; candidate: number | null };

/** Direct manipulation for the review-adjust schematic: click a vehicle to
 *  select it, drag to move it between the vocabulary's 4 legal approaches,
 *  and drag its rotation handle through the 12 legal clock positions.
 *
 *  This panel draws its OWN small road template and vehicle markers — it
 *  never touches the server-rendered `SchematicPreview` `<img>` below it
 *  (that stays a hardened, script-proof `<img src="data:...">`, and its
 *  positions come from a physics engine this panel does not, and should not,
 *  reproduce). Every drag/keyboard gesture here snaps to, and only ever
 *  writes, one of the SceneGraph's existing legal enum values via the same
 *  `patchMovement`/`setImpact` helpers the dropdowns and clock picker use —
 *  so the sealed AI->human approval receipt records these edits exactly as
 *  it would a dropdown edit, and the constrained-vocabulary gate never sees
 *  the difference. */
export function VehiclePlacementPanel({
  scene,
  proposedScene,
  onChange,
}: {
  scene: Scene;
  proposedScene?: Scene | null;
  onChange: (next: Scene) => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drag, setDrag] = useState<DragState | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const markerRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  // Imperative pointer-drag handlers close over these refs (never the
  // `scene`/`onChange` props directly) so a long-running gesture always
  // reads and writes the LATEST scene, never a stale one from pointerdown.
  const sceneRef = useRef(scene);
  sceneRef.current = scene;
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  // A real drag re-positions the marker/handle to its candidate slot as it
  // moves, so pointerup often lands away from wherever pointerdown started.
  // Browsers then synthesize a native "click" targeting the nearest common
  // ancestor of the down/up elements — typically this canvas — which would
  // otherwise fire the background deselect handler right after a successful
  // drag. This ref suppresses exactly that one synthetic click.
  const suppressClickRef = useRef(false);

  const selectedVehicle = scene.vehicles.find((v) => v.id === selectedId) ?? null;

  const approachOf = (s: Scene, id: string): Approach => movementFor(s, id)?.approach ?? "N";

  // Group vehicles by their current approach so same-side vehicles fan out
  // instead of stacking on one point.
  const byApproach = new Map<Approach, string[]>();
  for (const v of scene.vehicles) {
    const a = approachOf(scene, v.id);
    byApproach.set(a, [...(byApproach.get(a) ?? []), v.id]);
  }
  const slotFor = (id: string, approach: Approach): Pct => {
    const group = byApproach.get(approach) ?? [id];
    return markerPosition(approach, group.indexOf(id), group.length);
  };

  function commitMove(id: string, approach: Approach) {
    onChangeRef.current(patchMovement(sceneRef.current, id, { approach }));
    setAnnouncement(`${id} moved to ${humanize(approach)} approach`);
  }

  function commitRotate(id: string, clock: number | null) {
    onChangeRef.current(setImpact(sceneRef.current, id, clock));
    setAnnouncement(`${id} impact ${describeClock(clock)}`);
  }

  function beginMove(vehicleId: string, e: React.PointerEvent<HTMLButtonElement>) {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    setSelectedId(vehicleId);
    const startX = e.clientX;
    const startY = e.clientY;
    let candidate: Approach | null = null;
    setDrag({ id: vehicleId, mode: "move", candidate: null });

    const onMove = (ev: PointerEvent) => {
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      candidate = Math.hypot(dx, dy) < DRAG_THRESHOLD_PX ? null : angleToApproach(dx, dy);
      if (candidate) suppressClickRef.current = true;
      setDrag({ id: vehicleId, mode: "move", candidate });
    };
    const end = (commit: boolean) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onCancel);
      setDrag(null);
      if (commit && candidate) commitMove(vehicleId, candidate);
    };
    const onUp = () => end(true);
    const onCancel = () => end(false);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onCancel);
  }

  function beginRotate(vehicleId: string, e: React.PointerEvent<HTMLButtonElement>) {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const rect = markerRefs.current[vehicleId]?.getBoundingClientRect();
    const center = rect
      ? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
      : { x: e.clientX, y: e.clientY };
    const startClock = impactClock(sceneRef.current, vehicleId);
    let candidate = startClock;
    setDrag({ id: vehicleId, mode: "rotate", candidate });

    const onMove = (ev: PointerEvent) => {
      candidate = angleToClock(ev.clientX - center.x, ev.clientY - center.y);
      // Only once the handle actually lands on a different clock does it
      // visually relocate (see the render below), so only then can pointerup
      // land away from it and risk the stray click described above.
      if (candidate !== startClock) suppressClickRef.current = true;
      setDrag({ id: vehicleId, mode: "rotate", candidate });
    };
    const end = (commit: boolean) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onCancel);
      setDrag(null);
      if (commit) commitRotate(vehicleId, candidate);
    };
    const onUp = () => end(true);
    const onCancel = () => end(false);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onCancel);
  }

  function onMarkerKeyDown(vehicle: Vehicle, e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      setSelectedId(null);
      return;
    }
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      setSelectedId(vehicle.id);
      return;
    }
    const approach = APPROACH_KEY[e.key];
    if (!e.shiftKey && approach) {
      e.preventDefault();
      commitMove(vehicle.id, approach);
      return;
    }
    if (e.key === "[" || (e.shiftKey && e.key === "ArrowLeft")) {
      e.preventDefault();
      commitRotate(vehicle.id, clockStep(impactClock(scene, vehicle.id), -1));
      return;
    }
    if (e.key === "]" || (e.shiftKey && e.key === "ArrowRight")) {
      e.preventDefault();
      commitRotate(vehicle.id, clockStep(impactClock(scene, vehicle.id), 1));
    }
  }

  function onHandleKeyDown(vehicle: Vehicle, e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      setSelectedId(null);
      return;
    }
    if (e.key === "Backspace" || e.key === "Delete") {
      e.preventDefault();
      commitRotate(vehicle.id, null);
      return;
    }
    const decrement = e.key === "[" || e.key === "ArrowLeft" || e.key === "ArrowUp";
    const increment = e.key === "]" || e.key === "ArrowRight" || e.key === "ArrowDown";
    if (decrement || increment) {
      e.preventDefault();
      commitRotate(vehicle.id, clockStep(impactClock(scene, vehicle.id), increment ? 1 : -1));
    }
  }

  return (
    <section className="sheet p-4">
      <h3 className="label-caps mb-2 flex items-center gap-1.5">
        <Move3d className="h-3 w-3 text-cyan-400" /> Placement control
      </h3>
      <p className="mb-3 font-mono text-[11px] text-blueprint-dim">
        Click a vehicle to select it, then drag to move or drag its handle to rotate. Arrow keys
        move the selected vehicle; [ and ] (or shift+arrow) rotate it. Escape deselects.
      </p>

      <div
        role="group"
        aria-label="Vehicle placement canvas"
        className="relative mx-auto aspect-square w-full max-w-[22rem] overflow-hidden rounded border border-steel-700 bg-steel-950/60"
        onClick={(e) => {
          if (suppressClickRef.current) {
            suppressClickRef.current = false;
            return;
          }
          if (e.target === e.currentTarget) setSelectedId(null);
        }}
      >
        <RoadTemplate layout={scene.road.layout} />

        {/* Candidate snap targets, shown only while actively moving a vehicle. */}
        {drag?.mode === "move" &&
          (["N", "E", "S", "W"] as Approach[]).map((approach) => {
            const pos = markerPosition(approach, 0, 1);
            const active = drag.candidate === approach;
            return (
              <div
                key={approach}
                aria-hidden
                className={cn(
                  "pointer-events-none absolute h-9 w-9 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-dashed transition-colors",
                  active ? "border-amber-400 bg-amber-400/20" : "border-steel-500/60",
                )}
                style={{ left: `${pos.left}%`, top: `${pos.top}%` }}
              />
            );
          })}

        {scene.vehicles.map((vehicle) => {
          const approach =
            drag?.mode === "move" && drag.id === vehicle.id && drag.candidate
              ? drag.candidate
              : approachOf(scene, vehicle.id);
          const pos =
            drag?.mode === "move" && drag.id === vehicle.id && drag.candidate
              ? markerPosition(drag.candidate, 0, 1)
              : slotFor(vehicle.id, approach);
          const clock =
            drag?.mode === "rotate" && drag.id === vehicle.id
              ? drag.candidate
              : impactClock(scene, vehicle.id);
          const selected = selectedId === vehicle.id;

          const proposedMovement = proposedScene ? movementFor(proposedScene, vehicle.id) : undefined;
          const ghostApproach =
            proposedMovement && proposedMovement.approach !== approachOf(scene, vehicle.id)
              ? proposedMovement.approach
              : null;
          const proposedClock = proposedScene ? impactClock(proposedScene, vehicle.id) : undefined;
          const ghostClock =
            proposedScene && proposedClock !== undefined && proposedClock !== impactClock(scene, vehicle.id)
              ? proposedClock
              : null;

          return (
            <div key={vehicle.id}>
              {ghostApproach && (
                <div
                  aria-hidden
                  className="pointer-events-none absolute grid h-9 w-9 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border-2 border-dashed border-cyan-300/60 font-mono text-[9px] text-cyan-200/80"
                  style={markerStyle(markerPosition(ghostApproach, 0, 1))}
                >
                  {vehicle.id}
                </div>
              )}

              <button
                type="button"
                ref={(el) => {
                  markerRefs.current[vehicle.id] = el;
                }}
                aria-pressed={selected}
                aria-label={`${vehicle.id}: ${humanize(vehicle.color)} ${humanize(vehicle.kind)}, ${humanize(approach)} approach, ${describeClock(clock)}. Selected vehicles can be moved with arrow keys and rotated with [ and ].`}
                onPointerDown={(e) => beginMove(vehicle.id, e)}
                onKeyDown={(e) => onMarkerKeyDown(vehicle, e)}
                className={cn(
                  "absolute grid h-11 w-11 -translate-x-1/2 -translate-y-1/2 touch-none select-none place-items-center rounded-full border-2 font-mono text-[10px] font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400",
                  drag?.mode === "move" && drag.id === vehicle.id ? "cursor-grabbing" : "cursor-grab",
                  selected
                    ? "border-cyan-400 bg-steel-800 text-cyan-100 shadow-glow-cyan"
                    : "border-steel-600 bg-steel-900 text-blueprint-text hover:border-cyan-400/60",
                )}
                style={markerStyle(pos)}
              >
                <span
                  aria-hidden
                  className="absolute inset-1 rounded-full opacity-70"
                  style={{ backgroundColor: VEHICLE_HEX[vehicle.color] }}
                />
                <span className="relative">{vehicle.id.replace(/^veh_/, "")}</span>
              </button>

              {selected && (
                <RotateHandle
                  vehicle={vehicle}
                  markerPos={pos}
                  clock={clock}
                  ghostClock={ghostClock}
                  dragging={drag?.mode === "rotate" && drag.id === vehicle.id}
                  onPointerDown={(e) => beginRotate(vehicle.id, e)}
                  onKeyDown={(e) => onHandleKeyDown(vehicle, e)}
                />
              )}
            </div>
          );
        })}
      </div>

      <p className="mt-2 min-h-[1.25em] font-mono text-[11px] text-cyan-200">
        {selectedVehicle
          ? `${selectedVehicle.id}: ${humanize(approachOf(scene, selectedVehicle.id))} approach, ${describeClock(
              drag?.mode === "rotate" && drag.id === selectedVehicle.id
                ? drag.candidate
                : impactClock(scene, selectedVehicle.id),
            )}`
          : "No vehicle selected."}
      </p>
      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>
    </section>
  );
}

function markerStyle(pos: Pct): React.CSSProperties {
  return { left: `${pos.left}%`, top: `${pos.top}%` };
}

function RotateHandle({
  vehicle,
  markerPos,
  clock,
  ghostClock,
  dragging,
  onPointerDown,
  onKeyDown,
}: {
  vehicle: Vehicle;
  markerPos: Pct;
  clock: number | null;
  ghostClock: number | null;
  dragging: boolean;
  onPointerDown: (e: React.PointerEvent<HTMLButtonElement>) => void;
  onKeyDown: (e: React.KeyboardEvent) => void;
}) {
  const struck = clock !== null;
  const pos = handlePosition(markerPos, clock ?? 12);
  return (
    <>
      {ghostClock !== null && (
        <div
          aria-hidden
          className="pointer-events-none absolute h-5 w-5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-dashed border-cyan-300/60"
          style={markerStyle(handlePosition(markerPos, ghostClock))}
        />
      )}
      <button
        type="button"
        onPointerDown={onPointerDown}
        onKeyDown={onKeyDown}
        aria-label={`Rotate ${vehicle.id}: currently ${describeClock(clock)}. Drag, or use arrow keys, or [ and ], to change. Backspace clears the impact.`}
        className={cn(
          "absolute grid h-11 w-11 -translate-x-1/2 -translate-y-1/2 touch-none select-none place-items-center rounded-full border-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400",
          dragging ? "cursor-grabbing" : "cursor-grab",
          struck
            ? "border-amber-400 bg-amber-400 text-steel-950 shadow-glow-amber"
            : "border-steel-500 bg-steel-900/90 text-blueprint-dim",
        )}
        style={markerStyle(pos)}
      >
        <span className="font-mono text-[9px] font-bold">{clock ?? "-"}</span>
      </button>
    </>
  );
}

// A small decorative (aria-hidden, non-interactive) top-down road template so
// the placement panel reads as "a road", not an abstract grid. Purely
// cosmetic — legality of every vehicle position comes from the compass
// snapping in lib/manipulate.ts, never from anything drawn here.
function RoadTemplate({ layout }: { layout: RoadLayout }) {
  const stroke = "#4a6b80";
  const fill = "#16242e";
  const center = "#d9a441";
  return (
    <svg
      viewBox="0 0 100 100"
      aria-hidden
      className="pointer-events-none absolute inset-0 h-full w-full"
    >
      <rect x="0" y="0" width="100" height="100" fill="#0e1a22" />
      {layout === "straight" && (
        <>
          <rect x="38" y="0" width="24" height="100" fill={fill} />
          <line x1="50" y1="0" x2="50" y2="100" stroke={center} strokeWidth="1" strokeDasharray="4 3" />
        </>
      )}
      {layout === "x_intersection" && (
        <>
          <rect x="38" y="0" width="24" height="100" fill={fill} />
          <rect x="0" y="38" width="100" height="24" fill={fill} />
          <line x1="50" y1="0" x2="50" y2="100" stroke={center} strokeWidth="1" strokeDasharray="4 3" />
          <line x1="0" y1="50" x2="100" y2="50" stroke={center} strokeWidth="1" strokeDasharray="4 3" />
        </>
      )}
      {layout === "t_intersection" && (
        <>
          <rect x="0" y="38" width="100" height="24" fill={fill} />
          <rect x="38" y="50" width="24" height="50" fill={fill} />
          <line x1="0" y1="50" x2="100" y2="50" stroke={center} strokeWidth="1" strokeDasharray="4 3" />
          <line x1="50" y1="50" x2="50" y2="100" stroke={center} strokeWidth="1" strokeDasharray="4 3" />
        </>
      )}
      {layout === "roundabout" && (
        <>
          <rect x="38" y="0" width="24" height="100" fill={fill} />
          <rect x="0" y="38" width="100" height="24" fill={fill} />
          <circle cx="50" cy="50" r="22" fill={fill} stroke={stroke} strokeWidth="1.5" />
          <circle cx="50" cy="50" r="10" fill="#0e1a22" stroke={center} strokeWidth="1" />
        </>
      )}
      {layout === "parking_lot" && (
        <>
          <rect x="10" y="18" width="80" height="64" fill={fill} />
          {[28, 40, 52, 64, 76].map((y) => (
            <line key={y} x1="10" y1={y} x2="90" y2={y} stroke={stroke} strokeWidth="0.8" />
          ))}
        </>
      )}
    </svg>
  );
}
