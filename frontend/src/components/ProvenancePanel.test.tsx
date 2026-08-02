import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// The verify helpers hit fetch/WebCrypto; mock the module so the panel's
// wiring (one Verify → seal recompute + named-check receipt) is what we assert.
vi.mock("@/lib/provenance-verify", () => ({
  verifyCaseProvenance: vi.fn(),
  fetchCaseVerification: vi.fn(),
}));

import { ProvenancePanel } from "./ProvenancePanel";
import { claimsceneApi, type Manifest, type RenderResponse } from "@/lib/api";
import { fetchCaseVerification, verifyCaseProvenance } from "@/lib/provenance-verify";

const RESULT = {
  case_id: "case-1",
  manifest_hash: "h".repeat(64),
  degraded: true,
} as RenderResponse;

function baseManifest(): Manifest {
  return {
    schema: "claimscene/manifest/v1",
    case_id: "case-1",
    created_at: "t",
    disclosure: "AI-generated illustration — not evidence",
    inputs: [{
      filename: "view_a.jpg", sha256: "s".repeat(64), media_type: "image/jpeg",
      role: "scene_photo", source: "synthetic_generated",
      attribution: "team", license: "CC0-1.0",
    }],
    scene_graph: { schema: "s", sha256: "h" },
    timeline: { schema: "t", sha256: "h" },
    schematic: {},
    illustration: { provider: "fake-media", model: "m", prompt: "p", sha256: "h", degraded: true },
    report: { media_type: "text/markdown", sha256: "h" },
    manifest_hash: "h".repeat(64),
  } as Manifest;
}

function withReview(over: Record<string, unknown> = {}): Manifest {
  return {
    ...baseManifest(),
    review: {
      schema: "claimscene/review/v1",
      classification: "interactive_demo",
      reviewer_id: "alex",
      reviewed_at: "t",
      scene_proposed_sha256: "p".repeat(64),
      scene_confirmed_sha256: "c".repeat(64),
      diff: [
        { path: "vehicles.veh_b.color", proposed: "silver", confirmed: "red", changed: true, changed_by: "human" },
        { path: "vehicles.veh_a.damage_clocks", proposed: [6], confirmed: [6, 9], changed: true, changed_by: "human" },
        { path: "impacts.veh_c.clock_position", proposed: null, confirmed: 3, changed: true, changed_by: "human" },
        { path: "road.signal", proposed: "none", confirmed: "none", changed: false, changed_by: "unchanged" },
      ],
      counts: { proposed_fields: 17, human_changed: 3, unchanged: 14 },
      prior_confidence_notes: ["vlm: unsure about the second car colour"],
      decision_digest: "d".repeat(64),
      ...over,
    },
  } as Manifest;
}

function mount(manifest: Manifest) {
  vi.spyOn(claimsceneApi, "manifest").mockResolvedValue(manifest);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ProvenancePanel result={RESULT} />
    </QueryClientProvider>,
  );
}

describe("ProvenancePanel — review ledger", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.clearAllMocks());

  it("surfaces the honesty counts, classification badge and before→after corrections", async () => {
    mount(withReview());
    // Classification is spelled out honestly.
    expect(await screen.findByText("interactive demo")).toBeInTheDocument();
    // Honesty counts (17 proposed / 3 corrected / 14 unchanged) — 17 and 14 are
    // unique; the 3 corrected is asserted via the expandable summary below.
    expect(screen.getByText("17")).toBeInTheDocument();
    expect(screen.getByText("14")).toBeInTheDocument();
    // The expandable per-field before→after list.
    fireEvent.click(screen.getByText(/3 corrected fields/i));
    expect(screen.getByText("vehicles.veh_b.color")).toBeInTheDocument();
    expect(screen.getByText("silver")).toBeInTheDocument();
    expect(screen.getByText("red")).toBeInTheDocument();
    // Array + null diff values format cleanly.
    expect(screen.getByText("[6]")).toBeInTheDocument();
    expect(screen.getByText("[6, 9]")).toBeInTheDocument();
    expect(screen.getByText("∅")).toBeInTheDocument();
    // The AI's real notes are carried forward, not lost.
    expect(screen.getByText(/unsure about the second car colour/i)).toBeInTheDocument();
  });

  it("shows 'confirmed with no changes' when nothing was corrected", async () => {
    mount(withReview({
      counts: { proposed_fields: 17, human_changed: 0, unchanged: 17 },
      diff: [{ path: "road.signal", proposed: "none", confirmed: "none", changed: false, changed_by: "unchanged" }],
    }));
    expect(await screen.findByText(/confirmed the AI proposal with no changes/i)).toBeInTheDocument();
  });

  it("labels an unverified render honestly (no AI baseline)", async () => {
    mount(withReview({
      classification: "unverified_no_baseline",
      scene_proposed_sha256: null,
      scene_confirmed_sha256: null,
      diff: [],
      counts: { proposed_fields: 0, human_changed: 0, unchanged: 0 },
      prior_confidence_notes: [],
    }));
    expect(await screen.findByText("no AI baseline")).toBeInTheDocument();
    expect(screen.getByText(/No AI baseline was recorded/i)).toBeInTheDocument();
  });

  it("does not render the ledger when the manifest has no review block", async () => {
    mount(baseManifest());
    // The manifest still loads (input attribution shows) but no ledger.
    expect(await screen.findByText("view_a.jpg")).toBeInTheDocument();
    expect(screen.queryByText(/review ledger/i)).not.toBeInTheDocument();
  });
});

