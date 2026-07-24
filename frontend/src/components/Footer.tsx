import { ShieldCheck } from "lucide-react";

export function Footer() {
  return (
    <footer className="border-t border-steel-700/60 py-8">
      <div className="container flex flex-col items-center justify-between gap-3 text-xs text-blueprint-dim sm:flex-row">
        <p className="font-mono">
          © {new Date().getFullYear()} ClaimScene ·{" "}
          <a
            href="https://github.com/upgradedev/claimscene"
            target="_blank"
            rel="noreferrer"
            // ≥44px tap target on mobile; collapses back to an inline link from
            // `sm` up so the footer line reads normally on wider screens.
            className="inline-flex min-h-11 items-center underline decoration-steel-600 underline-offset-2 transition-colors hover:text-blueprint-text sm:min-h-0"
          >
            github.com/upgradedev/claimscene
          </a>
        </p>
        <p className="inline-flex items-center gap-1.5 font-mono">
          <ShieldCheck className="h-3.5 w-3.5 text-cyan-400/70" />
          Every case sealed with verifiable SHA-256 provenance.
        </p>
      </div>
    </footer>
  );
}
