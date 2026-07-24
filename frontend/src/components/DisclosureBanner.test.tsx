import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { DisclosureBanner } from "./DisclosureBanner";

describe("DisclosureBanner", () => {
  it("renders the persistent honesty note (the product's ethical spine)", () => {
    render(<DisclosureBanner />);
    const note = screen.getByRole("region", { name: /AI-content disclosure/i });
    expect(note).toBeInTheDocument();
    expect(note).toHaveTextContent(/AI-generated .* NOT EVIDENCE/i);
    expect(note).toHaveTextContent(/deterministic factual layer/i);
  });
});
