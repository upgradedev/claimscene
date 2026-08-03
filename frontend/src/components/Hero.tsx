import { useState } from "react";
import { motion } from "framer-motion";
import { ArrowRight, Compass, ShieldCheck, SlidersHorizontal, Sparkles } from "lucide-react";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { GuidedTour, hasSeenTour } from "./GuidedTour";
// A frame of the illustration clip from a live render of the
// `s05_t_intersection` scenario, taken at t=1.25s and fitted (not cropped)
// into the panel, so the stop sign and the side road stay in shot. Rendered
// after the road-contrast fix, which is why the carriageway, its kerbs and
// the lane markings are legible at all: before it, the seed's road was a
// 1.11:1 contrast ratio against its own background and the clip had no
// surface to keep the vehicles on.
//
// FactualCard below draws the SAME collision by hand. Whenever this image is
// replaced, that SVG, this section's copy and both alt texts have to move
// with it, or the page shows two layers disagreeing about what happened,
// which is the exact failure the product exists to prevent.
import heroIllustration from "@/assets/hero-illustration.jpg";

export function Hero({ onStart }: { onStart: () => void }) {
  const [tourOpen, setTourOpen] = useState(false);
  // Read once, on mount. A visitor who has already taken or left the tour keeps
  // the button but loses the first-visit nudge, so nothing nags on every visit.
  const [tourSeen, setTourSeen] = useState(hasSeenTour);

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
        <h1
          data-tour="what-it-is"
          className="mt-5 font-mono text-4xl font-bold leading-tight text-blueprint-text md:text-6xl"
        >
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
        {/* `flex-wrap` matters: three lg buttons on one line overflow a 768px
            container, and the responsive gate measures exactly that. */}
        <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row sm:flex-wrap">
          <Button size="lg" onClick={onStart}>
            Start a case <ArrowRight className="h-5 w-5" />
          </Button>
          <Button variant="outline" size="lg" onClick={onStart}>
            <Sparkles className="h-4 w-4" /> Try a sample scenario
          </Button>
          <Button variant="secondary" size="lg" onClick={() => setTourOpen(true)}>
            <Compass className="h-4 w-4" /> Take the guided tour
          </Button>
        </div>
        <p className="mt-3 font-mono text-xs text-blueprint-dim">
          No photos needed — run the whole flow on a committed synthetic scenario.
        </p>
        {!tourSeen && (
          <p className="mt-1.5 font-mono text-xs text-cyan-300">
            New here? The tour takes about a minute.
          </p>
        )}
      </motion.div>

      <GuidedTour
        open={tourOpen}
        onClose={() => {
          setTourOpen(false);
          setTourSeen(true);
        }}
        onStartCase={onStart}
      />

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
          The same collision, both ways: a red truck pulls out of the side road at a stop
          sign and is struck by a white car on the main road. On the left, a deterministic
          top-down schematic (the factual layer). On the right, a disclosed AI illustration
          (never evidence).
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
            tourId="you-confirm"
            icon={<SlidersHorizontal className="h-4 w-4 text-cyan-400" />}
            title="You stay in control"
            body="The AI proposes a constrained scene; you review and adjust every field before anything is sealed."
          />
          <Pillar
            tourId="no-coordinates"
            icon={<span className="font-mono text-cyan-400">/12</span>}
            title="No hallucinated coordinates"
            body="Damage and impact are placed on a 12-position clock. The layout is derived by a deterministic engine."
          />
          <Pillar
            tourId="verify"
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
    <figure data-tour="factual-layer" className="sheet sheet-ticks overflow-hidden">
      <figcaption className="flex items-center justify-between border-b border-steel-700/70 px-3 py-1.5">
        <span className="label-caps">factual layer · deterministic</span>
        <Badge variant="amber">schematic</Badge>
      </figcaption>
      <div className="aspect-[16/10] w-full bg-steel-850 p-4">
        {/* The SAME collision the illustration beside it shows: a T-junction,
            the red truck out of the south arm turning right, the white car
            along the main road from the west, contact at the mouth of the
            junction, stop sign on the corner. Hand-drawn rather than a live
            render because this is a landing page, but it has to agree with
            the picture next to it, so it is checked against that image and
            against this section's copy whenever either changes. */}
        <svg viewBox="0 0 200 120" className="h-full w-full" role="img" aria-label="Top-down schematic of the collision: a red truck pulling out of a side road at a stop sign, struck on its front by a white car travelling along the main road from the left">
          <defs>
            <pattern id="g" width="10" height="10" patternUnits="userSpaceOnUse">
              <path d="M10 0H0V10" fill="none" stroke="#17303d" strokeWidth="0.5" />
            </pattern>
          </defs>
          <rect width="200" height="120" fill="url(#g)" />
          {/* main road, east-west; and the side road, south only: a T, not a cross */}
          <rect x="0" y="46" width="200" height="28" fill="#16242e" />
          <rect x="84" y="60" width="32" height="60" fill="#16242e" />
          <line x1="0" y1="60" x2="200" y2="60" stroke="#d9a441" strokeWidth="1" strokeDasharray="5 3" />
          <line x1="100" y1="74" x2="100" y2="120" stroke="#d9a441" strokeWidth="1" strokeDasharray="5 3" />
          {/* white car, from the west, travelling east along the main road */}
          <rect x="34" y="50" width="24" height="10" rx="2" fill="#dfe7ee" stroke="#0e1a22" />
          <path d="M62 55 L56 51.5 L56 58.5 Z" fill="#dfe7ee" />
          {/* red truck, out of the south arm, turning right across the main road */}
          <g transform="rotate(-38 100 68)">
            <rect x="93" y="52" width="14" height="30" rx="2" fill="#d1495b" stroke="#0e1a22" />
            <path d="M100 46 L94 52 L106 52 Z" fill="#d1495b" />
          </g>
          {/* contact, on the truck's front-left corner once rotated */}
          <circle cx="85" cy="59" r="3.4" fill="#ffcc00" />
          {/* stop sign on the junction corner */}
          <circle cx="128" cy="36" r="4.5" fill="#c0392b" stroke="#e8e8e8" strokeWidth="1" />
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
    <figure data-tour="illustration-layer" className="sheet overflow-hidden">
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
          alt="AI illustration panel from a live case: a top-down CGI render of a red truck pulling out of a side road at a stop sign into the path of a white car on the main road, captioned 'AI-generated illustration - not evidence'."
          className="h-full w-full object-cover"
        />
        <div className="absolute left-0 right-0 top-0 flex items-center justify-center gap-1.5 bg-gradient-to-b from-black/80 to-transparent py-1.5 font-mono text-[10px] font-semibold tracking-wide text-amber-200">
          AI ILLUSTRATION — NOT EVIDENCE
        </div>
      </div>
    </figure>
  );
}

function Pillar({
  icon,
  title,
  body,
  tourId,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
  /** Anchor the guided tour can ring and scroll to (see GuidedTour.tsx). */
  tourId: string;
}) {
  return (
    <div data-tour={tourId} className="sheet p-4">
      <div className="flex items-center gap-2">
        <span className="grid h-7 w-7 place-items-center rounded border border-steel-600 bg-steel-800">{icon}</span>
        <h3 className="font-mono text-sm font-semibold text-blueprint-text">{title}</h3>
      </div>
      <p className="mt-2 text-xs text-blueprint-dim">{body}</p>
    </div>
  );
}
