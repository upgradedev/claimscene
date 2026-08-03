import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import {
  ApiError,
  claimsceneApi,
  type ExtractRequest,
  type ExtractResponse,
  type Health,
  type LibraryCase,
  type Manifest,
  type RenderEstimate,
  type RenderJobStatus,
  type RenderJobSubmitResponse,
  type RenderRequest,
  type RenderResponse,
  type Scenario,
} from "./api";

export const queryKeys = {
  health: ["health"] as const,
  scenarios: ["scenarios"] as const,
  manifest: (id: string) => ["manifest", id] as const,
  caseResult: (id: string) => ["caseResult", id] as const,
  renderEstimate: ["renderEstimate"] as const,
  myLibrary: ["myLibrary"] as const,
};

export function useHealth(): UseQueryResult<Health> {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: () => claimsceneApi.health(),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

export function useScenarios(): UseQueryResult<Scenario[]> {
  return useQuery({
    queryKey: queryKeys.scenarios,
    queryFn: () => claimsceneApi.scenarios(),
    staleTime: 5 * 60_000,
  });
}

export function useManifest(caseId: string | null): UseQueryResult<Manifest> {
  return useQuery({
    queryKey: queryKeys.manifest(caseId ?? ""),
    queryFn: () => claimsceneApi.manifest(caseId as string),
    enabled: !!caseId,
    staleTime: Infinity,
  });
}

/** A sealed case fetched by id (GET /cases/{id}/result) — the resume path,
 *  when someone reopens a link instead of finishing the wizard in one sitting.
 *
 *  `retry: false` on purpose. The failure this query actually sees is a 404:
 *  an unknown id, an expired one, or a case belonging to another account. None
 *  of those improve by asking again, and a claimant staring at a spinner is
 *  worse than being told plainly that the link did not work. A sealed case is
 *  immutable, so `staleTime: Infinity` matches the manifest query. */
export function useCaseResult(caseId: string | null): UseQueryResult<RenderResponse> {
  return useQuery({
    queryKey: queryKeys.caseResult(caseId ?? ""),
    queryFn: () => claimsceneApi.caseResult(caseId as string),
    enabled: !!caseId,
    retry: false,
    staleTime: Infinity,
  });
}

/** Measured render durations (GET /cases/render/estimate), so the app can say
 *  how long this takes without anybody guessing. Refetched rarely: it moves
 *  only when a render completes, and a stale-by-a-minute median is fine.
 *  `retry: false` keeps a missing estimate cheap — the UI simply says nothing
 *  has been timed here yet. */
export function useRenderEstimate(): UseQueryResult<RenderEstimate> {
  return useQuery({
    queryKey: queryKeys.renderEstimate,
    queryFn: () => claimsceneApi.renderEstimate(),
    retry: false,
    staleTime: 60_000,
  });
}

/** The signed-in tenant's own case library (GET /me/library). `enabled` is
 *  driven by the caller (MyCases passes `useAuthUser().user !== null`) — this
 *  hook never fetches on its own just because it was rendered, since the
 *  route 401s for anyone who isn't signed in.
 *
 *  `retry: false` overrides the app-wide default: a 401 here means "this
 *  deployment doesn't have multitenancy configured, or I'm signed out" —
 *  neither improves by retrying, so MyCases can render its honest degrade
 *  message immediately instead of after a pointless extra round trip. */
export function useMyLibrary(enabled: boolean): UseQueryResult<LibraryCase[]> {
  return useQuery({
    queryKey: queryKeys.myLibrary,
    queryFn: () => claimsceneApi.myLibrary(),
    enabled,
    retry: false,
  });
}

/** Erase every object under the signed-in tenant's storage prefix
 *  (DELETE /me/data). On success, invalidates the library query so MyCases
 *  refetches and renders the now-empty state. */
export function useDeleteMyData() {
  const qc = useQueryClient();
  return useMutation<{ deleted: number }, Error, void>({
    mutationFn: () => claimsceneApi.deleteMyData(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.myLibrary });
    },
  });
}

export function useExtract() {
  return useMutation<ExtractResponse, Error, ExtractRequest>({
    mutationFn: (req) => claimsceneApi.extract(req),
  });
}

/** The synchronous render mutation (POST /cases/render). Kept for direct
 *  callers/tests; the studio wizard itself uses the async submit + poll pair
 *  below (useSubmitRenderJob + usePollRenderJob) so a multi-minute live
 *  render never blocks one HTTP request past the edge proxy's timeout. */
export function useRender() {
  return useMutation<RenderResponse, Error, RenderRequest>({
    mutationFn: (req) => claimsceneApi.render(req),
  });
}

/** Submit a case render as a background job (POST /cases/render/jobs) — the
 *  async counterpart to useRender. Resolves with `{job_id, status: "queued"}`
 *  immediately; pair with usePollRenderJob to observe the actual generation
 *  outcome. */
export function useSubmitRenderJob() {
  return useMutation<RenderJobSubmitResponse, Error, RenderRequest>({
    mutationFn: (req) => claimsceneApi.submitRenderJob(req),
  });
}

/** How often a submitted render job's status is polled. */
export const RENDER_JOB_POLL_INTERVAL_MS = 4_000;

/** Bounded total poll duration (~12 min) before the caller gives up and
 *  surfaces the honest "taking longer than expected" state, reused via a
 *  synthetic 504 ApiError below rather than a new UI branch. */
