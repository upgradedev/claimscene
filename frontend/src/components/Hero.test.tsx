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

  it("both call-to-action buttons invoke onStart", () => {
    const onStart = vi.fn();
    render(<Hero onStart={onStart} />);
    fireEvent.click(screen.getByRole("button", { name: /Start a case/i }));
    fireEvent.click(screen.getByRole("button", { name: /Try a sample scenario/i }));
    expect(onStart).toHaveBeenCalledTimes(2);
  });
});
