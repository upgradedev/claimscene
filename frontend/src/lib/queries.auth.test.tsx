import { afterEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { createElement } from "react";
import { useDeleteMyData, useMyLibrary } from "./queries";
import { claimsceneApi, type LibraryCase } from "./api";

function wrapper(qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })) {
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: qc }, children);
}

const CASES: LibraryCase[] = [
  { case_id: "a", manifest_hash: "h", created_at: "2026-01-01T00:00:00Z" },
];

afterEach(() => vi.restoreAllMocks());

describe("useMyLibrary", () => {
  it("stays disabled (never fetches) when enabled=false", () => {
    const spy = vi.spyOn(claimsceneApi, "myLibrary");
    const { result } = renderHook(() => useMyLibrary(false), { wrapper: wrapper() });
    expect(result.current.fetchStatus).toBe("idle");
    expect(spy).not.toHaveBeenCalled();
  });

  it("fetches the library once enabled=true", async () => {
    vi.spyOn(claimsceneApi, "myLibrary").mockResolvedValue(CASES);
    const { result } = renderHook(() => useMyLibrary(true), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(CASES);
  });

  it("does not retry on failure — a 401 would never succeed on retry", async () => {
    const spy = vi.spyOn(claimsceneApi, "myLibrary").mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useMyLibrary(true), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(spy).toHaveBeenCalledTimes(1);
  });
});

describe("useDeleteMyData", () => {
  it("resolves the deleted count", async () => {
    vi.spyOn(claimsceneApi, "deleteMyData").mockResolvedValue({ deleted: 2 });
    const { result } = renderHook(() => useDeleteMyData(), { wrapper: wrapper() });
    result.current.mutate(undefined);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual({ deleted: 2 });
  });

  it("invalidates the library query on success, so a mounted MyCases view refetches", async () => {
    vi.spyOn(claimsceneApi, "myLibrary").mockResolvedValue(CASES);
    vi.spyOn(claimsceneApi, "deleteMyData").mockResolvedValue({ deleted: 1 });
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const w = wrapper(qc);

    const library = renderHook(() => useMyLibrary(true), { wrapper: w });
    await waitFor(() => expect(library.result.current.isSuccess).toBe(true));

    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    const del = renderHook(() => useDeleteMyData(), { wrapper: w });
    del.result.current.mutate(undefined);
    await waitFor(() => expect(del.result.current.isSuccess).toBe(true));

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["myLibrary"] });
  });

  it("surfaces a failed delete as an error, not a silent no-op", async () => {
    vi.spyOn(claimsceneApi, "deleteMyData").mockRejectedValue(new Error("network down"));
    const { result } = renderHook(() => useDeleteMyData(), { wrapper: wrapper() });
    result.current.mutate(undefined);
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error?.message).toBe("network down");
  });
});