describe("ProvenancePanel — authorship split", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.clearAllMocks());

  it("shows the sealed authorship split when the manifest carries one", async () => {
    const base = baseManifest();
    mount({
      ...base,
      illustration: {
        ...base.illustration,
        authorship_note:
          "Division of authorship: the geometry and camera path are computed; " +
          "the model contributes visual style only.",
      },
    });
    expect(await screen.findByText(/authorship split/i)).toBeInTheDocument();
    expect(screen.getByText(/Division of authorship/i)).toBeInTheDocument();
  });

  it("omits the authorship block on a manifest sealed before the field existed", async () => {
    mount(baseManifest());
    await screen.findByText("view_a.jpg"); // manifest loaded, no crash either way
    expect(screen.queryByText(/authorship split/i)).not.toBeInTheDocument();
  });
});

describe("ProvenancePanel — server-side named-check receipt", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.clearAllMocks());

  it("Verify recomputes the seal AND renders the named-check receipt (pass/fail + evidence)", async () => {
    vi.mocked(verifyCaseProvenance).mockResolvedValue({ state: "verified", detail: "seal ok" });
    vi.mocked(fetchCaseVerification).mockResolvedValue({
      state: "ok",
      receipt: {
        checks: [
          { id: "seal.manifest_hash", label: "Manifest seal recomputes", passed: true, evidence: "matches" },
          { id: "artifact.report", label: "Report bytes match", passed: false, evidence: "hash mismatch" },
        ],
        success: false,
        digest: "d".repeat(64),
      },
    });
    mount(withReview());
    fireEvent.click(await screen.findByRole("button", { name: /Verify in browser/i }));

    expect(await screen.findByText("Manifest seal recomputes")).toBeInTheDocument();
    expect(screen.getByText("Report bytes match")).toBeInTheDocument();
    expect(screen.getByText("hash mismatch")).toBeInTheDocument();
    expect(screen.getByText(/1\/2 from stored bytes/)).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument(); // receipt.success === false
    expect(vi.mocked(verifyCaseProvenance)).toHaveBeenCalledWith("case-1", RESULT.manifest_hash);
    expect(vi.mocked(fetchCaseVerification)).toHaveBeenCalledWith("case-1");
  });

  it("shows an honest message when server verification is unavailable", async () => {
    vi.mocked(verifyCaseProvenance).mockResolvedValue({ state: "verified", detail: "seal ok" });
    vi.mocked(fetchCaseVerification).mockResolvedValue({
      state: "unavailable",
      detail: "server verification not served here",
    });
    mount(withReview());
    fireEvent.click(await screen.findByRole("button", { name: /Verify in browser/i }));
    expect(await screen.findByText("server verification not served here")).toBeInTheDocument();
  });

  it("reflects a FAILED seal recompute in the badge and status text", async () => {
    vi.mocked(verifyCaseProvenance).mockResolvedValue({
      state: "failed",
      detail: "Recomputed SHA-256 does NOT match",
    });
    vi.mocked(fetchCaseVerification).mockResolvedValue({
      state: "ok",
      receipt: { checks: [], success: true, digest: "d" },
    });
    mount(withReview());
    fireEvent.click(await screen.findByRole("button", { name: /Verify in browser/i }));
    expect(await screen.findByText(/Verification failed/i)).toBeInTheDocument();
    expect(screen.getByText(/does NOT match/i)).toBeInTheDocument();
  });
});
