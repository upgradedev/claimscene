import { ArrowLeft, Crosshair, Lock, Plus, Trash2 } from "lucide-react";
import {
  APPROACHES,
  DAMAGE_SEVERITIES,
  MANEUVERS,
  ROAD_LAYOUTS,
  SEQUENCE_EVENTS,
  SIGNALS,
  SPEED_BANDS,
  VEHICLE_COLORS,
  VEHICLE_KINDS,
  addDamage,
  addVehicle,
  humanize,
  impactClock,
  movementFor,
  patchDamage,
  patchMovement,
  patchVehicle,
  removeDamage,
  removeVehicle,
  sceneIsRenderable,
  setImpact,
  setRoad,
  type Scene,
  type Vehicle,
} from "@/lib/scene";
import type { ExtractResponse } from "@/lib/api";
import { useCaseStore } from "@/store/useCaseStore";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Field, Select, toOptions } from "../ui/fields";
import { ClockPicker } from "../ClockPicker";
import { SchematicPreview } from "../SchematicPreview";
import { VehiclePlacementPanel } from "../VehiclePlacementPanel";
import { StepHeading } from "./StepHeading";

const LAYOUT_OPTS = toOptions(ROAD_LAYOUTS, humanize);
const SIGNAL_OPTS = toOptions(SIGNALS, humanize);
const LANE_OPTS = [
  { value: "1", label: "1" },
  { value: "2", label: "2" },
  { value: "3", label: "3" },
];
const KIND_OPTS = toOptions(VEHICLE_KINDS, humanize);
const COLOR_OPTS = toOptions(VEHICLE_COLORS, humanize);
const APPROACH_OPTS = toOptions(APPROACHES, humanize);
const MANEUVER_OPTS = toOptions(MANEUVERS, humanize);
const SPEED_OPTS = toOptions(SPEED_BANDS, humanize);
const SEVERITY_OPTS = toOptions(DAMAGE_SEVERITIES, humanize);

function sourceLabel(extraction: ExtractResponse["extraction"] | null): string {
  switch (extraction?.source) {
    case "committed_ground_truth":
      return "sample ground-truth (offline)";
    case "vlm_extraction":
      return "VLM-extracted";
    case "fake_extraction":
      return "offline extractor";
    default:
      return extraction?.extractor ?? "proposed";
  }
}

