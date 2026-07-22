import * as React from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

export function Field({
  label,
  hint,
  htmlFor,
  children,
  className,
}: {
  label: string;
  hint?: string;
  htmlFor?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <label htmlFor={htmlFor} className={cn("block", className)}>
      <span className="label-caps mb-1 block">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-blueprint-dim">{hint}</span>}
    </label>
  );
}

export interface Option {
  value: string;
  label: string;
}

/** A native <select> styled as a blueprint control — every review edit is
 *  constrained to a closed option set (dropdowns, never free text). */
export function Select({
  value,
  onChange,
  options,
  "aria-label": ariaLabel,
  id,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  options: readonly Option[];
  "aria-label"?: string;
  id?: string;
  className?: string;
}) {
  return (
    <div className={cn("relative", className)}>
      <select
        id={id}
        aria-label={ariaLabel}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-9 w-full appearance-none rounded border border-steel-600 bg-steel-950/80 px-2.5 pr-8 font-mono text-sm text-blueprint-text transition-colors hover:border-steel-500 focus:border-cyan-400/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/70"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value} className="bg-steel-900 text-blueprint-text">
            {o.label}
          </option>
        ))}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-4 w-4 -translate-y-1/2 text-blueprint-dim" />
    </div>
  );
}

/** Build Select options from a const enum tuple + a humanizer. */
export function toOptions(values: readonly string[], humanize: (v: string) => string): Option[] {
  return values.map((v) => ({ value: v, label: humanize(v) }));
}
