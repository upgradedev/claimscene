import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RenderStep } from "./RenderStep";
import { claimsceneApi } from "@/lib/api";
import { useCaseStore } from "@/store/useCaseStore";
import { emptyScene } from "@/lib/scene";

const SCENARIO = { id: "s02_left_cross", title: "Left Cross", context: "c",
                   summary: "s", images: [], thumbnail: null };

function renderStep() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <RenderStep />
    </QueryClientProvider>,
  );
}

describe("RenderStep", () => {
  beforeEach(() => {
    useCaseStore.getState().reset();
    vi.restoreAllMocks();
    useCaseStore.setState({ scene: emptyScene(), caseId: "case", scenario: SCENARIO, photos: [] });
  });

  it("auto-fires the render and seals the result on success", async () => {
    const spy = vi.spyOn(claimsceneApi, "render").mockResolvedValue({ case_id: "sealed-1" } as never);
    renderStep();
    expect(screen.getByRole("heading", { name: /Sealing your case/i })).toBeInTheDocument();
    await waitFor(() => expect(useCaseStore.getState().result).not.toBeNull());
    expect(spy).toHaveBeenCalledWith(
      expect.objectContaining({ scenarioId: "s02_left_cross", caseId: "case" }),
    );
    expect(useCaseStore.getState().step).toBe("result");
  });

  it("sends the frozen AI proposal + honest classification for the approval receipt", async () => {
    const proposed = emptyScene();
    useCaseStore.setState({ proposedScene: proposed });
    const spy = vi.spyOn(claimsceneApi, "render").mockResolvedValue({ case_id: "s" } as never);
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

  it("shows an error with retry + back on failure", async () => {
    vi.spyOn(claimsceneApi, "render").mockRejectedValue(new Error("render blew up"));
    renderStep();
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /Render failed/i })).toBeInTheDocument(),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/render blew up/i);

    // Retry re-fires the render (reset + start), a second attempt.
    const spy = vi.mocked(claimsceneApi.render);
    const before = spy.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: /Retry/i }));
    await waitFor(() => expect(spy.mock.calls.length).toBeGreaterThan(before));

    // Back to review returns to the edit step.
    fireEvent.click(screen.getByRole("button", { name: /Back to review/i }));
    expect(useCaseStore.getState().step).toBe("review");
  });
});
