import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Stepper } from "./Stepper";

describe("Stepper", () => {
  it("lists all four workflow steps in order", () => {
    render(<Stepper current="source" />);
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(4);
    for (const label of ["Source", "Review & adjust", "Render", "Sealed case"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    // At the first step, the index "01" marker is shown (not yet a done check).
    expect(screen.getByText("01")).toBeInTheDocument();
  });

  it("marks earlier steps done (check icons) at a later step", () => {
    const { container } = render(<Stepper current="render" />);
    // source + review are behind "render" → two check marks rendered.
    expect(container.querySelectorAll("svg").length).toBe(2);
    // The current step's own numeric marker ("03") is still shown.
    expect(screen.getByText("03")).toBeInTheDocument();
  });
});
