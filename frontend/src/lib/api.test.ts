import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, ManifestSchema, claimsceneApi } from "./api";
import { emptyScene } from "./scene";

// ── fetch stubbing ────────────────────────────────────────────────────────────
function jsonResponse(body: unknown, init: { ok?: boolean; status?: number } = {}) {
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    json: async () => body,
  } as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

const HEALTH = { status: "ok", service: "claimscene-api", mode: "offline",
                 provider: "fake-media", extractor: "fake-vision", storage: "InMemoryStorage" };

const SCENARIO = { id: "s01_rear_end", title: "Rear End", context: "c",
                   summary: "2 vehicles", images: ["a.jpg"], thumbnail: "/x.jpg" };

function renderBody() {
  return {
    case_id: "case-1", manifest_hash: "a".repeat(64), manifest_url: "/cases/case-1",
    provider: "fake-media", degraded: true, provider_degraded: false,
    has_schematic_animation: false, schematic_kind: "static",
    schematic_url: "/cases/case-1/schematic", illustration_url: "/cases/case-1/illustration",
    report_markdown: "# report", scene: emptyScene(), warnings: [], artifacts: {},
  };
}

describe("claimsceneApi (typed client + zod validation)", () => {
  it("health() parses the health surface", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(HEALTH));
    const health = await claimsceneApi.health();
    expect(health.service).toBe("claimscene-api");
    expect(fetchMock.mock.calls[0]![0]).toBe("/health");
  });

  it("scenarios() unwraps the scenarios array", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ scenarios: [SCENARIO] }));
    const scenarios = await claimsceneApi.scenarios();
    expect(scenarios).toHaveLength(1);
    expect(scenarios[0]!.id).toBe("s01_rear_end");
  });

  it("extract() posts multipart with scenario_id / context / roles / files", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      scene: emptyScene(), inputs: [],
      extraction: { extractor: "fake-vision", source: "fake_extraction", mode: "offline" },
    }));
    await claimsceneApi.extract({
      scenarioId: "s01_rear_end", context: "note", roles: ["scene_photo"],
      files: [new File(["x"], "p.png", { type: "image/png" })],
    });
    const [path, init] = fetchMock.mock.calls[0]!;
    expect(path).toBe("/cases/extract");
    expect(init!.method).toBe("POST");
    const body = init!.body as FormData;
    expect(body).toBeInstanceOf(FormData);
    expect(body.get("scenario_id")).toBe("s01_rear_end");
    expect(body.get("context")).toBe("note");
    expect(body.get("roles")).toBe("scene_photo");
    expect(body.getAll("files")).toHaveLength(1);
  });

  it("previewSchematic() posts the scene as JSON", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      svg: "<svg></svg>", warnings: [], contact_point: null, vehicle_count: 1,
      road: { layout: "straight", lanes_per_direction: 1, signal: "none" },
    }));
    const res = await claimsceneApi.previewSchematic(emptyScene());
    expect(res.svg).toContain("svg");
    const [path, init] = fetchMock.mock.calls[0]!;
    expect(path).toBe("/cases/preview-schematic");
    expect((init!.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("render() posts multipart with the serialized scene", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(renderBody()));
    const res = await claimsceneApi.render({ scene: emptyScene(), caseId: "c" });
    expect(res.case_id).toBe("case-1");
    const body = fetchMock.mock.calls[0]![1]!.body as FormData;
    expect(JSON.parse(body.get("scene") as string).schema).toBe("claimscene/scene/v1");
    expect(body.get("case_id")).toBe("c");
    // No review fields when not reviewing against a baseline.
    expect(body.get("proposed_scene")).toBeNull();
    expect(body.get("review_classification")).toBeNull();
  });

  it("render() sends the AI-proposed baseline + honest classification for the receipt", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(renderBody()));
    await claimsceneApi.render({
      scene: emptyScene(), caseId: "c", proposedScene: emptyScene(),
      reviewClassification: "interactive_demo", reviewerId: "alex",
    });
    const body = fetchMock.mock.calls[0]![1]!.body as FormData;
    expect(JSON.parse(body.get("proposed_scene") as string).schema).toBe("claimscene/scene/v1");
    expect(body.get("review_classification")).toBe("interactive_demo");
    expect(body.get("reviewer_id")).toBe("alex");
  });

  it("manifest() url-encodes the case id", async () => {
    const manifest = { schema: "claimscene/manifest/v1", case_id: "a/b", created_at: "t",
      disclosure: "d", inputs: [], scene_graph: { schema: "s", sha256: "h" },
      timeline: { schema: "t", sha256: "h" }, schematic: {},
      illustration: { provider: "genblaze", model: "m", prompt: "p", sha256: "h", degraded: false },
      report: { media_type: "text/markdown", sha256: "h" }, manifest_hash: "h" };
    fetchMock.mockResolvedValueOnce(jsonResponse(manifest));
    await claimsceneApi.manifest("a/b");
    expect(fetchMock.mock.calls[0]![0]).toBe("/cases/a%2Fb");
  });

  it("ManifestSchema parses an optional review block (and back-compat without one)", () => {
    const base = {
      schema: "claimscene/manifest/v1", case_id: "c", created_at: "t", disclosure: "d",
      inputs: [], scene_graph: { schema: "s", sha256: "h" },
      timeline: { schema: "t", sha256: "h" }, schematic: {},
      illustration: { provider: "fake-media", model: "m", prompt: "p", sha256: "h", degraded: true },
      report: { media_type: "text/markdown", sha256: "h" }, manifest_hash: "h",
    };
    expect(ManifestSchema.parse(base).review).toBeUndefined(); // back-compat
    const withReview = {
      ...base,
      review: {
        schema: "claimscene/review/v1", classification: "interactive_demo",
        reviewer_id: "r", reviewed_at: "t", scene_proposed_sha256: "a",
        scene_confirmed_sha256: "b",
        diff: [{ path: "road.signal", proposed: "none", confirmed: "stop_sign",
                 changed: true, changed_by: "human" }],
        counts: { proposed_fields: 1, human_changed: 1, unchanged: 0 },
        prior_confidence_notes: ["n"], decision_digest: "d",
      },
    };
    const parsed = ManifestSchema.parse(withReview);
    expect(parsed.review?.classification).toBe("interactive_demo");
    expect(parsed.review?.counts.human_changed).toBe(1);
    expect(parsed.review?.diff[0]?.confirmed).toBe("stop_sign");
  });

  // ── error surfaces ──────────────────────────────────────────────────────────
  it("throws ApiError with the status on a non-2xx response", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({}, { ok: false, status: 422 }));
    await expect(claimsceneApi.health()).rejects.toMatchObject({ name: "ApiError", status: 422 });
  });

  it("wraps a network failure in a friendly ApiError", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("boom"));
    await expect(claimsceneApi.health()).rejects.toThrow(/Network unreachable/);
  });

  it("throws on a malformed (non-JSON) body", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true, status: 200, json: async () => { throw new Error("bad json"); },
    } as unknown as Response);
    await expect(claimsceneApi.health()).rejects.toThrow(/Malformed response/);
  });

  it("throws on an unexpected response shape (zod)", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ nope: true }));
    const err = await claimsceneApi.health().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toMatch(/Unexpected response shape/);
  });
});
