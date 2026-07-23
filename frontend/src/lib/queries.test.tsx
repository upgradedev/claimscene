import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  queryKeys,
  useExtract,
  useHealth,
  useManifest,
  useRender,
  useScenarios,
} from "./queries";
import { claimsceneApi } from "./api";
import { emptyScene } from "./scene";

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

const HEALTH = { status: "ok", service: "claimscene-api", mode: "offline" };

describe("query keys", () => {
  it("are stable and parameterised", () => {
    expect(queryKeys.health).toEqual(["health"]);
    expect(queryKeys.scenarios).toEqual(["scenarios"]);
    expect(queryKeys.manifest("x")).toEqual(["manifest", "x"]);
  });
});

describe("react-query hooks", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("useHealth fetches health", async () => {
    vi.spyOn(claimsceneApi, "health").mockResolvedValue(HEALTH);
    const { result } = renderHook(() => useHealth(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data!.mode).toBe("offline");
  });

  it("useScenarios fetches the scenario list", async () => {
    vi.spyOn(claimsceneApi, "scenarios").mockResolvedValue([
      { id: "s1", title: "T", context: "c", summary: "s", images: [], thumbnail: null },
    ]);
    const { result } = renderHook(() => useScenarios(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
  });

  it("useManifest is disabled without a case id and enabled with one", async () => {
    const spy = vi.spyOn(claimsceneApi, "manifest").mockResolvedValue({} as never);
    const { result: off } = renderHook(() => useManifest(null), { wrapper: wrapper() });
    expect(off.current.fetchStatus).toBe("idle"); // gated: never fires
    expect(spy).not.toHaveBeenCalled();

    renderHook(() => useManifest("case-1"), { wrapper: wrapper() });
    await waitFor(() => expect(spy).toHaveBeenCalledWith("case-1"));
  });

  it("useExtract runs the extract mutation", async () => {
    const spy = vi.spyOn(claimsceneApi, "extract").mockResolvedValue({
      scene: emptyScene(), inputs: [],
      extraction: { extractor: "fake", source: "fake_extraction", mode: "offline" },
    });
    const { result } = renderHook(() => useExtract(), { wrapper: wrapper() });
    result.current.mutate({ scenarioId: "s01_rear_end" });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(spy).toHaveBeenCalledWith({ scenarioId: "s01_rear_end" });
  });

  it("useRender runs the render mutation", async () => {
    const spy = vi.spyOn(claimsceneApi, "render").mockResolvedValue({ case_id: "c" } as never);
    const { result } = renderHook(() => useRender(), { wrapper: wrapper() });
    result.current.mutate({ scene: emptyScene() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(spy).toHaveBeenCalledOnce();
  });
});
