import { cn } from "@/lib/utils";

// Where each clock position sits, as (left%, tops%) on a square face.
// 12 = front (top), 3 = right, 6 = rear (bottom), 9 = left.
function pos(clock: number): { left: number; top: number } {
  const angle = ((clock % 12) * 30 * Math.PI) / 180; // 0 at top, clockwise
  const r = 42; // % radius
  return {
    left: 50 + r * Math.sin(angle),
    top: 50 - r * Math.cos(angle),
  };
}

const MEANING: Record<number, string> = {
  12: "front", 3: "right side", 6: "rear", 9: "left side",
};

/** A 12-position clock face for locating damage / impact on a vehicle body.
 *  Constrained by construction — no free-text coordinates ever reach the
 *  scene. Optional "none" clears the value (used for the struck-toggle). */
export function ClockPicker({
  value,
  onChange,
  ariaLabel,
  allowNone = false,
  size = 128,
}: {
  value: number | null;
  onChange: (clock: number | null) => void;
  ariaLabel: string;
  allowNone?: boolean;
  size?: number;
}) {
  return (
    <div className="flex items-center gap-3">
      <div
        role="radiogroup"
        aria-label={ariaLabel}
        className="relative shrink-0"
        style={{ width: size, height: size }}
      >
        {/* Face: a top-down car body glyph with a front marker. */}
        <svg viewBox="0 0 100 100" className="absolute inset-0 h-full w-full" aria-hidden>
          <circle cx="50" cy="50" r="46" fill="none" stroke="#17303d" strokeWidth="1" />
          <rect x="38" y="26" width="24" height="48" rx="7" fill="#122430" stroke="#4a6b80" strokeWidth="1.2" />
          <path d="M42 30 L50 24 L58 30 Z" fill="#cfe3ee" />
          <line x1="50" y1="50" x2="50" y2="30" stroke="#2a4a5c" strokeWidth="1" />
        </svg>
        {Array.from({ length: 12 }, (_, k) => k + 1).map((clock) => {
          const { left, top } = pos(clock);
          const selected = value === clock;
          return (
            <button
              key={clock}
              type="button"
              role="radio"
              aria-checked={selected}
              aria-label={`${clock} o'clock${MEANING[clock] ? ` (${MEANING[clock]})` : ""}`}
              onClick={() => onChange(selected && allowNone ? null : clock)}
              style={{ left: `${left}%`, top: `${top}%` }}
              className={cn(
                "absolute grid h-6 w-6 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border font-mono text-[10px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400",
                selected
                  ? "border-amber-400 bg-amber-400 font-bold text-steel-950 shadow-glow-amber"
                  : "border-steel-600 bg-steel-900 text-blueprint-dim hover:border-cyan-400/60 hover:text-cyan-200",
              )}
            >
              {clock}
            </button>
          );
        })}
      </div>
      <div className="min-w-[64px] font-mono text-xs">
        {value ? (
          <>
            <span className="text-amber-300">{value} o&apos;clock</span>
            {MEANING[value] && <span className="block text-blueprint-dim">{MEANING[value]}</span>}
          </>
        ) : (
          <span className="text-blueprint-dim">{allowNone ? "not struck" : "—"}</span>
        )}
      </div>
    </div>
  );
}
