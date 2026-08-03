import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Compass, X } from "lucide-react";
import { Button } from "./ui/button";

/**
 * A hand-rolled guided tour. No tour library: driver.js / shepherd / intro.js
 * would each add a runtime dependency, a bundle, a licence and a CSP surface for
 * something this small does honestly.
 *
 * Shape, and why:
 *  - NON-modal `role="dialog"`. The tour points AT the page, so the page must
 *    stay readable and reachable behind it. There is deliberately no focus trap
 *    (the brief: it must never trap focus with no way out) — Escape leaves,
 *    "Leave tour" leaves, and Tab walks out into the page like normal.
 *  - Anchored by data attribute, not by measured coordinates. Each step marks
 *    its target with `data-tour-active`, which index.css rings with a box-shadow
 *    (never an outline or a border, which could widen the scrollable area on a
 *    375px screen), and scrolls it into view. The panel itself is a fixed
 *    bottom sheet, so nothing has to be positioned against a moving rect and
 *    there is no clipping or overflow risk on a phone.
 *  - Opt-in. It never auto-opens: an overlay that appears unasked would change
 *    the input to the Lighthouse accessibility budget and to every existing e2e
 *    spec, and "the app must never be blocked" outranks being noticed.
 */

export interface TourStep {
  /** Stable id, also the `data-tour` value of the element the step points at. */
  id: string;
  title: string;
  body: string;
}

/** The product's actual argument, in the order it has to be understood. */
export const TOUR_STEPS: readonly TourStep[] = [
  {
    id: "what-it-is",
    title: "Two layers, kept apart",
    body:
      "ClaimScene turns crash photos into two separate things. One is a plain top-down drawing of what happened. The other is an AI picture that carries the look of the scene and none of the facts.",
  },
  {
    id: "factual-layer",
    title: "The drawing is the factual layer",
    body:
      "Code draws this from the details you confirm, with no AI anywhere in the drawing step. Run it again on the same details and you get the same bytes, every time.",
  },
  {
    id: "illustration-layer",
    title: "The AI picture is an illustration",
    body:
      "It is marked NOT EVIDENCE on the picture itself and in the sealed record. It carries visual style only, so nothing in it is ever read as a fact.",
  },
  {
    id: "you-confirm",
    title: "You confirm every field",
    body:
      "The AI reads your photos and proposes a scene. You check and change every field before anything is sealed, and the difference between what it proposed and what you confirmed is sealed with it.",
  },
  {
    id: "verify",
    title: "Sealed, and you can check it yourself",
    body:
      "Every file is stored on Backblaze B2 under a name made from the file's own contents. The app recomputes those names in your browser, so you are not asked to take our word for it.",
  },
  {
    id: "disclosure",
    title: "What sealed does not mean",
    body:
      "Sealed means the files have not changed since the case was closed. It is not a police report and it does not decide who was at fault.",
  },
] as const;

const STORAGE_KEY = "claimscene.tour.seen.v1";

/** True once the visitor has finished or left the tour. Storage can throw
 *  (Safari private mode, blocked third-party storage), and a first-visit hint
 *  is never worth an exception, so a failure reads as "not seen yet". */
export function hasSeenTour(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

/** Remembers the dismissal so the first-visit hint is shown once, not on every
 *  navigation. Same reasoning: a storage failure must not break the app. */
export function markTourSeen(): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, "1");
  } catch {
    /* storage unavailable; the hint just shows again next time */
  }
}

export interface GuidedTourProps {
  open: boolean;
  /** Called whenever the tour ends, by any route (Leave, Escape, or finish). */
  onClose: () => void;
  /** The final step's call to action: leave the tour and enter the studio. */
  onStartCase: () => void;
}

