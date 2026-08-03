import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Header } from "./Header";
import { claimsceneApi } from "@/lib/api";

function renderHeader() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Header />
    </QueryClientProvider>,
  );
}

describe("Header", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows no backend badge on the healthy path", async () => {
    // Regression guard. The header used to advertise "API · live" with a
    // tooltip reading "real B2 storage + VLM + Genblaze wired". Backend mode
    // is an ops detail, and that vocabulary is aimed at us, not at a driver
    // filing a claim. Health is still polled, it just stays silent when fine.
    const health = vi.spyOn(claimsceneApi, "health").mockResolvedValue({
      status: "ok", service: "claimscene-api", mode: "live",
    });
    renderHeader();
    await waitFor(() => expect(health).toHaveBeenCalled());
    expect(screen.queryByText(/API/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\blive\b/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/VLM|B2 storage/i)).not.toBeInTheDocument();
  });

  it("shows an offline badge when health is unreachable", async () => {
    vi.spyOn(claimsceneApi, "health").mockRejectedValue(new Error("down"));
    renderHeader();
    await waitFor(() => expect(screen.getByText(/API offline/i)).toBeInTheDocument());
  });
});
