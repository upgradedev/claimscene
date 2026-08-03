import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ResumedCase, STALE_JOB_MS } from "./ResumedCase";
import { ApiError, claimsceneApi } from "@/lib/api";
import { RENDER_JOB_POLL_INTERVAL_MS } from "@/lib/queries";
import type { Route } from "@/lib/route";
import { emptyScene } from "@/lib/scene";
import { useCaseStore } from "@/store/useCaseStore";

function sealedCase(overrides: Record<string, unknown> = {}) {
  return {
    case_id: "sealed-1", manifest_hash: "a".repeat(64), manifest_url: "/cases/sealed-1",
    provider: "fake-media", degraded: true, provider_degraded: false,
    has_schematic_animation: false, schematic_kind: "static",
    schematic_url: "/cases/sealed-1/schematic", illustration_url: "/cases/sealed-1/illustration",
    report_markdown: "# report", scene: emptyScene(), warnings: [], artifacts: {},
    ...overrides,
  };
}

function renderResume(route: Route) {
  const onStartNew = vi.fn();
  const onLoaded = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={qc}>
      <ResumedCase route={route} onStartNew={onStartNew} onLoaded={onLoaded} />
    </QueryClientProvider>,
  );
  return { ...utils, onStartNew, onLoaded };
}

describe("reopening a case from its link", () => {
  beforeEach(() => {
    useCaseStore.getState().reset();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // ── a sealed case ─────────────────────────────────────────────────────────
  it("fetches a sealed case by id and hands it to the studio", async () => {
    const body = sealedCase();
    const spy = vi.spyOn(claimsceneApi, "caseResult").mockResolvedValue(body as never);

    const { onLoaded } = renderResume({ kind: "case", id: "sealed-1" });
    expect(screen.getByText(/Opening your case/i)).toBeInTheDocument();

    await waitFor(() => expect(useCaseStore.getState().result?.case_id).toBe("sealed-1"));
    expect(spy).toHaveBeenCalledWith("sealed-1");
    expect(useCaseStore.getState().step).toBe("result");
    expect(onLoaded).toHaveBeenCalled();
  });

  it("says so plainly when the case cannot be found, and offers a fresh start",
    async () => {
      // Unknown, expired and another tenant's case are the SAME 404 from the
      // server (it cannot see outside the caller's own data), so they are the
      // same message here. A link must not confirm someone else's case exists.
      vi.spyOn(claimsceneApi, "caseResult")
        .mockRejectedValue(new ApiError("Request to /cases/x/result failed (404).", 404));

      const { onStartNew } = renderResume({ kind: "case", id: "someone-elses" });
      await screen.findByText(/could not find this case/i);
      expect(screen.getByText(/may belong to a different account/i)).toBeInTheDocument();
      // Never leaks the raw request line.
      expect(screen.queryByText(/failed \(404\)/)).not.toBeInTheDocument();

      screen.getByRole("button", { name: /Start a new case/i }).click();
      expect(onStartNew).toHaveBeenCalled();
    });

  it("distinguishes an unreachable service from a missing case", async () => {
    vi.spyOn(claimsceneApi, "caseResult")
      .mockRejectedValue(new ApiError("Network unreachable — check your connection."));
    renderResume({ kind: "case", id: "sealed-1" });
    await screen.findByText(/could not reach the service/i);
  });

  // ── a render still running ────────────────────────────────────────────────
  it("picks a running render back up and shows it when it lands", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const body = sealedCase({ case_id: "sealed-2" });
    vi.spyOn(claimsceneApi, "getRenderJob")
      .mockResolvedValueOnce({ job_id: "j1", status: "running",
                               updated_at: new Date().toISOString() } as never)
      .mockResolvedValue({ job_id: "j1", status: "done", result: body } as never);

    renderResume({ kind: "job", id: "j1" });
    await screen.findByText(/still being made/i);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(RENDER_JOB_POLL_INTERVAL_MS + 50);
    });
    await waitFor(() => expect(useCaseStore.getState().result?.case_id).toBe("sealed-2"));
  });

  it("tells the visitor the link keeps working while they wait", async () => {
    vi.spyOn(claimsceneApi, "getRenderJob")
      .mockResolvedValue({ job_id: "j1", status: "running",
                           updated_at: new Date().toISOString() } as never);
    renderResume({ kind: "job", id: "j1" });
    await screen.findByText(/link in your address bar stays valid/i);
  });

  it("calls a job that stopped advancing stalled instead of polling in silence",
    async () => {
      const stale = new Date(Date.now() - STALE_JOB_MS - 60_000).toISOString();
      vi.spyOn(claimsceneApi, "getRenderJob")
        .mockResolvedValue({ job_id: "j1", status: "running", updated_at: stale } as never);

      const { onStartNew } = renderResume({ kind: "job", id: "j1" });
      await screen.findByText(/stopped before it finished/i);
      screen.getByRole("button", { name: /Start a new case/i }).click();
      expect(onStartNew).toHaveBeenCalled();
    });

  it("gives up immediately on an unknown job id rather than retrying it", async () => {
    // Mid-wizard a 404 is worth retrying; resuming from a link it is simply
    // the answer, and four more polls would only make someone wait for it.
    const spy = vi.spyOn(claimsceneApi, "getRenderJob")
      .mockRejectedValue(new ApiError("Request failed (404).", 404));
    renderResume({ kind: "job", id: "gone" });
    await screen.findByText(/could not find this case/i);
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("reports a failed render as a case that did not finish", async () => {
    vi.spyOn(claimsceneApi, "getRenderJob")
      .mockResolvedValue({ job_id: "j1", status: "failed", error: "RuntimeError" } as never);
    renderResume({ kind: "job", id: "j1" });
    await screen.findByText(/did not finish/i);
    // The exception class name is for the logs, not for a claimant.
    expect(screen.queryByText(/RuntimeError/)).not.toBeInTheDocument();
  });

  // ── a link that is not a case link ────────────────────────────────────────
  it("rejects a malformed link without making a request", async () => {
    const caseSpy = vi.spyOn(claimsceneApi, "caseResult");
    const jobSpy = vi.spyOn(claimsceneApi, "getRenderJob");
    const { onStartNew } = renderResume({ kind: "unreadable" });

    expect(screen.getByText(/does not name a case/i)).toBeInTheDocument();
    expect(caseSpy).not.toHaveBeenCalled();
    expect(jobSpy).not.toHaveBeenCalled();

    screen.getByRole("button", { name: /Start a new case/i }).click();
    expect(onStartNew).toHaveBeenCalled();
  });

  it("never shows developer vocabulary on any unhappy path", async () => {
    vi.spyOn(claimsceneApi, "caseResult")
      .mockRejectedValue(new ApiError("Request to /cases/x/result failed (404).", 404));
    renderResume({ kind: "case", id: "nope" });
    await screen.findByText(/could not find this case/i);
    const text = document.body.innerText || document.body.textContent || "";
    for (const jargon of [/\bAPI\b/, /\b404\b/, /\bendpoint\b/i, /\bbackend\b/i,
                          /\bundefined\b/, /\bNaN\b/, /—/]) {
      expect(text, `leaked ${jargon.source}`).not.toMatch(jargon);
    }
  });
});
