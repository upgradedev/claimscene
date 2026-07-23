import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { Wordmark } from "./Wordmark";

describe("Wordmark", () => {
  it("renders the crosshair mark and the ClaimScene wordmark", () => {
    const { container } = render(<Wordmark />);
    expect(container.querySelector("svg")).toBeTruthy();
    expect(container.textContent).toContain("ClaimScene");
  });

  it("merges a caller className onto the root", () => {
    const { container } = render(<Wordmark className="test-marker" />);
    expect(container.firstElementChild?.className).toContain("test-marker");
  });
});
