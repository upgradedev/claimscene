import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RenderStep } from "./RenderStep";
import { claimsceneApi } from "@/lib/api";
import { useCaseStore } from "@/store/useCaseStore";
import { emptyScene } from "@/lib/scene";
import { RENDER_JOB_POLL_INTERVAL_MS } from "@/lib/queries";

const SCENARIO = { id: "s02_left_cross", title: "Left Cross", context: "c",
                   summary: "s", images: [], thumbnail: null };

function renderStep() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <RenderStep />
    </QueryClientProvider>,
  );
}

function renderBody(overrides: Record<string, unknown> = {}) {
  return {
    case_id: "sealed-1", manifest_hash: "a".repeat(64), manifest_url: "/cases/sealed-1",
    provider: "fake-media", degraded: true, provider_degraded: false,
    has_schematic_animation: false, schematic_kind: "static",
    schematic_url: "/cases/sealed-1/schematic", illustration_url: "/cases/sealed-1/illustration",
    report_markdown: "# report", scene: emptyScene(), warnings: [], artifacts: {},
    ...overrides,
  };
}

describe("RenderStep", () => {
  beforeEach(() => {
    useCaseStore.getState().reset();
    vi.restoreAllMocks();
    useCaseStore.setState({ scene: emptyScene(), caseId: "case", scenario: SCENARIO, photos: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("submits a background job, polls it, and seals the result when the first poll is already done (offline/demo)", async () => {
    const submitSpy = vi
      .spyOn(claimsceneApi, "submitRenderJob")
      .mockResolvedValue({ job_id: "job-1", status: "queued" });
    const getJobSpy = vi
      .spyOn(claimsceneApi, "getRenderJob")
      .mockResolvedValue({ job_id: "job-1", status: "done", result: renderBody() as never });

    renderStep();
    expect(screen.getByRole("heading", { name: /Sealing your case/i })).toBeInTheDocument();

    await waitFor(() => expect(useCaseStore.getState().result).not.toBeNull());
    expect(submitSpy).toHaveBeenCalledWith(
      expect.objectContaining({ scenarioId: "s02_left_cross", caseId: "case" }),
    );
    expect(getJobSpy).toHaveBeenCalledWith("job-1");
    expect(useCaseStore.getState().step).toBe("result");
  });

  it("sends the frozen AI proposal + honest classification for the approval receipt", async () => {
    const proposed = emptyScene();
    useCaseStore.setState({ proposedScene: proposed });
    const spy = vi
      .spyOn(claimsceneApi, "submitRenderJob")
      .mockResolvedValue({ job_id: "job-1", status: "queued" });
    vi.spyOn(claimsceneApi, "getRenderJob").mockResolvedValue({
      job_id: "job-1", status: "running",
    });
    renderStep();
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy).toHaveBeenCalledWith(
      expect.objectContaining({
        proposedScene: proposed,
        reviewClassification: "interactive_demo",
        scenarioId: "s02_left_cross",
      }),
    );
  });

  it("sends the uploaded photos + their roles when no scenario is selected", async () => {
    const file = new File([new Uint8Array([1, 2, 3])], "damage_a.png", { type: "image/png" });
    useCaseStore.setState({
      scenario: null,
      photos: [{ id: "p1", file, url: "blob:x", name: "damage_a.png", role: "damage_photo" }],
    });
    const spy = vi
      .spyOn(claimsceneApi, "submitRenderJob")
      .mockResolvedValue({ job_id: "job-1", status: "queued" });
    vi.spyOn(claimsceneApi, "getRenderJob").mockResolvedValue({ job_id: "job-1", status: "running" });
    renderStep();
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy).toHaveBeenCalledWith(
      expect.objectContaining({ files: [file], roles: ["damage_photo"] }),
    );
    expect(spy.mock.calls[0]?.[0].scenarioId).toBeUndefined();
  });

  it("keeps polling across queued -> running -> done before sealing the result", async () => {
    vi.useFakeTimers();
    vi.spyOn(claimsceneApi, "submitRenderJob").mockResolvedValue({ job_id: "job-1", status: "queued" });
    const getJobSpy = vi
      .spyOn(claimsceneApi, "getRenderJob")
      .mockResolvedValueOnce({ job_id: "job-1", status: "queued" })
      .mockResolvedValueOnce({ job_id: "job-1", status: "running" })
      .mockResolvedValueOnce({ job_id: "job-1", status: "done", result: renderBody() as never });

    renderStep();

    // Flush the submit and the poll's immediate first tick (queued).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole("heading", { name: /Sealing your case/i })).toBeInTheDocument();
    expect(getJobSpy).toHaveBeenCalledTimes(1);
    expect(useCaseStore.getState().result).toBeNull();

    // Second tick (running) — one interval later.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RENDER_JOB_POLL_INTERVAL_MS);
    });
    expect(getJobSpy).toHaveBeenCalledTimes(2);
    expect(useCaseStore.getState().result).toBeNull();

    // Third tick (done) — another interval later.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RENDER_JOB_POLL_INTERVAL_MS);
    });
    expect(getJobSpy).toHaveBeenCalledTimes(3);
    expect(useCaseStore.getState().result).not.toBeNull();
    expect(useCaseStore.getState().step).toBe("result");
  });

  it("shows the friendly job failure error and offers retry + back", async () => {
    vi.spyOn(claimsceneApi, "submitRenderJob").mockResolvedValue({ job_id: "job-1", status: "queued" });
    vi.spyOn(claimsceneApi, "getRenderJob").mockResolvedValue({
      job_id: "job-1", status: "failed", error: "RuntimeError",
    });

    renderStep();
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /Render failed/i })).toBeInTheDocument(),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("RuntimeError");
    expect(useCaseStore.getState().result).toBeNull();

    // Back to review returns to the edit step.
    fireEvent.click(screen.getByRole("button", { name: /Back to review/i }));
    expect(useCaseStore.getState().step).toBe("review");
  });

  it("surfaces a submit-level failure without ever polling", async () => {
    vi.spyOn(claimsceneApi, "submitRenderJob").mockRejectedValue(new Error("render blew up"));
    const getJobSpy = vi.spyOn(claimsceneApi, "getRenderJob");

    renderStep();
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /Render failed/i })).toBeInTheDocument(),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/render blew up/i);
    expect(getJobSpy).not.toHaveBeenCalled();

    // Retry re-fires a fresh submit (a second attempt).
    const spy = vi.mocked(claimsceneApi.submitRenderJob);
    const before = spy.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: /Retry/i }));
    await waitFor(() => expect(spy.mock.calls.length).toBeGreaterThan(before));
  });
});