export function GuidedTour({ open, onClose, onStartCase }: GuidedTourProps) {
  const [index, setIndex] = useState(0);
  const primaryRef = useRef<HTMLButtonElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  // `index` is only ever produced by the two clamped setters below, so this is
  // in range by construction — asserted rather than defaulted so there is no
  // unreachable fallback branch to pretend to test.
  const step = TOUR_STEPS[index]!;
  const isLast = index === TOUR_STEPS.length - 1;

  const leave = useCallback(() => {
    markTourSeen();
    onClose();
  }, [onClose]);

  const goNext = useCallback(() => {
    setIndex((i) => Math.min(i + 1, TOUR_STEPS.length - 1));
  }, []);

  const goBack = useCallback(() => {
    setIndex((i) => Math.max(i - 1, 0));
  }, []);

  const finish = useCallback(() => {
    markTourSeen();
    onClose();
    onStartCase();
  }, [onClose, onStartCase]);

  // Always begin at step one, and move focus to the primary action so Enter
  // walks the whole tour. Focus returns to whatever opened it on close.
  useEffect(() => {
    if (!open) return;
    setIndex(0);
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    primaryRef.current?.focus();
    return () => {
      previouslyFocused.current?.focus();
    };
  }, [open]);

  // Ring the element this step is about, and bring it on screen.
  useEffect(() => {
    if (!open) return;
    const target = document.querySelector<HTMLElement>(`[data-tour="${step.id}"]`);
    if (!target) return;
    target.setAttribute("data-tour-active", "true");
    target.scrollIntoView({ block: "center", behavior: "smooth" });
    return () => target.removeAttribute("data-tour-active");
  }, [open, step.id]);

  // Escape leaves; the arrow keys walk the steps without reaching for a button.
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        leave();
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        goNext();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        goBack();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, leave, goNext, goBack]);

  if (!open) return null;

  return (
    // The outer layer is click-through (`pointer-events-none`) so the ringed
    // element behind the sheet stays usable; only the sheet itself takes events.
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-50 px-3 pb-3">
      <div
        role="dialog"
        aria-labelledby="tour-step-title"
        aria-describedby="tour-step-body"
        className="sheet sheet-ticks pointer-events-auto mx-auto w-full max-w-xl bg-steel-900 p-4 shadow-2xl"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="label-caps inline-flex items-center gap-1.5">
            <Compass className="h-3.5 w-3.5 text-cyan-400" aria-hidden />
            Guided tour · step {index + 1} of {TOUR_STEPS.length}
          </p>
          <Button variant="ghost" size="sm" className="min-h-11" onClick={leave}>
            <X className="h-4 w-4" aria-hidden /> Leave tour
          </Button>
        </div>

        {/* A stable live region: its wrapper is mounted for the whole tour, so
            steps 2..N are announced when the text inside it changes. Step one
            is carried by the dialog's own name/description instead, which a
            live region cannot do (it announces changes, not initial content). */}
        <div aria-live="polite" aria-atomic="true" className="mt-3">
          <h2 id="tour-step-title" className="font-mono text-base font-semibold text-blueprint-text">
            {step.title}
          </h2>
          <p id="tour-step-body" className="mt-1.5 text-sm leading-relaxed text-blueprint-dim">
            {step.body}
          </p>
        </div>

        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          {/* Decoration, not controls: as buttons these would be interactive
              targets far under 44px. Progress is stated in words above. */}
          <div className="flex items-center gap-1.5" aria-hidden="true">
            {TOUR_STEPS.map((s, i) => (
              <span
                key={s.id}
                className={
                  i === index
                    ? "h-1.5 w-5 rounded-full bg-cyan-400"
                    : "h-1.5 w-1.5 rounded-full bg-steel-600"
                }
              />
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {index > 0 && (
              <Button variant="secondary" size="md" className="min-h-11" onClick={goBack}>
                <ArrowLeft className="h-4 w-4" aria-hidden /> Back
              </Button>
            )}
            {isLast ? (
              <Button ref={primaryRef} variant="primary" size="md" className="min-h-11" onClick={finish}>
                Start a case <ArrowRight className="h-4 w-4" aria-hidden />
              </Button>
            ) : (
              <Button ref={primaryRef} variant="primary" size="md" className="min-h-11" onClick={goNext}>
                Next <ArrowRight className="h-4 w-4" aria-hidden />
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
