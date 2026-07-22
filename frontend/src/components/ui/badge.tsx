import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded border px-2 py-0.5 font-mono text-[11px] font-medium tracking-wide transition-colors",
  {
    variants: {
      variant: {
        amber: "border-amber-400/30 bg-amber-400/10 text-amber-200",
        verified: "border-cyan-400/30 bg-cyan-400/10 text-cyan-200",
        neutral: "border-steel-600 bg-steel-800/60 text-blueprint-text",
        muted: "border-steel-700 bg-transparent text-blueprint-dim",
        danger: "border-red-400/40 bg-red-500/10 text-red-300",
      },
    },
    defaultVariants: { variant: "neutral" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
