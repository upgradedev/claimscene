import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { Hero } from "./Hero";

describe("Hero", () => {
  it("renders the two-layer thesis and trust pillars", () => {
    render(<Hero onStart={() => {}} />);
    expect(screen.getByRole("heading", { name: /One verifiable seal/i })).toBeInTheDocument();
    expect(screen.getByText(/You stay in control/i)).toBeInTheDocument();
    expect(screen.getByText(/No hallucinated coordinates/i)).toBeInTheDocument();
    // Both preview cards carry the NOT EVIDENCE disclosure.
    expect(screen.getAllByText(/NOT EVIDENCE/i).length).toBeGreaterThanOrEqual(2);
  });

  it("names the target audience", () => {
    render(<Hero onStart={() => {}} />);
    expect(screen.getByText(/insurers/i)).toBeInTheDocument();
    expect(screen.getByText(/claims adjusters/i)).toBeInTheDocument();
    expect(screen.getByText(/fleet-safety teams/i)).toBeInTheDocument();
    expect(screen.getByText(/claimants/i)).toBeInTheDocument();
  });

  it("shows the two layers as a real example pair", () => {
    render(<Hero onStart={() => {}} />);
    // Section titles frame the pair + the trust group (clean h1 → h2 → h3).
    expect(screen.getByRole("heading", { level: 2, name: /two layers, side by side/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: /why you can trust it/i })).toBeInTheDocument();
    // The disclosed illustration is a real rendered frame (not a placeholder)…
    expect(screen.getByRole("img", { name: /AI illustration panel from a live case/i })).toBeInTheDocument();
    // …paired with the deterministic schematic image.
    expect(screen.getByRole("img", { name: /top-down schematic/i })).toBeInTheDocument();
  });

  it("both call-to-action buttons invoke onStart", () => {
    const onStart = vi.fn();
    render(<Hero onStart={onStart} />);
    fireEvent.click(screen.getByRole("button", { name: /Start a case/i }));
    fireEvent.click(screen.getByRole("button", { name: /Try a sample scenario/i }));
    expect(onStart).toHaveBeenCalledTimes(2);
  });

  describe("guided tour entry point", () => {
    beforeEach(() => window.localStorage.clear());
    afterEach(() => window.localStorage.clear());

    it("offers the tour, opens it on demand, and never opens it unasked", () => {
      render(<Hero onStart={() => {}} />);
      // Opt-in: nothing overlays the page until the visitor asks for it.
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /Take the guided tour/i }));
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      expect(screen.getByText(/step 1 of 6/i)).toBeInTheDocument();
    });

    it("anchors every tour step to something real on the page", () => {
      const { container } = render(<Hero onStart={() => {}} />);
      // The banner's anchor lives in DisclosureBanner (App-level), so the last
      // step is checked in the e2e rather than here.
      for (const id of ["what-it-is", "factual-layer", "illustration-layer", "you-confirm", "verify"]) {
        expect(container.querySelector(`[data-tour="${id}"]`), `anchor ${id}`).not.toBeNull();
      }
    });

    it("hints once for a first-time visitor, then never nags again", () => {
      const { unmount } = render(<Hero onStart={() => {}} />);
      expect(screen.getByText(/New here\?/i)).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /Take the guided tour/i }));
      fireEvent.click(screen.getByRole("button", { name: /Leave tour/i }));
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(screen.queryByText(/New here\?/i)).not.toBeInTheDocument();
      // And it stays gone on the next visit, not just the rest of this one.
      unmount();
      render(<Hero onStart={() => {}} />);
      expect(screen.queryByText(/New here\?/i)).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Take the guided tour/i })).toBeInTheDocument();
    });

    it("the tour's closing call to action starts a case", () => {
      const onStart = vi.fn();
      render(<Hero onStart={onStart} />);
      fireEvent.click(screen.getByRole("button", { name: /Take the guided tour/i }));
      for (let i = 0; i < 5; i++) fireEvent.click(screen.getByRole("button", { name: /^Next/i }));
      // Two "Start a case" buttons exist at this point (the hero CTA and the
      // tour's own); the one inside the dialog is the one under test.
      const dialog = screen.getByRole("dialog");
      fireEvent.click(within(dialog).getByRole("button", { name: /^Start a case/i }));
      expect(onStart).toHaveBeenCalledTimes(1);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });
});
