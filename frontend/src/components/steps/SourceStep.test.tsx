import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SourceStep } from "./SourceStep";
import { claimsceneApi } from "@/lib/api";
import { useCaseStore } from "@/store/useCaseStore";
import { emptyScene } from "@/lib/scene";

const SCENARIO = { id: "s01_rear_end", title: "Rear End", context: "c",
                   summary: "2 vehicles · straight", images: ["a.jpg"], thumbnail: "/a.jpg" };

function renderStep() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SourceStep />
    </QueryClientProvider>,
  );
}

describe("SourceStep", () => {
  beforeEach(() => {
    useCaseStore.getState().reset();
    vi.restoreAllMocks();
    vi.spyOn(claimsceneApi, "scenarios").mockResolvedValue([SCENARIO]);
  });

  it("renders the heading and the sample scenario cards", async () => {
    renderStep();
    expect(screen.getByText(/Start a case/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Rear End")).toBeInTheDocument());
  });

  it("choosing a scenario then Extract advances to the review step", async () => {
    const extractSpy = vi.spyOn(claimsceneApi, "extract").mockResolvedValue({
      scene: emptyScene(), inputs: [],
      extraction: { extractor: "committed-ground-truth", source: "committed_ground_truth", mode: "offline" },
    });
    renderStep();
    await waitFor(() => expect(screen.getByText("Rear End")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /Rear End/i }));
    expect(useCaseStore.getState().scenario?.id).toBe("s01_rear_end");

    const extract = screen.getByRole("button", { name: /Extract scene/i });
    expect(extract).not.toBeDisabled();
    fireEvent.click(extract);

    await waitFor(() => expect(useCaseStore.getState().step).toBe("review"));
    expect(extractSpy).toHaveBeenCalledWith({ scenarioId: "s01_rear_end", context: undefined });
    expect(useCaseStore.getState().scene).not.toBeNull();
  });

  it("surfaces an extraction error without advancing", async () => {
    vi.spyOn(claimsceneApi, "extract").mockRejectedValue(new Error("extractor exploded"));
    renderStep();
    await waitFor(() => expect(screen.getByText("Rear End")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Rear End/i }));
    fireEvent.click(screen.getByRole("button", { name: /Extract scene/i }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/extractor exploded/i));
    expect(useCaseStore.getState().step).toBe("source");
  });

  it("the Extract button is disabled until a source is chosen", async () => {
    renderStep();
    await waitFor(() => expect(screen.getByText("Rear End")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Extract scene/i })).toBeDisabled();
  });
});

describe("SourceStep — the upload path", () => {
  beforeEach(() => {
    useCaseStore.getState().reset();
    vi.restoreAllMocks();
    vi.spyOn(claimsceneApi, "scenarios").mockResolvedValue([SCENARIO]);
  });

  function upload(name: string) {
    const input = screen.getByLabelText(/Choose accident photos/i);
    fireEvent.change(input, {
      target: { files: [new File(["x"], name, { type: "image/png" })] },
    });
  }

  it("adds uploaded photos with an inferred role and a role dropdown", async () => {
    renderStep();
    upload("front-damage.png");
    expect(useCaseStore.getState().photos).toHaveLength(1);
    expect(useCaseStore.getState().photos[0]!.role).toBe("damage_photo");
    // The per-photo role Select renders.
    expect(screen.getByLabelText(/Role for front-damage\.png/i)).toBeInTheDocument();
  });

  it("edits a role, removes a photo, and clears the list", () => {
    renderStep();
    upload("a.png");
    const select = screen.getByLabelText(/Role for a\.png/i);
    fireEvent.change(select, { target: { value: "road_photo" } });
    expect(useCaseStore.getState().photos[0]!.role).toBe("road_photo");

    fireEvent.click(screen.getByRole("button", { name: /Remove a\.png/i }));
    expect(useCaseStore.getState().photos).toHaveLength(0);

    upload("b.png");
    fireEvent.click(screen.getByRole("button", { name: /^Clear$/i }));
    expect(useCaseStore.getState().photos).toHaveLength(0);
  });

  it("accepts drag-and-drop and records a context note", () => {
    renderStep();
    const dropzone = screen.getByRole("button", { name: /Browse files/i }).parentElement!;
    fireEvent.dragOver(dropzone);
    fireEvent.dragLeave(dropzone);
    fireEvent.drop(dropzone, {
      dataTransfer: { files: [new File(["x"], "dropped.png", { type: "image/png" })] },
    });
    expect(useCaseStore.getState().photos).toHaveLength(1);

    fireEvent.change(screen.getByLabelText(/context note/i), {
      target: { value: "rear-ended at the light" },
    });
    expect(useCaseStore.getState().contextNote).toBe("rear-ended at the light");
  });

  it("extracts from uploaded files (files + roles) and advances to review", async () => {
    const extractSpy = vi.spyOn(claimsceneApi, "extract").mockResolvedValue({
      scene: emptyScene(), inputs: [],
      extraction: { extractor: "fake-vision", source: "fake_extraction", mode: "offline" },
    });
    renderStep();
    upload("scene.png");
    fireEvent.click(screen.getByRole("button", { name: /Extract scene/i }));
    await waitFor(() => expect(useCaseStore.getState().step).toBe("review"));
    expect(extractSpy).toHaveBeenCalledWith(
      expect.objectContaining({ roles: ["scene_photo"] }),
    );
    expect(extractSpy.mock.calls[0]![0].files).toHaveLength(1);
  });
});
