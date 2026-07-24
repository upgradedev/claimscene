import { useEffect, useRef } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { Stepper } from "./Stepper";
import { SourceStep } from "./steps/SourceStep";
import { ReviewStep } from "./steps/ReviewStep";
import { RenderStep } from "./steps/RenderStep";
import { ResultStep } from "./steps/ResultStep";
import { useCaseStore } from "@/store/useCaseStore";

const STEP_LABEL: Record<string, string> = {
  source: "Step 1 of 4: choose a source",
  review: "Step 2 of 4: review and adjust the scene",
  render: "Step 3 of 4: rendering the sealed case",
  result: "Step 4 of 4: your sealed case is ready",
};

export function Studio() {
  const step = useCaseStore((s) => s.step);
  const result = useCaseStore((s) => s.result);
  const regionRef = useRef<HTMLDivElement>(null);
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    regionRef.current?.focus();
  }, [step]);

  return (
    <section className="container max-w-5xl py-8 md:py-12">
      <Stepper current={step} />
      <p className="sr-only" role="status" aria-live="polite">
        {STEP_LABEL[step]}
      </p>

      {/* Robust step transitions. Each step is a fresh keyed node that animates
          its OWN entrance to opacity:1 on mount — there is no exit to wait on,
          so an incoming step can never stall at its initial opacity:0. This
          replaces the previous <AnimatePresence mode="wait"> wrapper, whose
          enter only started after the outgoing exit completed: when that exit
          didn't finish, the next step stayed invisible and the wizard
          dead-ended. `role=group`'s label stays in lock-step with the
          `role=status` live region above (both read STEP_LABEL[step]).
          prefers-reduced-motion snaps straight to the settled state. */}
      <motion.div
        key={step}
        ref={regionRef}
        tabIndex={-1}
        role="group"
        aria-label={STEP_LABEL[step]}
        className="outline-none"
        initial={reduceMotion ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: reduceMotion ? 0 : 0.28, ease: [0.22, 1, 0.36, 1] }}
      >
        {step === "source" && <SourceStep />}
        {step === "review" && <ReviewStep />}
        {step === "render" && <RenderStep />}
        {step === "result" && (result ? <ResultStep result={result} /> : <SourceStep />)}
      </motion.div>
    </section>
  );
}
