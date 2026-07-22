import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

// Blueprint buttons: sharp (rounded-md), monospace, instrument-panel feel.
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md font-mono text-sm font-medium transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/80 focus-visible:ring-offset-2 focus-visible:ring-offset-steel-950 disabled:pointer-events-none disabled:opacity-40 select-none",
  {
    variants: {
      variant: {
        primary:
          "bg-amber-400 text-steel-950 font-semibold shadow-glow-amber hover:bg-amber-300 active:scale-[0.98]",
        secondary:
          "border border-steel-600 bg-steel-800/70 text-blueprint-text hover:border-steel-500 hover:bg-steel-700/70 active:scale-[0.98]",
        outline:
          "border border-cyan-400/50 text-cyan-200 hover:bg-cyan-400/10 hover:border-cyan-400/80 active:scale-[0.98]",
        ghost: "text-blueprint-dim hover:bg-white/[0.04] hover:text-blueprint-text",
        danger:
          "border border-red-400/50 text-red-200 hover:bg-red-500/10 active:scale-[0.98]",
      },
      size: {
        sm: "h-8 px-3 text-xs",
        md: "h-10 px-5",
        lg: "h-12 px-7 text-base",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return <Comp ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
  },
);
Button.displayName = "Button";

export { buttonVariants };
