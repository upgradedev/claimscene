import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, Check, Loader2 } from "lucide-react";
import { useRender } from "@/lib/queries";
import { useCaseStore } from "@/store/useCaseStore";
import { Button } from "../ui/button";
import { cn } from "@/lib/utils";

const STAGES = [
  "Plotting the deterministic schematic (factual layer)",
  "Generating the disclosed AI illustration",
  "Sealing the manifest — SHA-256 provenance",
];

export function RenderStep() {
  const scene = useCaseStore((s) => s.scene);
  const scenario = useCaseStore((s) => s.scenario);
  const photos = useCaseStore((s) => s.photos);
  const caseId = useCaseStore((s) => s.caseId);
  const setResult = useCaseStore((s) => s.setResult);
  const goTo = useCaseStore((s) => s.goTo);
  const render = useRender();
  const fired = useRef(false);
  const [phase, setPhase] = useState(0);

  const start = useCallback(() => {
    if (!scene) return;
    setPhase(0);
    render.mutate(
      scenario
        ? { scene, caseId, scenarioId: scenario.id }
        : { scene, caseId, files: photos.map((p) => p.file), roles: photos.map((p) => p.role) },
      { onSuccess: (res) => setResult(res) },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scene, scenario, photos, caseId, setResult]);

  useEffect(() => {
    if (fired.current) return;
    fired.current = true;
    start();
  }, [start]);

  // Cosmetic staged feedback while the single real request is in flight.
  useEffect(() => {
    if (render.isError || render.isSuccess) return;
    const t = window.setInterval(() => setPhase((p) => Math.min(p + 1, STAGES.length - 1)), 1400);
    return () => window.clearInterval(t);
  }, [render.isError, render.isSuccess]);

  return (
    <div className="animate-fade-up py-8">
      <div className="mx-auto max-w-lg">
        <div className={cn("sheet sheet-ticks p-6", !render.isError && "scanline")}>
          <h2 className="font-mono text-xl font-semibold text-blueprint-text">
            {render.isError ? "Render failed" : "Sealing your case"}
          </h2>
          <p className="mt-1 text-sm text-blueprint-dim">
            The schematic is deterministic; the illustration is generative and disclosed.
          </p>

          {!render.isError && (
            <ol className="mt-5 space-y-2.5">
              {STAGES.map((label, i) => {
                const done = i < phase;
                const active = i === phase;
                return (
                  <li key={i} className="flex items-center gap-3 font-mono text-sm">
                    <span
                      className={cn(
                        "grid h-6 w-6 place-items-center rounded-full border",
                        done && "border-cyan-400/60 bg-cyan-400/10 text-cyan-300",
                        active && "border-amber-400 text-amber-300",
                        !done && !active && "border-steel-700 text-blueprint-dim",
                      )}
                    >
                      {done ? <Check className="h-3.5 w-3.5" /> : active ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : i + 1}
                    </span>
                    <span className={done || active ? "text-blueprint-text" : "text-blueprint-dim"}>
                      {label}
                    </span>
                  </li>
                );
              })}
            </ol>
          )}

          {render.isError && (
            <div className="mt-4">
              <p role="alert" className="flex items-center gap-2 rounded border border-red-400/30 bg-red-500/5 p-3 text-sm text-red-300">
                <AlertCircle className="h-4 w-4" /> {render.error.message}
              </p>
              <div className="mt-3 flex gap-2">
                <Button size="sm" onClick={() => { render.reset(); start(); }}>
                  Retry
                </Button>
                <Button variant="ghost" size="sm" onClick={() => goTo("review")}>
                  Back to review
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
