import { useEffect, useRef } from "react";
import { AnimatePresence, motion } from "framer-motion";
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

  useEffect(() => {
    regionRef.current?.focus();
  }, [step]);

  return (
    <section className="container max-w-5xl py-8 md:py-12">
      <Stepper current={step} />
      <p className="sr-only" role="status" aria-live="polite">
        {STEP_LABEL[step]}
      </p>

      <AnimatePresence mode="wait">
        <motion.div
          key={step}
          ref={regionRef}
          tabIndex={-1}
          role="group"
          aria-label={STEP_LABEL[step]}
          className="outline-none"
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -12 }}
          transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
        >
          {step === "source" && <SourceStep />}
          {step === "review" && <ReviewStep />}
          {step === "render" && <RenderStep />}
          {step === "result" && (result ? <ResultStep result={result} /> : <SourceStep />)}
        </motion.div>
      </AnimatePresence>
    </section>
  );
}
