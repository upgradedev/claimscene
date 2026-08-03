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

  it("explains a live-provider failure in plain words, and clears the schematic",
    async () => {
      renderWithClient(
        <ResultStep result={makeRender({ provider_degraded: true, degrade_kind: "credit" })} />,
      );
      expect(screen.getByText(/AI picture could not be made/i)).toBeInTheDocument();
      expect(screen.getByText(/out of credit/i)).toBeInTheDocument();
      // The one thing that actually matters: the facts layer is untouched.
      expect(screen.getByText(/schematic below is the factual layer/i)).toBeInTheDocument();
      // Nothing from the provider or the transport reaches the notice. Scoped
      // to the notice itself: the sealed provenance ledger further down names
      // providers on purpose, and is exempt (its readers are adjusters).
      const notice = screen.getByText(/AI picture could not be made/i).closest("div");
      expect(notice?.textContent ?? "").not.toMatch(/genblaze|gmi|402|api key|http/i);
    });

  it("says nothing about a failure when the provider did not fail", () => {
    renderWithClient(<ResultStep result={makeRender()} />);
    expect(screen.queryByText(/AI picture could not be made/i)).not.toBeInTheDocument();
  });

  it("falls back to an honest sentence for a failure kind it has never seen", () => {
    renderWithClient(
      <ResultStep result={makeRender({ provider_degraded: true, degrade_kind: "solar_flare" })} />,
    );
    expect(screen.getByText(/did not say why/i)).toBeInTheDocument();
  });

  it("shows the link that reopens this case", () => {
    renderWithClient(<ResultStep result={makeRender()} />);
    expect(screen.getByText(/keep this link/i)).toBeInTheDocument();
    expect(screen.getByText(/#case\/golden-case$/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Copy link/i })).toBeInTheDocument();
  });

  it("the placeholder in the picture frame never carries operator instructions",
    () => {
      // This copy used to read "Deploy in live mode (GMI + B2)" at whoever was
      // filing a claim, and it was the one string the plain-language e2e gate
      // cannot see (that spec scans landing/source/review, never this step).
      for (const degraded of [true, false]) {
        const { unmount } = renderWithClient(
          <ResultStep result={makeRender({ provider_degraded: degraded })} />,
        );
        const frame = screen.getByText(/still verifies below/i).closest("div");
        const text = frame?.textContent ?? "";
        expect(text).not.toMatch(/gmi|b2|deploy|live mode|—/i);
        unmount();
      }
    });

  it("does not call a failed live picture an offline run", () => {
    renderWithClient(<ResultStep result={makeRender({ provider_degraded: true })} />);
    expect(screen.getByText(/No picture was made for this case/i)).toBeInTheDocument();
    expect(screen.getByText(/illustration: unavailable/i)).toBeInTheDocument();
  });
});
