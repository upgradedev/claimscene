import { useRef, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  ImagePlus,
  Loader2,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react";
import { API_BASE } from "@/lib/api";
import { useExtract, useScenarios } from "@/lib/queries";
import { useCaseStore } from "@/store/useCaseStore";
import { cn } from "@/lib/utils";
import { Button } from "../ui/button";
import { Badge } from "../ui/badge";
import { Select, toOptions } from "../ui/fields";
import { ExtractProgress } from "../ExtractProgress";
import { StepHeading } from "./StepHeading";

const ROLE_OPTIONS = toOptions(["scene_photo", "damage_photo", "road_photo"], (r) =>
  ({ scene_photo: "scene", damage_photo: "damage", road_photo: "road" })[r] ?? r,
);

export function SourceStep() {
  const scenariosQuery = useScenarios();
  const extract = useExtract();

  const photos = useCaseStore((s) => s.photos);
  const scenario = useCaseStore((s) => s.scenario);
  const contextNote = useCaseStore((s) => s.contextNote);
  const addPhotos = useCaseStore((s) => s.addPhotos);
  const removePhoto = useCaseStore((s) => s.removePhoto);
  const setPhotoRole = useCaseStore((s) => s.setPhotoRole);
  const clearPhotos = useCaseStore((s) => s.clearPhotos);
  const chooseScenario = useCaseStore((s) => s.chooseScenario);
  const setContextNote = useCaseStore((s) => s.setContextNote);
  const loadExtraction = useCaseStore((s) => s.loadExtraction);
  const goTo = useCaseStore((s) => s.goTo);

  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const hasSource = !!scenario || photos.length > 0;

  const onExtract = () => {
    const req = scenario
      ? { scenarioId: scenario.id, context: contextNote || undefined }
      : {
          files: photos.map((p) => p.file),
          roles: photos.map((p) => p.role),
          context: contextNote || undefined,
        };
    extract.mutate(req, {
      onSuccess: (res) => {
        loadExtraction(res);
        goTo("review");
      },
    });
  };

  return (
    <div className="animate-fade-up">
      <StepHeading
        title="Start a case"
        subtitle="Pick a sample scenario to run the whole flow with zero photos, or upload your own accident photos. Either way, the AI only proposes a scene — you confirm it next."
      />

      {/* Sample scenarios — the zero-photo demo path (primary for reviewers). */}
      <section aria-labelledby="scenario-heading">
        <div className="mb-3 flex items-center justify-between">
          <h3 id="scenario-heading" className="label-caps">
            Try a sample scenario · no photos needed
          </h3>
          {scenario && (
            <Button variant="ghost" size="sm" onClick={() => chooseScenario(null)}>
              <X className="h-3.5 w-3.5" /> Clear
            </Button>
          )}
        </div>

        {scenariosQuery.isLoading && (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="h-28 animate-pulse rounded border border-steel-700 bg-steel-800/50" />
            ))}
          </div>
        )}
        {scenariosQuery.isError && (
          <p className="flex items-center gap-2 rounded border border-red-400/30 bg-red-500/5 p-3 text-sm text-red-300">
            <AlertCircle className="h-4 w-4" /> Couldn&apos;t load sample scenarios.
          </p>
        )}
        {scenariosQuery.data && (
          <ul className="grid grid-cols-2 gap-3 md:grid-cols-3">
            {scenariosQuery.data.map((s) => {
              const selected = scenario?.id === s.id;
              return (
                <li key={s.id}>
                  <button
                    type="button"
                    onClick={() => chooseScenario(selected ? null : s)}
                    aria-pressed={selected}
                    className={cn(
                      "group w-full overflow-hidden rounded border text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400",
                      selected
                        ? "border-amber-400 bg-amber-400/[0.06]"
                        : "border-steel-700 bg-steel-900/60 hover:border-cyan-400/50",
                    )}
                  >
                    <div className="relative aspect-video w-full bg-steel-850">
                      {s.thumbnail && (
                        <img
                          src={`${API_BASE}${s.thumbnail}`}
                          alt=""
                          loading="lazy"
                          className="h-full w-full object-cover opacity-90 transition-opacity group-hover:opacity-100"
                        />
                      )}
                      <span className="absolute right-1.5 top-1.5">
                        <Badge variant="muted">synthetic</Badge>
                      </span>
                    </div>
                    <div className="p-2.5">
                      <p className="font-mono text-sm font-semibold text-blueprint-text">{s.title}</p>
                      <p className="mt-0.5 line-clamp-2 text-[11px] text-blueprint-dim">{s.summary}</p>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {/* Upload path */}
      <section aria-labelledby="upload-heading" className="mt-8">
        <h3 id="upload-heading" className="label-caps mb-3">
          or upload your own accident photos
        </h3>
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            if (e.dataTransfer.files) addPhotos(Array.from(e.dataTransfer.files));
          }}
          className={cn(
            "flex flex-col items-center justify-center rounded border-2 border-dashed px-6 py-10 text-center transition-colors",
            dragOver ? "border-cyan-400 bg-cyan-400/[0.05]" : "border-steel-700 bg-steel-900/40 hover:border-steel-500",
          )}
        >
          <span className="grid h-12 w-12 place-items-center rounded border border-steel-600 bg-steel-800 text-cyan-300">
            <UploadCloud className="h-6 w-6" />
          </span>
          <p className="mt-3 text-sm text-blueprint-text">Drag &amp; drop photos, or</p>
          <Button
            variant="secondary"
            size="sm"
            // ≥44px tap target on mobile (WCAG 2.5.5); the compact 32px height
            // is kept from the `sm` breakpoint up, where a pointer is likelier.
            className="mt-2 h-11 px-4 sm:h-8 sm:px-3"
            onClick={() => inputRef.current?.click()}
          >
            <ImagePlus className="h-4 w-4" /> Browse files
          </Button>
          <p className="mt-3 font-mono text-xs text-blueprint-dim">
            JPG / PNG / WebP · photos are stored with your case, content-addressed, so it
            can be re-verified later
          </p>
          <input
            ref={inputRef}
            type="file"
            accept="image/*"
            multiple
            className="sr-only"
            aria-label="Choose accident photos"
            onChange={(e) => {
              if (e.target.files) addPhotos(Array.from(e.target.files));
              e.target.value = "";
            }}
          />
        </div>

        {photos.length > 0 && (
          <div className="mt-4">
            <div className="mb-2 flex items-center justify-between">
              <p className="font-mono text-xs text-blueprint-dim">
                <span className="text-blueprint-text">{photos.length}</span> photo(s) · tag each role
              </p>
              <Button variant="ghost" size="sm" onClick={clearPhotos}>
                <Trash2 className="h-3.5 w-3.5" /> Clear
              </Button>
            </div>
            <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              {photos.map((p) => (
                <li key={p.id} className="overflow-hidden rounded border border-steel-700 bg-steel-900/60">
                  <div className="relative aspect-video w-full bg-steel-850">
                    <img src={p.url} alt={p.name} className="h-full w-full object-cover" />
                    <button
                      type="button"
                      onClick={() => removePhoto(p.id)}
                      aria-label={`Remove ${p.name}`}
                      className="absolute right-1.5 top-1.5 grid h-6 w-6 place-items-center rounded-full bg-black/70 text-white/90 hover:bg-red-500"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                    <span className="absolute left-1.5 top-1.5">
                      <Badge variant="muted">uploaded</Badge>
                    </span>
                  </div>
                  <div className="p-2">
                    <Select
                      aria-label={`Role for ${p.name}`}
                      value={p.role}
                      onChange={(role) => setPhotoRole(p.id, role)}
                      options={ROLE_OPTIONS}
                    />
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {/* Context note */}
      <section className="mt-6">
        <label htmlFor="context-note" className="label-caps mb-1 block">
          context note (optional)
        </label>
        <textarea
          id="context-note"
          value={contextNote}
          onChange={(e) => setContextNote(e.target.value)}
          rows={2}
          placeholder="e.g. Both cars were heading south; the rear car failed to stop at the light."
          className="w-full rounded border border-steel-600 bg-steel-950/80 p-2.5 font-sans text-sm text-blueprint-text placeholder:text-blueprint-dim focus:border-cyan-400/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/70"
        />
      </section>

      {extract.isPending && <ExtractProgress isSample={!!scenario} />}

      {extract.isError && (
        <p role="alert" className="mt-4 flex items-center gap-2 rounded border border-red-400/30 bg-red-500/5 p-3 text-sm text-red-300">
          <AlertCircle className="h-4 w-4" /> {extract.error.message}
        </p>
      )}

      <div className="mt-8 flex items-center justify-between gap-4">
        <p className="max-w-sm font-mono text-xs text-blueprint-dim">
          {scenario
            ? `Sample "${scenario.title}" — offline returns the shipped ground-truth scene; live reads the photos with AI vision.`
            : "The extractor proposes a constrained scene graph. You review and adjust it next."}
        </p>
        <Button size="lg" disabled={!hasSource || extract.isPending} onClick={onExtract}>
          {extract.isPending ? (
            <>
              <Loader2 className="h-5 w-5 animate-spin" /> Extracting…
            </>
          ) : (
            <>
              Extract scene <ArrowRight className="h-5 w-5" />
            </>
          )}
        </Button>
      </div>
    </div>
  );
}
