import { Loader2, Ruler } from "lucide-react";
import { Badge } from "./ui/badge";
import { cn, svgToDataUrl } from "@/lib/utils";

/** The deterministic top-down schematic — the FACTUAL layer.
 *
 *  The server-produced SVG is rendered through an <img src="data:image/svg+xml…">
 *  (never injected into the DOM), so it can never execute script even if a
 *  hostile string slipped past the backend's text-escape. */
export function SchematicView({
  svg,
  loading = false,
  caption,
  className,
}: {
  svg: string | null;
  loading?: boolean;
  caption?: string;
  className?: string;
}) {
  return (
    <figure
      className={cn(
        "sheet sheet-ticks overflow-hidden",
        loading && "scanline",
        className,
      )}
    >
      <div className="flex items-center justify-between border-b border-steel-700/70 px-3 py-1.5">
        <span className="label-caps flex items-center gap-1.5">
          <Ruler className="h-3 w-3 text-cyan-400" /> Factual layer · top-down schematic
        </span>
        <Badge variant="amber">deterministic</Badge>
      </div>
      <div className="relative aspect-[4/3] w-full bg-steel-850">
        {svg ? (
          <img
            src={svgToDataUrl(svg)}
            alt="Top-down accident schematic derived from the constrained scene graph"
            className="h-full w-full object-contain"
          />
        ) : (
          <div className="grid h-full w-full place-items-center text-blueprint-dim">
            {loading ? (
              <span className="flex items-center gap-2 font-mono text-xs">
                <Loader2 className="h-4 w-4 animate-spin" /> plotting schematic…
              </span>
            ) : (
              <span className="font-mono text-xs">the schematic will render here</span>
            )}
          </div>
        )}
      </div>
      {caption && (
        <figcaption className="border-t border-steel-700/70 px-3 py-1.5 font-mono text-[11px] text-blueprint-dim">
          {caption}
        </figcaption>
      )}
    </figure>
  );
}
