import { cn } from "@/lib/utils";

/** The ClaimScene mark: a survey crosshair inside a corner-ticked frame — a
 *  top-down datum point, monospace wordmark. Deliberately instrument-panel,
 *  not the cinema clapperboard of our sibling app. */
export function Wordmark({ className }: { className?: string }) {
  return (
    <div className={cn("flex items-center gap-2.5", className)}>
      <span className="relative grid h-9 w-9 place-items-center rounded border border-cyan-400/40 bg-steel-900">
        <svg viewBox="0 0 24 24" className="h-5 w-5" aria-hidden>
          <circle cx="12" cy="12" r="6.5" fill="none" stroke="#38bdf8" strokeWidth="1.4" />
          <line x1="12" y1="1.5" x2="12" y2="7" stroke="#d9a441" strokeWidth="1.4" />
          <line x1="12" y1="17" x2="12" y2="22.5" stroke="#d9a441" strokeWidth="1.4" />
          <line x1="1.5" y1="12" x2="7" y2="12" stroke="#d9a441" strokeWidth="1.4" />
          <line x1="17" y1="12" x2="22.5" y2="12" stroke="#d9a441" strokeWidth="1.4" />
          <circle cx="12" cy="12" r="1.6" fill="#ffcc00" />
        </svg>
      </span>
      <span className="font-mono text-lg font-semibold tracking-tight text-blueprint-text">
        Claim<span className="text-amber-glow">Scene</span>
      </span>
    </div>
  );
}
