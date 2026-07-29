import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// lib/api.ts imports ONLY `getIdToken` from lib/auth — mocking that single
// export here is complete for this file's purposes (a partial mock of a
// module is only safe when it covers every export the code under test
// actually reaches, and this is the only one it reaches).
const mocks = vi.hoisted(() => ({ getIdToken: vi.fn() }));
vi.mock("./auth", () => ({ getIdToken: mocks.getIdToken }));

import { ApiError, claimsceneApi } from "./api";
import { emptyScene } from "./scene";

function mockFetch(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response);
}

function renderBody() {
  return {
    case_id: "case-1", manifest_hash: "a".repeat(64), manifest_url: "/cases/case-1",
    provider: "fake-media", degraded: true, provider_degraded: false,
    has_schematic_animation: false, schematic_kind: "static",
    schematic_url: "/cases/case-1/schematic", illustration_url: "/cases/case-1/illustration",
    report_markdown: "# report", scene: emptyScene(), warnings: [], artifacts: {},
  };
}

function manifestBody() {
  return {
    schema: "claimscene/manifest/v1", case_id: "case-1", created_at: "t", disclosure: "d",
    inputs: [], scene_graph: { schema: "s", sha256: "h" }, timeline: { schema: "t", sha256: "h" },
    schematic: {}, illustration: { provider: "fake-media", model: "m", prompt: "p", sha256: "h", degraded: true },
    report: { media_type: "text/markdown", sha256: "h" }, manifest_hash: "h",
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  mocks.getIdToken.mockReset();
});

describe("guest (getIdToken resolves null) — every call is byte-identical to before", () => {
  beforeEach(() => mocks.getIdToken.mockResolvedValue(null));

  it("render: reaches fetch with only request()'s default header, no Authorization", async () => {
    const fetchMock = mockFetch(200, renderBody());
    vi.stubGlobal("fetch", fetchMock);
    await claimsceneApi.render({ scene: emptyScene(), caseId: "c" });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    // The caller omits the `headers` key entirely for a guest, so request()'s
    // own default survives untouched — byte-identical to the pre-auth call.
    expect(init.headers).toEqual({ Accept: "application/json" });
  });

  it("submitRenderJob: reaches fetch with only request()'s default header, no Authorization", async () => {
    const fetchMock = mockFetch(202, { job_id: "j", status: "queued" });
    vi.stubGlobal("fetch", fetchMock);
    await claimsceneApi.submitRenderJob({ scene: emptyScene(), caseId: "c" });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.headers).toEqual({ Accept: "application/json" });
  });

  it("getRenderJob: only request()'s default header reaches fetch", async () => {
    const fetchMock = mockFetch(200, { job_id: "j", status: "queued" });
    vi.stubGlobal("fetch", fetchMock);
    await claimsceneApi.getRenderJob("j");
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.headers).toEqual({ Accept: "application/json" });
  });

  it("manifest: only request()'s default header reaches fetch", async () => {
    const fetchMock = mockFetch(200, manifestBody());
    vi.stubGlobal("fetch", fetchMock);
    await claimsceneApi.manifest("case-1");
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.headers).toEqual({ Accept: "application/json" });
  });

  it("extract: never attaches auth, even when signed in (backend route ignores tenant)", async () => {
    mocks.getIdToken.mockResolvedValue("would-be-ignored");
    // This test's whole point is asserting getIdToken is never called by
    // extract() — establish that from a definitively clean slate right here,
    // rather than relying solely on the previous test's own afterEach
    // (mockReset() in afterEach clears history for the NEXT test correctly,
    // but this extra mockClear() removes any doubt about ordering between
    // hooks and keeps the assertion below meaningful on its own terms).
    mocks.getIdToken.mockClear();
    const fetchMock = mockFetch(200, {
      scene: emptyScene(), inputs: [],
      extraction: { extractor: "fake", source: "fake_extraction", mode: "offline" },
    });
    vi.stubGlobal("fetch", fetchMock);
    await claimsceneApi.extract({ scenarioId: "s01_rear_end" });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.headers).toEqual({ Accept: "application/json" });
    expect(mocks.getIdToken).not.toHaveBeenCalled();
  });

  it("previewSchematic: never attaches auth, even when signed in (backend route ignores tenant)", async () => {
    mocks.getIdToken.mockResolvedValue("would-be-ignored");
    mocks.getIdToken.mockClear(); // see the identical comment on the extract test above
    const fetchMock = mockFetch(200, {
      svg: "<svg></svg>", warnings: [], contact_point: null, vehicle_count: 1,
      road: { layout: "straight", lanes_per_direction: 1, signal: "none" },
    });
    vi.stubGlobal("fetch", fetchMock);
    await claimsceneApi.previewSchematic(emptyScene());
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
    expect(mocks.getIdToken).not.toHaveBeenCalled();
  });
});

