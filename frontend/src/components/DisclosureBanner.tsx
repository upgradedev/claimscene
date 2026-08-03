import { AlertTriangle } from "lucide-react";

/** The persistent, unmissable honesty line — the product's ethical spine.
 *  Present at the top level on every screen: the generative clip is an
 *  illustration, never evidence; the schematic is the factual layer. */
export function DisclosureBanner() {
  return (
    <div
      role="region"
      aria-label="AI-content disclosure"
      // The guided tour's last step ("what sealed does not mean") rings this.
      data-tour="disclosure"
      // OPAQUE warm-dark bar (not a 7%-alpha tint): the ethics line is the
      // product's spine, so it must render deterministically — a translucent
      // background over the page's grid image left its contrast unresolvable by
      // automated checkers. Cream on #1c1608 is 13.3:1 (subcopy 7.1:1), well
      // past WCAG AA, and 12px is comfortably readable.
      className="border-b border-amber-400/40 bg-[#1c1608]"
    >
      <div className="container flex items-center gap-2.5 py-2 text-xs font-mono text-amber-200">
        <AlertTriangle className="h-4 w-4 shrink-0 text-amber-400" aria-hidden />
        <p className="tracking-wide">
          <span className="font-semibold">Illustrations are AI-generated — NOT EVIDENCE.</span>{" "}
          <span className="text-amber-200/70">
            The top-down schematic is the deterministic factual layer; the
            generative clip is a disclosed illustration.
          </span>
        </p>
      </div>
    </div>
  );
}
