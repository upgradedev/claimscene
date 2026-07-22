import { Check } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Step } from "@/store/useCaseStore";

const STEPS: { key: Step; label: string; n: string }[] = [
  { key: "source", label: "Source", n: "01" },
  { key: "review", label: "Review & adjust", n: "02" },
  { key: "render", label: "Render", n: "03" },
  { key: "result", label: "Sealed case", n: "04" },
];

const ORDER: Step[] = ["source", "review", "render", "result"];

export function Stepper({ current }: { current: Step }) {
  const idx = ORDER.indexOf(current);
  return (
    <ol className="mb-8 flex items-center gap-2 font-mono text-xs sm:gap-3">
      {STEPS.map((s, i) => {
        const done = i < idx;
        const active = i === idx;
        return (
          <li key={s.key} className="flex flex-1 items-center gap-2">
            <span
              className={cn(
                "grid h-7 w-7 shrink-0 place-items-center rounded border text-[11px] transition-colors",
                active && "border-amber-400 bg-amber-400/15 text-amber-300",
                done && "border-cyan-400/60 bg-cyan-400/10 text-cyan-300",
                !active && !done && "border-steel-700 text-blueprint-dim",
              )}
            >
              {done ? <Check className="h-3.5 w-3.5" /> : s.n}
            </span>
            <span
              className={cn(
                "hidden truncate sm:block",
                active ? "text-blueprint-text" : "text-blueprint-dim",
              )}
            >
              {s.label}
            </span>
            {i < STEPS.length - 1 && (
              <span className={cn("h-px flex-1", done ? "bg-cyan-400/40" : "bg-steel-700")} />
            )}
          </li>
        );
      })}
    </ol>
  );
}
