import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { claimsceneApi } from "@/lib/api";
import { useCaseStore } from "@/store/useCaseStore";

function renderApp() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <App />
    </QueryClientProvider>,
  );
}

describe("App shell", () => {
  beforeEach(() => {
    useCaseStore.getState().reset();
    window.location.hash = "";
    vi.restoreAllMocks();
    // jsdom doesn't implement scrollTo; the Start handler calls it.
    vi.stubGlobal("scrollTo", vi.fn());
    vi.spyOn(claimsceneApi, "health").mockResolvedValue({
      status: "ok", service: "claimscene-api", mode: "offline",
    });
    vi.spyOn(claimsceneApi, "scenarios").mockResolvedValue([]);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders the landing hero (with banner + footer) by default", () => {
    renderApp();
    expect(screen.getByRole("note")).toBeInTheDocument(); // DisclosureBanner
    expect(screen.getByRole("heading", { name: /One sealed truth/i })).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toBeInTheDocument(); // Footer
    expect(screen.queryByText(/Step 1 of 4/i)).not.toBeInTheDocument();
  });

  it("Start a case enters the studio", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: /Start a case/i }));
    // Studio's live-region step status is only present inside the studio.
    expect(screen.getByText(/Step 1 of 4/i)).toBeInTheDocument();
    expect(window.scrollTo).toHaveBeenCalled();
  });

  it("deep-links straight into the studio on #start", async () => {
    window.location.hash = "#start";
    renderApp();
    await waitFor(() => expect(screen.getByText(/Step 1 of 4/i)).toBeInTheDocument());
  });
});