export function ReviewStep() {
  const scene = useCaseStore((s) => s.scene);
  const proposedScene = useCaseStore((s) => s.proposedScene);
  const extraction = useCaseStore((s) => s.extraction);
  const setScene = useCaseStore((s) => s.setScene);
  const goTo = useCaseStore((s) => s.goTo);

  if (!scene) {
    return (
      <p className="font-mono text-sm text-blueprint-dim">
        No scene to review yet — go back and extract one.
      </p>
    );
  }

  const update = (next: Scene) => setScene(next);

  return (
    <div className="animate-fade-up">
      <StepHeading
        title="Review & adjust the scene"
        subtitle="This is where you stay in control. The AI only proposed the scene below — every field is a constrained control, and the top-down schematic redraws live as you correct it."
      />

      {/* The trust statement — the human-in-the-loop centrepiece. */}
      <div className="mb-6 flex flex-wrap items-center gap-3 rounded border border-cyan-400/25 bg-cyan-400/[0.05] px-4 py-2.5">
        <Lock className="h-4 w-4 shrink-0 text-cyan-400" />
        <p className="font-mono text-xs text-cyan-100">
          You stay in control — <span className="text-cyan-300">AI proposes, you confirm.</span>{" "}
          Nothing is sealed until you render.
        </p>
        <span className="ml-auto">
          <Badge variant="amber">source: {sourceLabel(extraction)}</Badge>
        </span>
      </div>

      {/* grid-cols-1 at the base pins the single mobile column to minmax(0,1fr)
          — without it the implicit column sizes to max-content and overflows. */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,400px)]">
        {/* ── editor ─────────────────────────────────────────────────── */}
        <div className="space-y-5">
          <section className="sheet p-4">
            <h3 className="label-caps mb-3 flex items-center gap-1.5">
              <Crosshair className="h-3 w-3 text-cyan-400" /> Road
            </h3>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <Field label="layout">
                <Select value={scene.road.layout} options={LAYOUT_OPTS}
                        onChange={(v) => update(setRoad(scene, { layout: v as Scene["road"]["layout"] }))} />
              </Field>
              <Field label="lanes / dir">
                <Select value={String(scene.road.lanes_per_direction)} options={LANE_OPTS}
                        onChange={(v) => update(setRoad(scene, { lanes_per_direction: Number(v) }))} />
              </Field>
              <Field label="traffic control">
                <Select value={scene.road.signal} options={SIGNAL_OPTS}
                        onChange={(v) => update(setRoad(scene, { signal: v as Scene["road"]["signal"] }))} />
              </Field>
            </div>
          </section>

          {scene.vehicles.map((v) => (
            <VehicleEditor
              key={v.id}
              scene={scene}
              vehicle={v}
              canRemove={scene.vehicles.length > 1}
              onChange={update}
            />
          ))}

          <Button variant="secondary" size="sm" onClick={() => update(addVehicle(scene))}>
            <Plus className="h-4 w-4" /> Add vehicle
          </Button>

          <SequenceEditor scene={scene} onChange={update} />
        </div>

        {/* ── live preview (sticky) ──────────────────────────────────── */}
        <div className="lg:sticky lg:top-20 lg:self-start">
          <VehiclePlacementPanel scene={scene} proposedScene={proposedScene} onChange={update} />
          <div className="mt-4">
            <SchematicPreview scene={scene} proposedScene={proposedScene} />
          </div>
          <div className="mt-4 flex flex-col gap-2">
            <Button
              size="lg"
              disabled={!sceneIsRenderable(scene)}
              onClick={() => goTo("render")}
            >
              Confirm &amp; render this case
            </Button>
            <Button variant="ghost" size="sm" onClick={() => goTo("source")}>
              <ArrowLeft className="h-3.5 w-3.5" /> Back to source
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function VehicleEditor({
  scene,
  vehicle,
  canRemove,
  onChange,
}: {
  scene: Scene;
  vehicle: Vehicle;
  canRemove: boolean;
  onChange: (scene: Scene) => void;
}) {
  const movement = movementFor(scene, vehicle.id);
  const struck = impactClock(scene, vehicle.id);

  return (
    <section className="sheet p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="label-caps flex items-center gap-2">
          <span className="rounded bg-steel-700 px-1.5 py-0.5 font-mono text-[11px] text-cyan-200">
            {vehicle.id}
          </span>
          vehicle
        </h3>
        {canRemove && (
          <Button variant="ghost" size="sm" aria-label={`Remove ${vehicle.id}`}
                  onClick={() => onChange(removeVehicle(scene, vehicle.id))}>
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <Field label="kind">
          <Select value={vehicle.kind} options={KIND_OPTS}
                  onChange={(v) => onChange(patchVehicle(scene, vehicle.id, { kind: v as Vehicle["kind"] }))} />
        </Field>
        <Field label="colour">
          <Select value={vehicle.color} options={COLOR_OPTS}
                  onChange={(v) => onChange(patchVehicle(scene, vehicle.id, { color: v as Vehicle["color"] }))} />
        </Field>
      </div>

      <div className="mt-3 grid grid-cols-3 gap-3">
        <Field label="approach">
          <Select value={movement?.approach ?? "N"} options={APPROACH_OPTS}
                  onChange={(v) => onChange(patchMovement(scene, vehicle.id, { approach: v as NonNullable<typeof movement>["approach"] }))} />
        </Field>
        <Field label="maneuver">
          <Select value={movement?.maneuver ?? "straight"} options={MANEUVER_OPTS}
                  onChange={(v) => onChange(patchMovement(scene, vehicle.id, { maneuver: v as NonNullable<typeof movement>["maneuver"] }))} />
        </Field>
        <Field label="speed">
          <Select value={movement?.speed_band ?? "moderate"} options={SPEED_OPTS}
                  onChange={(v) => onChange(patchMovement(scene, vehicle.id, { speed_band: v as NonNullable<typeof movement>["speed_band"] }))} />
        </Field>
      </div>

      {/* Impact point (12-position clock) */}
      <div className="mt-4 rounded border border-steel-700 bg-steel-950/40 p-3">
        <p className="label-caps mb-2">struck at (impact point)</p>
        <ClockPicker
          value={struck}
          allowNone
          size={112}
          ariaLabel={`Impact clock position for ${vehicle.id}`}
          onChange={(clock) => onChange(setImpact(scene, vehicle.id, clock))}
        />
      </div>

      {/* Damage zones */}
      <div className="mt-4">
        <div className="mb-2 flex items-center justify-between">
          <p className="label-caps">damage zones</p>
          <Button variant="ghost" size="sm" onClick={() => onChange(addDamage(scene, vehicle.id))}>
            <Plus className="h-3.5 w-3.5" /> add
          </Button>
        </div>
        {vehicle.damage.length === 0 && (
          <p className="font-mono text-[11px] text-blueprint-dim">no damage recorded</p>
        )}
        <div className="space-y-3">
          {vehicle.damage.map((dz, i) => (
            <div key={i} className="rounded border border-steel-700 bg-steel-950/40 p-3">
              <div className="flex items-start justify-between gap-3">
                <ClockPicker
                  value={dz.clock_position}
                  size={96}
                  ariaLabel={`Damage ${i + 1} clock position for ${vehicle.id}`}
                  onChange={(clock) => onChange(patchDamage(scene, vehicle.id, i, { clock_position: clock ?? 12 }))}
                />
                <div className="flex-1 space-y-2">
                  <Field label="severity">
                    <Select value={dz.severity} options={SEVERITY_OPTS}
                            onChange={(v) => onChange(patchDamage(scene, vehicle.id, i, { severity: v as typeof dz.severity }))} />
                  </Field>
                  <input
                    aria-label={`Damage ${i + 1} note`}
                    value={dz.note ?? ""}
                    placeholder="note (optional)"
                    onChange={(e) => onChange(patchDamage(scene, vehicle.id, i, { note: e.target.value || null }))}
                    className="h-8 w-full rounded border border-steel-600 bg-steel-950/80 px-2 font-mono text-xs text-blueprint-text placeholder:text-blueprint-dim focus:border-cyan-400/70 focus-visible:outline-none"
                  />
                </div>
                <Button variant="ghost" size="icon" aria-label={`Remove damage ${i + 1}`}
                        onClick={() => onChange(removeDamage(scene, vehicle.id, i))}>
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function SequenceEditor({ scene, onChange }: { scene: Scene; onChange: (scene: Scene) => void }) {
  const active = new Set(scene.sequence);
  const toggle = (ev: (typeof SEQUENCE_EVENTS)[number]) => {
    const next = { ...scene };
    next.sequence = active.has(ev)
      ? scene.sequence.filter((x) => x !== ev)
      : [...scene.sequence, ev];
    onChange(next);
  };
  return (
    <section className="sheet p-4">
      <h3 className="label-caps mb-3">incident phases (optional)</h3>
      <div className="flex flex-wrap gap-2">
        {SEQUENCE_EVENTS.map((ev) => {
          const on = active.has(ev);
          return (
            <button
              key={ev}
              type="button"
              aria-pressed={on}
              onClick={() => toggle(ev)}
              className={
                "rounded border px-2.5 py-1 font-mono text-[11px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400 " +
                (on
                  ? "border-cyan-400/60 bg-cyan-400/10 text-cyan-200"
                  : "border-steel-700 text-blueprint-dim hover:border-steel-500")
              }
            >
              {humanize(ev)}
            </button>
          );
        })}
      </div>
    </section>
  );
}
