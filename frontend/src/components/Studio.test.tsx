import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Studio } from "./Studio";
import { claimsceneApi, type RenderResponse } from "@/lib/api";
import { useCaseStore } from "@/store/useCaseStore";
import { emptyScene } from "@/lib/scene";
import goldenRaw from "../test/fixtures/golden-manifest.json?raw";
import { ManifestSchema } from "@/lib/api";

function renderStudio() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Studio />
    </QueryClientProvider>,
  );
}

function makeResult(): RenderResponse {
  return {
    case_id: "golden-case", manifest_hash: "a".repeat(64), manifest_url: "/cases/golden-case",
    provider: "genblaze", degraded: true, provider_degraded: false,
    has_schematic_animation: false, schematic_kind: "static",
    schematic_url: "/cases/golden-case/schematic", illustration_url: "/cases/golden-case/illustration",
    report_markdown: "# report", scene: emptyScene(), warnings: [], artifacts: {},
  };
}

describe("Studio (step router + stepper)", () => {
  beforeEach(() => {
    useCaseStore.getState().reset();
    vi.restoreAllMocks();
    vi.spyOn(claimsceneApi, "scenarios").mockResolvedValue([]);
  });

  it("renders the stepper and routes the source step", () => {
    useCaseStore.setState({ step: "source" });
    renderStudio();
    expect(screen.getByText(/Step 1 of 4/i)).toBeInTheDocument();
    expect(screen.getByText(/Start a case/i)).toBeInTheDocument();
  });

  it("routes the review step to the review-adjust panel", () => {
    vi.spyOn(claimsceneApi, "previewSchematic").mockResolvedValue({
      svg: "<svg></svg>", warnings: [], contact_point: null, vehicle_count: 1,
      road: { layout: "x_intersection", lanes_per_direction: 1, signal: "none" },
    });
    useCaseStore.setState({ step: "review", scene: emptyScene() });
    renderStudio();
    expect(screen.getByText(/AI proposes, you confirm/i)).toBeInTheDocument();
  });

  it("routes the render step to the sealing screen", () => {
    vi.spyOn(claimsceneApi, "render").mockResolvedValue({ case_id: "x" } as never);
    useCaseStore.setState({ step: "render", scene: emptyScene(), scenario: null, photos: [] });
    renderStudio();
    expect(screen.getByRole("heading", { name: /Sealing your case/i })).toBeInTheDocument();
  });

  it("routes the result step to the sealed case", async () => {
    vi.spyOn(claimsceneApi, "manifest").mockResolvedValue(ManifestSchema.parse(JSON.parse(goldenRaw)));
    useCaseStore.setState({ step: "result", result: makeResult() });
    renderStudio();
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /Case sealed/i })).toBeInTheDocument(),
    );
  });
});
