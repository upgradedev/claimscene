import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Hero } from "./Hero";

describe("Hero", () => {
  it("renders the two-layer thesis and trust pillars", () => {
    render(<Hero onStart={() => {}} />);
    expect(screen.getByRole("heading", { name: /One sealed truth/i })).toBeInTheDocument();
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
});
