import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { GuidedTour, TOUR_STEPS, hasSeenTour, markTourSeen } from "./GuidedTour";

/** Renders the panel with the anchors it points at actually present, so the
 *  ring-and-scroll path is exercised rather than skipped. */
function renderWithAnchors(props: Partial<Parameters<typeof GuidedTour>[0]> = {}) {
  const onClose = props.onClose ?? vi.fn();
  const onStartCase = props.onStartCase ?? vi.fn();
  const utils = render(
    <>
      {TOUR_STEPS.map((s) => (
        <div key={s.id} data-tour={s.id}>
          anchor {s.id}
        </div>
      ))}
      <GuidedTour open={props.open ?? true} onClose={onClose} onStartCase={onStartCase} />
    </>,
  );
  return { ...utils, onClose, onStartCase };
}

const next = () => fireEvent.click(screen.getByRole("button", { name: /^Next/i }));

describe("GuidedTour", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });
  afterEach(() => window.localStorage.clear());

  it("renders nothing while closed", () => {
    renderWithAnchors({ open: false });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens on the first step, named and described for assistive tech", () => {
    renderWithAnchors();
    const dialog = screen.getByRole("dialog");
    // The dialog's own name/description carry step one, which a live region
    // cannot (it announces changes, not the content it mounted with).
    expect(dialog).toHaveAccessibleName(TOUR_STEPS[0]!.title);
    expect(dialog).toHaveAccessibleDescription(TOUR_STEPS[0]!.body);
    expect(screen.getByText(/step 1 of 6/i)).toBeInTheDocument();
  });

  it("is deliberately NOT modal, so the page behind it stays reachable", () => {
    renderWithAnchors();
    // aria-modal would tell a screen reader the rest of the page is inert; the
    // tour points AT that page, and the brief forbids trapping focus.
    expect(screen.getByRole("dialog")).not.toHaveAttribute("aria-modal", "true");
  });

  it("moves focus to the primary action on open so Enter walks the tour", () => {
    renderWithAnchors();
    expect(screen.getByRole("button", { name: /^Next/i })).toHaveFocus();
  });

  it("returns focus to the trigger when it closes", () => {
    const trigger = document.createElement("button");
    document.body.appendChild(trigger);
    trigger.focus();
    const { rerender, onClose } = renderWithAnchors();
    expect(screen.getByRole("button", { name: /^Next/i })).toHaveFocus();
    rerender(
      <>
        <GuidedTour open={false} onClose={onClose} onStartCase={vi.fn()} />
      </>,
    );
    expect(trigger).toHaveFocus();
    trigger.remove();
  });

  it("announces later steps through a stable polite live region", () => {
    const { container } = renderWithAnchors();
    const live = container.querySelector('[aria-live="polite"]')!;
    expect(live).toHaveAttribute("aria-atomic", "true");
    expect(live).toHaveTextContent(TOUR_STEPS[0]!.title);
    next();
    // Same element, new text — which is what makes it announce.
    expect(container.querySelector('[aria-live="polite"]')).toBe(live);
    expect(live).toHaveTextContent(TOUR_STEPS[1]!.title);
  });

  it("walks forward and back through every step", () => {
    renderWithAnchors();
    // Back is absent on the first step rather than present-and-dead.
    expect(screen.queryByRole("button", { name: /^Back/i })).not.toBeInTheDocument();
    for (let i = 1; i < TOUR_STEPS.length; i++) {
      next();
      expect(screen.getByRole("heading", { name: TOUR_STEPS[i]!.title })).toBeInTheDocument();
      expect(screen.getByText(new RegExp(`step ${i + 1} of 6`, "i"))).toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole("button", { name: /^Back/i }));
    expect(screen.getByRole("heading", { name: TOUR_STEPS[4]!.title })).toBeInTheDocument();
  });

  it("rings the element each step points at, and only that one", () => {
    const { container } = renderWithAnchors();
    const ringed = () =>
      Array.from(container.querySelectorAll("[data-tour-active]")).map((el) =>
        el.getAttribute("data-tour"),
      );
    expect(ringed()).toEqual(["what-it-is"]);
    next();
    expect(ringed()).toEqual(["factual-layer"]);
  });

  it("scrolls the anchored element into view", () => {
    const scrollIntoView = vi.fn();
    vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(scrollIntoView);
    renderWithAnchors();
    expect(scrollIntoView).toHaveBeenCalledWith({ block: "center", behavior: "smooth" });
  });

  it("survives a step whose anchor is not on the page", () => {
    // The tour is opened from the landing page; if it is ever mounted somewhere
    // the anchors do not exist, it must still render and still be leavable.
    const onClose = vi.fn();
    render(<GuidedTour open onClose={onClose} onStartCase={vi.fn()} />);
    expect(screen.getByRole("heading", { name: TOUR_STEPS[0]!.title })).toBeInTheDocument();
    next();
    expect(screen.getByRole("heading", { name: TOUR_STEPS[1]!.title })).toBeInTheDocument();
    expect(document.querySelectorAll("[data-tour-active]")).toHaveLength(0);
  });

  it("clears the ring when the tour closes", () => {
    const { container, rerender, onClose } = renderWithAnchors();
    expect(container.querySelectorAll("[data-tour-active]")).toHaveLength(1);
    rerender(
      <>
        {TOUR_STEPS.map((s) => (
          <div key={s.id} data-tour={s.id} />
        ))}
        <GuidedTour open={false} onClose={onClose} onStartCase={vi.fn()} />
      </>,
    );
    expect(container.querySelectorAll("[data-tour-active]")).toHaveLength(0);
  });

  it("leaves on the Leave tour button and remembers the dismissal", () => {
    const { onClose } = renderWithAnchors();
    expect(hasSeenTour()).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: /Leave tour/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(hasSeenTour()).toBe(true);
  });

  it("leaves on Escape", () => {
    const { onClose } = renderWithAnchors();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(hasSeenTour()).toBe(true);
  });

  it("walks with the arrow keys", () => {
    renderWithAnchors();
    fireEvent.keyDown(document, { key: "ArrowRight" });
    expect(screen.getByRole("heading", { name: TOUR_STEPS[1]!.title })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "ArrowLeft" });
    expect(screen.getByRole("heading", { name: TOUR_STEPS[0]!.title })).toBeInTheDocument();
    // An unrelated key is ignored rather than swallowed.
    fireEvent.keyDown(document, { key: "a" });
    expect(screen.getByRole("heading", { name: TOUR_STEPS[0]!.title })).toBeInTheDocument();
  });

  it("does not run past either end", () => {
    renderWithAnchors();
    fireEvent.keyDown(document, { key: "ArrowLeft" });
    expect(screen.getByText(/step 1 of 6/i)).toBeInTheDocument();
    for (let i = 0; i < TOUR_STEPS.length + 2; i++) fireEvent.keyDown(document, { key: "ArrowRight" });
    expect(screen.getByText(/step 6 of 6/i)).toBeInTheDocument();
  });

  it("ends on a call to action that closes the tour and starts a case", () => {
    const { onClose, onStartCase } = renderWithAnchors();
    for (let i = 1; i < TOUR_STEPS.length; i++) next();
    expect(screen.queryByRole("button", { name: /^Next/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Start a case/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onStartCase).toHaveBeenCalledTimes(1);
    expect(hasSeenTour()).toBe(true);
  });

  it("restarts at step one when reopened", () => {
    const { rerender, onClose } = renderWithAnchors();
    next();
    expect(screen.getByText(/step 2 of 6/i)).toBeInTheDocument();
    const anchors = TOUR_STEPS.map((s) => <div key={s.id} data-tour={s.id} />);
    rerender(
      <>
        {anchors}
        <GuidedTour open={false} onClose={onClose} onStartCase={vi.fn()} />
      </>,
    );
    rerender(
      <>
        {anchors}
        <GuidedTour open onClose={onClose} onStartCase={vi.fn()} />
      </>,
    );
    expect(screen.getByText(/step 1 of 6/i)).toBeInTheDocument();
  });

  it("carries the product's argument, not generic onboarding filler", () => {
    renderWithAnchors();
    const said = TOUR_STEPS.map((s) => `${s.title} ${s.body}`).join(" ");
    // The five points the tour exists to land.
    expect(said).toMatch(/same bytes/i); // deterministic factual layer
    expect(said).toMatch(/NOT EVIDENCE/); // disclosed illustration
    expect(said).toMatch(/confirm every field/i); // human in the loop
    expect(said).toMatch(/Backblaze B2/); // content-addressed storage
    expect(said).toMatch(/not a police report/i); // what sealed does not mean
    // Plain language: no developer vocabulary and no em-dashes in tour copy.
    expect(said).not.toMatch(/\bVLM\b|\bB2 storage\b|\bbackend\b|\bendpoint\b/i);
    expect(said).not.toMatch(/—/);
  });
});

describe("tour dismissal memory", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it("survives storage being unavailable", () => {
    // Safari private mode and blocked storage both throw here. A first-visit
    // hint is never worth an exception.
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("storage disabled");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage disabled");
    });
    expect(() => markTourSeen()).not.toThrow();
    expect(hasSeenTour()).toBe(false);
  });
});
