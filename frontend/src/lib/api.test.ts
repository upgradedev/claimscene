import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, claimsceneApi } from "./api";
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
