import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ResultStep } from "./ResultStep";
import { ManifestSchema, claimsceneApi, type Manifest, type RenderResponse } from "@/lib/api";
import { emptyScene } from "@/lib/scene";
// The real backend manifest (raw HTTP body of GET /cases/{id}).
import goldenRaw from "../../test/fixtures/golden-manifest.json?raw";

const REPORT = [
  "# Incident report — case `golden-case`",
  "",
  "> **AI-generated illustration — not evidence.** Factual layer only.",
  "",
  "## Parties",
  "",
  "- **veh_a** — silver car, approaching from N, turning left, at low speed.",
  "- **veh_b** — green van, approaching from S, proceeding straight.",
  "",
  "---",
  "_AI-generated illustration — not evidence._",
].join("\n");

function makeRender(overrides: Partial<RenderResponse> = {}): RenderResponse {
  return {
    case_id: "golden-case",
    manifest_hash: "a".repeat(64),
    manifest_url: "/cases/golden-case",
    provider: "genblaze",
    degraded: true,
    provider_degraded: false,
    has_schematic_animation: true,
    schematic_kind: "animation",
    schematic_url: "/cases/golden-case/schematic",
    illustration_url: "/cases/golden-case/illustration",
    report_markdown: REPORT,
    scene: emptyScene(),
    warnings: ["reviewed by a human operator"],
    artifacts: {},
    ...overrides,
  };
}

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("ManifestSchema against the real backend manifest", () => {
  it("parses the golden manifest (the provenance panel depends on this shape)", () => {
    const parsed = ManifestSchema.safeParse(JSON.parse(goldenRaw));
    expect(parsed.success).toBe(true);
  });
});

describe("ResultStep (the sealed-case money screen)", () => {
  const manifest: Manifest = ManifestSchema.parse(JSON.parse(goldenRaw));

  beforeEach(() => {
    vi.spyOn(claimsceneApi, "manifest").mockResolvedValue(manifest);
  });

  it("renders the report, disclosure overlay and provenance without throwing", async () => {
    const { container } = renderWithClient(<ResultStep result={makeRender()} />);

    expect(screen.getByRole("heading", { name: /Case sealed/i })).toBeInTheDocument();
    // The Markdown report renders: the ## Parties heading + a bolded party id.
    expect(screen.getByRole("heading", { name: "Parties" })).toBeInTheDocument();
    expect(screen.getByText("veh_a")).toBeInTheDocument();
    expect(screen.getByText(/silver car/)).toBeInTheDocument();
    // Persistent illustration disclosure overlay.
    expect(screen.getAllByText(/AI ILLUSTRATION — NOT EVIDENCE/i).length).toBeGreaterThan(0);
    // Animated schematic → a <video>; degraded illustration → placeholder (no 2nd video).
    expect(container.querySelectorAll("video").length).toBe(1);
    expect(screen.getByText(/provenance still verifies/i)).toBeInTheDocument();

    // The per-input attribution table resolves from the mocked manifest.
    await waitFor(() =>
      expect(screen.getByText(manifest.inputs[0]!.filename)).toBeInTheDocument(),
    );
    expect(claimsceneApi.manifest).toHaveBeenCalledWith("golden-case");
  });

  it("uses the static PNG branch and a playable illustration video when live", () => {
    const { container } = renderWithClient(
      <ResultStep
        result={makeRender({ degraded: false, has_schematic_animation: false, schematic_kind: "static" })}
      />,
    );
    // Static schematic → an <img>; live illustration → a <video>.
    expect(container.querySelector('img[alt="Sealed top-down accident schematic"]')).toBeTruthy();
    expect(container.querySelectorAll("video").length).toBe(1);
  });
});