export const RENDER_JOB_MAX_POLL_MS = 12 * 60_000;

/** A poll tick that fails before even reaching a real job status (network
 *  blip, transient 5xx) is tolerated this many times IN A ROW before the
 *  poll gives up — a brief hiccup must never abort an in-progress
 *  multi-minute render. Resets on every successful tick. */
export const RENDER_JOB_MAX_CONSECUTIVE_ERRORS = 3;

export interface RenderJobPollOptions {
  /** Treat a 404 as final instead of a blip worth retrying.
   *
   *  The two callers want opposite things from the same failure. Mid-wizard,
   *  the job was just accepted, so a 404 is almost certainly a hiccup and
   *  abandoning a live render over it would be awful. Resuming from a link,
   *  a 404 is the normal answer for a link that is wrong, expired, or from
   *  another account, and making someone watch a spinner for four more polls
   *  before saying so is just a slower way to say the same thing. */
  unknownIsFinal?: boolean;
}

export interface RenderJobPollState {
  /** The sealed case once the job status is "done". */
  result: RenderResponse | null;
  /** The most recent status read back, whatever it said. Exposed so a caller
   *  resuming from a link can see `updated_at` and notice a job that stopped
   *  advancing (the in-process worker can be lost with the instance that ran
   *  it — see claimscene/jobs.py), rather than polling a dead job for the
   *  full ceiling. */
  status: RenderJobStatus | null;
  /** Set once the job status is "failed", the max poll duration elapses, or
   *  the consecutive-error budget is exhausted. Always an Error (usually the
   *  same ApiError getRenderJob already throws) so the caller's existing
   *  error-rendering branch — built for the old synchronous mutation — can
   *  be reused unchanged. */
  error: Error | null;
  /** True while a submitted job is still queued/running and being polled. */
  isPolling: boolean;
}

const IDLE_POLL_STATE: RenderJobPollState = {
  result: null, status: null, error: null, isPolling: false,
};

/**
 * Poll `GET /cases/render/jobs/{jobId}` on an interval until the job reaches
 * a terminal status, a bounded max duration elapses, or the component
 * unmounts. `jobId === null` means "nothing submitted yet" — idle, no
 * polling.
 *
 * Deliberately a plain interval loop, not `useQuery` + `refetchInterval`:
 * job polling needs a hard wall-clock ceiling (~12 min) and a small
 * consecutive-failure budget shared ACROSS ticks, which is a different shape
 * than react-query's per-fetch retry/backoff. Primes the manifest cache on
 * success exactly like useRender does. The very first tick fires immediately
 * (no interval wait) — offline/demo generation can finish near-instantly, so
 * a job may already be "done" the first time it's polled.
 */
export function usePollRenderJob(
  jobId: string | null,
  { unknownIsFinal = false }: RenderJobPollOptions = {},
): RenderJobPollState {
  const qc = useQueryClient();
  const [state, setState] = useState<RenderJobPollState>(IDLE_POLL_STATE);

  useEffect(() => {
    if (!jobId) {
      setState(IDLE_POLL_STATE);
      return;
    }
    let cancelled = false;
    // `number`, not ReturnType<typeof window.setTimeout>: with @types/node in
    // scope that alias resolves to Node's `Timeout`, while the DOM
    // `window.setTimeout` call below actually returns a `number`.
    let timer: number | undefined;
    let consecutiveErrors = 0;
    const startedAt = Date.now();
    let lastStatus: RenderJobStatus | null = null;
    setState({ result: null, status: null, error: null, isPolling: true });

    const giveUp = (error: Error) => {
      if (cancelled) return;
      setState({ result: null, status: lastStatus, error, isPolling: false });
    };

    // Schedules the next tick, unless the wall-clock ceiling has already
    // passed — in which case this poll honestly gives up as a timeout.
    const scheduleNext = () => {
      if (cancelled) return;
      if (Date.now() - startedAt >= RENDER_JOB_MAX_POLL_MS) {
        giveUp(new ApiError("This case is taking longer than expected to render.", 504));
        return;
      }
      timer = window.setTimeout(tick, RENDER_JOB_POLL_INTERVAL_MS);
    };

    async function tick() {
      if (cancelled) return;
      try {
        const status = await claimsceneApi.getRenderJob(jobId as string);
        if (cancelled) return;
        consecutiveErrors = 0;
        lastStatus = status;
        if (status.status === "done") {
          const result = status.result ?? null;
          if (result) qc.invalidateQueries({ queryKey: queryKeys.manifest(result.case_id) });
          setState({ result, status, error: null, isPolling: false });
          return;
        }
        if (status.status === "failed") {
          giveUp(new ApiError(status.error ?? "Render failed."));
          return;
        }
        setState({ result: null, status, error: null, isPolling: true });
        scheduleNext();
      } catch (err) {
        if (cancelled) return;
        if (unknownIsFinal && err instanceof ApiError && err.status === 404) {
          giveUp(err);
          return;
        }
        consecutiveErrors += 1;
        if (consecutiveErrors > RENDER_JOB_MAX_CONSECUTIVE_ERRORS) {
          giveUp(err instanceof Error ? err : new Error(String(err)));
          return;
        }
        scheduleNext();
      }
    }

    tick();

    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [jobId, qc, unknownIsFinal]);

  return state;
}