describe("signed in (getIdToken resolves a fresh token)", () => {
  beforeEach(() => mocks.getIdToken.mockResolvedValue("fresh-id-token"));

  it("render carries Authorization", async () => {
    const fetchMock = mockFetch(200, renderBody());
    vi.stubGlobal("fetch", fetchMock);
    await claimsceneApi.render({ scene: emptyScene(), caseId: "c" });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer fresh-id-token");
    expect(init.body).toBeInstanceOf(FormData);
  });

  it("submitRenderJob carries Authorization", async () => {
    const fetchMock = mockFetch(202, { job_id: "j", status: "queued" });
    vi.stubGlobal("fetch", fetchMock);
    await claimsceneApi.submitRenderJob({ scene: emptyScene(), caseId: "c" });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer fresh-id-token");
    expect(init.body).toBeInstanceOf(FormData);
  });

  it("getRenderJob carries Authorization", async () => {
    const fetchMock = mockFetch(200, { job_id: "j", status: "running" });
    vi.stubGlobal("fetch", fetchMock);
    await claimsceneApi.getRenderJob("j");
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.headers).toEqual({ Authorization: "Bearer fresh-id-token" });
  });

  it("manifest carries Authorization", async () => {
    const fetchMock = mockFetch(200, manifestBody());
    vi.stubGlobal("fetch", fetchMock);
    await claimsceneApi.manifest("case-1");
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.headers).toEqual({ Authorization: "Bearer fresh-id-token" });
  });

  it("asks for a fresh token on every call — two calls, two distinct tokens", async () => {
    mocks.getIdToken.mockReset();
    mocks.getIdToken.mockResolvedValueOnce("token-A").mockResolvedValueOnce("token-B");
    const fetchMock = mockFetch(200, renderBody());
    vi.stubGlobal("fetch", fetchMock);
    await claimsceneApi.render({ scene: emptyScene(), caseId: "c" });
    await claimsceneApi.render({ scene: emptyScene(), caseId: "c2" });
    expect(mocks.getIdToken).toHaveBeenCalledTimes(2);
    const first = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const second = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect((first.headers as Record<string, string>).Authorization).toBe("Bearer token-A");
    expect((second.headers as Record<string, string>).Authorization).toBe("Bearer token-B");
  });
});

describe("claimsceneApi.myLibrary", () => {
  it("returns the cases array on success and attaches Authorization", async () => {
    mocks.getIdToken.mockResolvedValue("lib-token");
    const fetchMock = mockFetch(200, {
      cases: [
        { case_id: "a", manifest_hash: "h1", created_at: "2026-01-01T00:00:00Z" },
        { case_id: "b", manifest_hash: null, created_at: null },
      ],
    });
    vi.stubGlobal("fetch", fetchMock);
    const cases = await claimsceneApi.myLibrary();
    expect(cases).toHaveLength(2);
    expect(cases[0]?.case_id).toBe("a");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/me/library");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer lib-token");
  });

  it("surfaces a 401 as ApiError (guest, or a token the backend rejects)", async () => {
    mocks.getIdToken.mockResolvedValue(null);
    vi.stubGlobal("fetch", mockFetch(401, { detail: "sign in required" }));
    const err = await claimsceneApi.myLibrary().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(401);
  });
});

describe("claimsceneApi.deleteMyData", () => {
  it("sends DELETE with Authorization and returns the deleted count", async () => {
    mocks.getIdToken.mockResolvedValue("del-token");
    const fetchMock = mockFetch(200, { deleted: 3 });
    vi.stubGlobal("fetch", fetchMock);
    const result = await claimsceneApi.deleteMyData();
    expect(result).toEqual({ deleted: 3 });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/me/data");
    expect(init.method).toBe("DELETE");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer del-token");
  });

  it("sends no Authorization when there is no token", async () => {
    mocks.getIdToken.mockResolvedValue(null);
    const fetchMock = mockFetch(200, { deleted: 0 });
    vi.stubGlobal("fetch", fetchMock);
    await claimsceneApi.deleteMyData();
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    // No `headers` key is added by the caller, so only request()'s default
    // remains and no Authorization is ever sent.
    expect(init.headers).toEqual({ Accept: "application/json" });
    expect(init.method).toBe("DELETE");
  });
});
