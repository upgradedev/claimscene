import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Footer } from "./Footer";

describe("Footer", () => {
  it("shows the current year, the repo link and the provenance line", () => {
    render(<Footer />);
    expect(screen.getByText(new RegExp(String(new Date().getFullYear())))).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /github\.com\/upgradedev\/claimscene/i });
    expect(link).toHaveAttribute("href", "https://github.com/upgradedev/claimscene");
    expect(link).toHaveAttribute("target", "_blank");
    expect(screen.getByText(/verifiable SHA-256 provenance/i)).toBeInTheDocument();
  });
});
