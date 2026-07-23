import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Header, modeTooltip } from "./Header";
import { claimsceneApi } from "@/lib/api";

function renderHeader() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Header />
    </QueryClientProvider>,
  );
}

describe("modeTooltip", () => {
  it("explains each backend mode in plain English", () => {
    expect(modeTooltip("live")).toMatch(/real B2 storage/i);
    expect(modeTooltip("offline")).toMatch(/deterministic backends/i);
    expect(modeTooltip("weird")).toMatch(/mode "weird"/i);
  });
});

describe("Header", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows a live API badge when health resolves", async () => {
    vi.spyOn(claimsceneApi, "health").mockResolvedValue({
      status: "ok", service: "claimscene-api", mode: "offline",
    });
    renderHeader();
    await waitFor(() => expect(screen.getByText(/API/)).toBeInTheDocument());
    expect(screen.getByText(/offline/)).toBeInTheDocument();
  });

  it("shows an offline badge when health is unreachable", async () => {
    vi.spyOn(claimsceneApi, "health").mockRejectedValue(new Error("down"));
    renderHeader();
    await waitFor(() => expect(screen.getByText(/API offline/i)).toBeInTheDocument());
  });
});
