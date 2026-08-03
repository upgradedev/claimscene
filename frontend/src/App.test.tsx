import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { ApiError, claimsceneApi } from "@/lib/api";
import { emptyScene } from "@/lib/scene";
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
    expect(screen.getByRole("region", { name: /AI-content disclosure/i })).toBeInTheDocument(); // DisclosureBanner
    expect(screen.getByRole("heading", { name: /One verifiable seal/i })).toBeInTheDocument();
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
  it("reopens a sealed case named in the address bar", async () => {
    // The whole point: the tab that made this case is gone, and the case is
    // still here because its id is in the URL.
    const sealed = {
      case_id: "sealed-9", manifest_hash: "b".repeat(64), manifest_url: "/cases/sealed-9",
      provider: "fake-media", degraded: true, provider_degraded: false,
      has_schematic_animation: false, schematic_kind: "static" as const,
      schematic_url: "/cases/sealed-9/schematic",
      illustration_url: "/cases/sealed-9/illustration",
      report_markdown: "# report", scene: emptyScene(), warnings: [], artifacts: {},
    };
    vi.spyOn(claimsceneApi, "caseResult").mockResolvedValue(sealed);
    window.location.hash = "#case/sealed-9";
    renderApp();

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /Case sealed/i })).toBeInTheDocument());
    expect(screen.getByText("sealed-9")).toBeInTheDocument();
  });

  it("offers a fresh start when the link in the address bar names nothing", async () => {
    vi.spyOn(claimsceneApi, "caseResult")
      .mockRejectedValue(new ApiError("Request to /cases/x/result failed (404).", 404));
    window.location.hash = "#case/never-existed";
    renderApp();

    await screen.findByText(/could not find this case/i);
    fireEvent.click(screen.getByRole("button", { name: /Start a new case/i }));
    expect(screen.getByText(/Step 1 of 4/i)).toBeInTheDocument();
    expect(window.location.hash).toBe("#start");
  });

  it("keeps the case in the address bar when the skip link is used", async () => {
    // The skip link navigates to #main. Before this was handled, that read as
    // "not a case link" and threw the visitor out of their own case.
    const sealed = {
      case_id: "sealed-9", manifest_hash: "b".repeat(64), manifest_url: "/cases/sealed-9",
      provider: "fake-media", degraded: true, provider_degraded: false,
      has_schematic_animation: false, schematic_kind: "static" as const,
      schematic_url: "/cases/sealed-9/schematic",
      illustration_url: "/cases/sealed-9/illustration",
      report_markdown: "# report", scene: emptyScene(), warnings: [], artifacts: {},
    };
    vi.spyOn(claimsceneApi, "caseResult").mockResolvedValue(sealed);
    window.location.hash = "#case/sealed-9";
    renderApp();
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /Case sealed/i })).toBeInTheDocument());

    window.location.hash = "#main";
    await waitFor(() => expect(window.location.hash).toBe("#case/sealed-9"));
    expect(screen.getByRole("heading", { name: /Case sealed/i })).toBeInTheDocument();
  });

  it("#start starts a new case, even with one already open", async () => {
    const sealed = {
      case_id: "sealed-9", manifest_hash: "b".repeat(64), manifest_url: "/cases/sealed-9",
      provider: "fake-media", degraded: true, provider_degraded: false,
      has_schematic_animation: false, schematic_kind: "static" as const,
      schematic_url: "/cases/sealed-9/schematic",
      illustration_url: "/cases/sealed-9/illustration",
      report_markdown: "# report", scene: emptyScene(), warnings: [], artifacts: {},
    };
    vi.spyOn(claimsceneApi, "caseResult").mockResolvedValue(sealed);
    window.location.hash = "#case/sealed-9";
    renderApp();
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /Case sealed/i })).toBeInTheDocument());

    window.location.hash = "#start";
    await waitFor(() => expect(screen.getByText(/Step 1 of 4/i)).toBeInTheDocument());
    expect(useCaseStore.getState().result).toBeNull();
  });
});
