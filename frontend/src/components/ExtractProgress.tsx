import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

/** Format a running second count as `Ns` under a minute, else `M:SS`. */
export function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = String(seconds % 60).padStart(2, "0");
  return `${m}:${s}`;
}

/**
 * Honest in-flight feedback for the extract step. Extraction can be slow — the
 * live VLM ladder takes up to ~a minute — and a static "Extracting…" gives no
 * signal that anything is happening. This shows an elapsed timer, an
 * indeterminate progress bar, and expected-duration copy, and adapts its
 * message to the source: a committed sample resolves to its shipped
 * ground-truth scene (usually instant), while uploads run the real VLM ladder.
 */
export function ExtractProgress({ isSample }: { isSample: boolean }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const start = Date.now();
    const id = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - start) / 1000)),
      500,
    );
    return () => window.clearInterval(id);
  }, []);

  return (
    <div
      role="status"
      aria-live="polite"
      className="mt-4 rounded border border-cyan-400/25 bg-cyan-400/[0.04] p-3"
    >
      <div className="flex items-center justify-between gap-3 font-mono text-xs">
        <span className="flex items-center gap-2 text-cyan-100">
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-cyan-300" aria-hidden />
          {isSample ? "Loading the committed ground-truth scene…" : "Extracting the scene…"}
        </span>
        <span className="tabular-nums text-blueprint-dim" aria-label={`elapsed ${elapsed} seconds`}>
          {formatElapsed(elapsed)}
        </span>
      </div>

      {/* Indeterminate bar — a sliding segment (static under reduced motion). */}
      <div
        className="mt-2.5 h-1.5 w-full overflow-hidden rounded-full bg-steel-800"
        role="progressbar"
        aria-label="Extraction in progress"
      >
        <div className="h-full w-1/3 rounded-full bg-cyan-400/70 motion-safe:animate-indeterminate" />
      </div>

      <p className="mt-2 text-[11px] text-blueprint-dim">
        {isSample
          ? "Samples return the shipped ground-truth scene offline, so this is usually instant. On a live deployment the AI reads the photos instead."
          : "Reading your photos with AI vision. This can take up to about a minute. You confirm every field on the next step."}
      </p>
    </div>
  );
}
