import { useEffect, useRef, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { claimsceneApi } from "@/lib/api";
import type { Scene } from "@/lib/scene";
import { SchematicView } from "./SchematicView";

interface PreviewState {
  svg: string | null;
  warnings: string[];
  loading: boolean;
  error: string | null;
}

/** Live review feedback: whenever the reviewed scene changes, (debounced)
 *  POST it to /cases/preview-schematic and render the returned static
 *  schematic. A monotonic request id guards against a slow earlier response
 *  clobbering a newer one. This is the wiring that makes the review loop feel
 *  immediate — AI proposes, the human edits, the factual layer redraws. */
export function SchematicPreview({
  scene,
  debounceMs = 300,
}: {
  scene: Scene;
  debounceMs?: number;
}) {
  const [state, setState] = useState<PreviewState>({
    svg: null,
    warnings: [],
    loading: true,
    error: null,
  });
  const reqId = useRef(0);
  const key = JSON.stringify(scene);

  useEffect(() => {
    const id = (reqId.current += 1);
    setState((s) => ({ ...s, loading: true, error: null }));
    const timer = window.setTimeout(() => {
      claimsceneApi
        .previewSchematic(scene)
        .then((res) => {
          if (id === reqId.current)
            setState({ svg: res.svg, warnings: res.warnings, loading: false, error: null });
        })
        .catch((err: unknown) => {
          if (id === reqId.current)
            setState((s) => ({
              ...s,
              loading: false,
              error: err instanceof Error ? err.message : "preview failed",
            }));
        });
    }, debounceMs);
    return () => window.clearTimeout(timer);
    // `key` fingerprints the scene; re-runs on any edit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, debounceMs]);

  return (
    <div>
      <SchematicView
        svg={state.svg}
        loading={state.loading && !state.svg}
        caption="Redraws as you edit · watermarked · not evidence"
      />
      {state.error && (
        <p role="alert" className="mt-2 flex items-center gap-1.5 font-mono text-[11px] text-red-300">
          <AlertTriangle className="h-3.5 w-3.5" /> preview unavailable: {state.error}
        </p>
      )}
      {state.warnings.length > 0 && (
        <ul className="mt-2 space-y-1">
          {state.warnings.map((w, i) => (
            <li
              key={i}
              className="flex items-start gap-1.5 rounded border border-amber-400/25 bg-amber-400/[0.06] px-2 py-1 font-mono text-[11px] text-amber-200"
            >
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-amber-400" />
              {w}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
