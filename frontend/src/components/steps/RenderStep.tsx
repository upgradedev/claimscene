import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, Check, Loader2 } from "lucide-react";
import { usePollRenderJob, useSubmitRenderJob } from "@/lib/queries";
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
  const proposedScene = useCaseStore((s) => s.proposedScene);
  const scenario = useCaseStore((s) => s.scenario);
  const photos = useCaseStore((s) => s.photos);
  const caseId = useCaseStore((s) => s.caseId);
  const setResult = useCaseStore((s) => s.setResult);
  const goTo = useCaseStore((s) => s.goTo);
  const submitJob = useSubmitRenderJob();
  const fired = useRef(false);
  const [phase, setPhase] = useState(0);
  // Async submit + poll: a real render can take minutes (an establish-shot
  // still, then an image-to-video clip chained from it — see
  // claimscene.api's module docstring), longer than the Firebase Hosting
  // proxy cap, so this submits a background job (POST /cases/render/jobs)
  // instead of blocking one request. `jobId` is reset to null on every fresh
  // attempt so a retry's stale poll result/error never lingers even a beat.
  const [jobId, setJobId] = useState<string | null>(null);
  const poll = usePollRenderJob(jobId);

  const start = useCallback(() => {
    if (!scene) return;
    setPhase(0);
    setJobId(null);
    // Seal the AI→human approval receipt: send the frozen AI proposal + an honest
    // classification. The public demo is unauthenticated, so `interactive_demo`
    // is the truthful label (the server also enforces this).
    const review = {
      proposedScene: proposedScene ?? undefined,
      reviewClassification: "interactive_demo",
    };
    submitJob.mutate(
      scenario
        ? { scene, caseId, scenarioId: scenario.id, ...review }
        : { scene, caseId, files: photos.map((p) => p.file), roles: photos.map((p) => p.role), ...review },
      { onSuccess: (data) => setJobId(data.job_id) },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scene, proposedScene, scenario, photos, caseId]);

  useEffect(() => {
    if (fired.current) return;
    fired.current = true;
    start();
  }, [start]);

  // The polled job landed a sealed case — hand off to the store, which also
  // flips the wizard to the result step. Offline/demo generation can finish
  // near-instantly, so this may fire from the very first poll tick.
  useEffect(() => {
    if (!poll.result) return;
    setResult(poll.result);
  }, [poll.result, setResult]);

  const isSuccess = poll.result !== null;
  const error: Error | null = submitJob.isError ? submitJob.error : poll.error;

  // Cosmetic staged feedback while the real request is in flight.
  useEffect(() => {
    if (error || isSuccess) return;
    const t = window.setInterval(() => setPhase((p) => Math.min(p + 1, STAGES.length - 1)), 1400);
    return () => window.clearInterval(t);
  }, [error, isSuccess]);

  return (
    <div className="animate-fade-up py-8">
      <div className="mx-auto max-w-lg">
        <div className={cn("sheet sheet-ticks p-6", !error && "scanline")}>
          <h2 className="font-mono text-xl font-semibold text-blueprint-text">
            {error ? "Render failed" : "Sealing your case"}
          </h2>
          <p className="mt-1 text-sm text-blueprint-dim">
            The schematic is deterministic; the illustration is generative and disclosed.
          </p>

          {!error && (
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

          {error && (
            <div className="mt-4">
              <p role="alert" className="flex items-center gap-2 rounded border border-red-400/30 bg-red-500/5 p-3 text-sm text-red-300">
                <AlertCircle className="h-4 w-4" /> {error.message}
              </p>
              <div className="mt-3 flex gap-2">
                <Button
                  size="sm"
                  onClick={() => {
                    submitJob.reset();
                    start();
                  }}
                >
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
