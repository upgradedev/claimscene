import { motion } from "framer-motion";
import { ArrowRight, ShieldCheck, SlidersHorizontal, Sparkles } from "lucide-react";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
// A downscaled crop of a live app capture of the AI illustration panel
// (demo/assets/claimscene-11-result-illustration.png), the disclosed
// illustration layer, shown as a real example, not described.
import heroIllustration from "@/assets/hero-illustration.jpg";

export function Hero({ onStart }: { onStart: () => void }) {
  return (
    <section className="container max-w-5xl py-16 md:py-24">
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
        className="text-center"
      >
        <Badge variant="verified" className="mx-auto">
          <ShieldCheck className="h-3.5 w-3.5" /> honest accident documentation &amp; illustration
        </Badge>
        <h1 className="mt-5 font-mono text-4xl font-bold leading-tight text-blueprint-text md:text-6xl">
          Two layers.<br />
          <span className="text-amber-glow">One verifiable seal.</span>
        </h1>
        <p className="mx-auto mt-5 max-w-2xl text-balance text-blueprint-dim md:text-lg">
          A deterministic top-down schematic is the <span className="text-blueprint-text">factual layer</span>.
          The generative clip is a <span className="text-blueprint-text">disclosed illustration</span> — never
          evidence. Everything, including each input photo&apos;s source, is sealed with
          verifiable SHA-256 provenance.
        </p>
        <p className="mx-auto mt-4 max-w-xl font-mono text-xs text-blueprint-dim">
          Built for <span className="text-blueprint-text">insurers</span>,{" "}
          <span className="text-blueprint-text">claims adjusters</span>,{" "}
          <span className="text-blueprint-text">fleet-safety teams</span>, and the{" "}
          <span className="text-blueprint-text">claimants</span> who photograph the scene.
        </p>
        <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Button size="lg" onClick={onStart}>
            Start a case <ArrowRight className="h-5 w-5" />
          </Button>
          <Button variant="outline" size="lg" onClick={onStart}>
            <Sparkles className="h-4 w-4" /> Try a sample scenario
          </Button>
        </div>
        <p className="mt-3 font-mono text-xs text-blueprint-dim">
          No photos needed — run the whole flow on a committed synthetic scenario.
        </p>
      </motion.div>

      {/* The two layers, SHOWN: the same accident as a deterministic schematic
          and as a clearly-labelled AI illustration. */}
      <motion.section
        aria-labelledby="two-layers-heading"
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, delay: 0.15 }}
        className="mt-16"
      >
        <h2
          id="two-layers-heading"
          className="text-center font-mono text-sm font-semibold uppercase tracking-[0.15em] text-blueprint-dim"
        >
          The two layers, side by side
        </h2>
        <p className="mx-auto mt-2 max-w-2xl text-balance text-center text-sm text-blueprint-dim">
          The same rear-end collision, both ways: a deterministic top-down schematic (the
          factual layer) and a disclosed AI illustration (never evidence).
        </p>
        <div className="mt-6 grid gap-4 md:grid-cols-2">
          <FactualCard />
          <IllustrationCard />
        </div>
      </motion.section>

      {/* How it works / why you can trust it. */}
      <section aria-labelledby="trust-heading" className="mt-16">
        <h2
          id="trust-heading"
          className="text-center font-mono text-sm font-semibold uppercase tracking-[0.15em] text-blueprint-dim"
        >
          Why you can trust it
        </h2>
        <div className="mt-6 grid gap-4 sm:grid-cols-3">
          <Pillar
            icon={<SlidersHorizontal className="h-4 w-4 text-cyan-400" />}
            title="You stay in control"
            body="The AI proposes a constrained scene; you review and adjust every field before anything is sealed."
          />
          <Pillar
            icon={<span className="font-mono text-cyan-400">/12</span>}
            title="No hallucinated coordinates"
            body="Damage and impact are placed on a 12-position clock. The layout is derived by a deterministic engine."
          />
          <Pillar
            icon={<ShieldCheck className="h-4 w-4 text-cyan-400" />}
            title="Verifiable in your browser"
            body="Recompute the sealed SHA-256 client-side — including every input photo's declared source."
          />
        </div>
      </section>
    </section>
  );
}

function FactualCard() {
  return (
    <figure className="sheet sheet-ticks overflow-hidden">
      <figcaption className="flex items-center justify-between border-b border-steel-700/70 px-3 py-1.5">
        <span className="label-caps">factual layer · deterministic</span>
        <Badge variant="amber">schematic</Badge>
      </figcaption>
      <div className="aspect-[16/10] w-full bg-steel-850 p-4">
        <svg viewBox="0 0 200 120" className="h-full w-full" role="img" aria-label="Top-down schematic of the rear-end collision: a blue car struck from behind by a red car at a traffic light">
          <defs>
            <pattern id="g" width="10" height="10" patternUnits="userSpaceOnUse">
              <path d="M10 0H0V10" fill="none" stroke="#17303d" strokeWidth="0.5" />
            </pattern>
          </defs>
          <rect width="200" height="120" fill="url(#g)" />
          <rect x="84" y="0" width="32" height="120" fill="#16242e" />
          <rect x="0" y="46" width="200" height="28" fill="#16242e" />
          <line x1="100" y1="0" x2="100" y2="120" stroke="#d9a441" strokeWidth="1" strokeDasharray="5 3" />
          <line x1="0" y1="60" x2="200" y2="60" stroke="#d9a441" strokeWidth="1" strokeDasharray="5 3" />
          <rect x="70" y="52" width="20" height="10" rx="2" fill="#3d7dd8" stroke="#0e1a22" />
          <rect x="96" y="40" width="10" height="20" rx="2" fill="#d1495b" stroke="#0e1a22" />
          <circle cx="94" cy="58" r="3" fill="#ffcc00" />
          <text x="100" y="112" fill="#7a93a4" fontSize="7" textAnchor="middle" fontFamily="monospace">
            ILLUSTRATION — NOT EVIDENCE
          </text>
        </svg>
      </div>
    </figure>
  );
}

function IllustrationCard() {
  return (
    <figure className="sheet overflow-hidden">
      <figcaption className="flex items-center justify-between border-b border-steel-700/70 px-3 py-1.5">
        <span className="label-caps">generative · disclosed</span>
        <Badge variant="danger">not evidence</Badge>
      </figcaption>
      <div className="relative aspect-[16/10] w-full overflow-hidden bg-steel-850">
        <img
          src={heroIllustration}
          width={720}
          height={450}
          loading="lazy"
          alt="AI illustration panel from a live case: a top-down CGI render of a blue car and a red car side by side at a traffic light, captioned 'AI-generated illustration - not evidence'."
          className="h-full w-full object-cover"
        />
        <div className="absolute left-0 right-0 top-0 flex items-center justify-center gap-1.5 bg-gradient-to-b from-black/80 to-transparent py-1.5 font-mono text-[10px] font-semibold tracking-wide text-amber-200">
          AI ILLUSTRATION — NOT EVIDENCE
        </div>
      </div>
    </figure>
  );
}

function Pillar({ icon, title, body }: { icon: React.ReactNode; title: string; body: string }) {
  return (
    <div className="sheet p-4">
      <div className="flex items-center gap-2">
        <span className="grid h-7 w-7 place-items-center rounded border border-steel-600 bg-steel-800">{icon}</span>
        <h3 className="font-mono text-sm font-semibold text-blueprint-text">{title}</h3>
      </div>
      <p className="mt-2 text-xs text-blueprint-dim">{body}</p>
    </div>
  );
}
