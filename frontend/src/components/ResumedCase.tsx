import { useEffect } from "react";
import { AlertCircle, Loader2, Search } from "lucide-react";
import { ApiError } from "@/lib/api";
import { useCaseResult, usePollRenderJob } from "@/lib/queries";
import type { Route } from "@/lib/route";
import { useCaseStore } from "@/store/useCaseStore";
import { elapsedLabel } from "@/lib/utils";
import { Button } from "./ui/button";

/**
 * Reopening a case from its link.
 *
 * A render takes minutes, and the page that started it used to be the only
 * place the case existed. Refresh, tab crash, phone locking itself, and the
 * case was gone even though the work carried on and every sealed byte stayed
 * fetchable. With the id in the URL (see lib/route.ts), this component picks
 * a case back up from either end of that wait: a job still running, or a case
 * already sealed.
 *
 * Every unhappy answer here is a plain sentence and a way forward, never a
 * spinner that runs out of road:
 *
 *   - a link that is not shaped like a case link is rejected without a request
 *   - an id nobody can resolve says so, once, and offers a fresh start
 *   - a case belonging to another account is INDISTINGUISHABLE from an unknown
 *     one, by design: the server 404s both identically (it cannot see outside
 *     the caller's own tenant), so a shared link cannot even confirm that
 *     someone else's case exists
 *   - a job that stopped advancing is called that, rather than polled to the
 *     ceiling in silence
 */

/** A job whose status has not moved for this long has almost certainly lost
 *  the instance that was running it (the worker is in-process — see
 *  claimscene/jobs.py). Comfortably longer than the render itself, so a slow
 *  render is never called stalled. */
export const STALE_JOB_MS = 15 * 60_000;

function Panel({ icon, title, children, onStartNew }: {
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
  onStartNew?: () => void;
}) {
  return (
    <div className="animate-fade-up py-8">
      <div className="mx-auto max-w-lg">
        <div className="sheet sheet-ticks p-6">
          <h2 className="flex items-center gap-2 font-mono text-xl font-semibold text-blueprint-text">
            {icon} {title}
          </h2>
          <div className="mt-2 space-y-2 text-sm leading-relaxed text-blueprint-dim">
            {children}
          </div>
          {onStartNew && (
            <div className="mt-5">
              <Button size="lg" onClick={onStartNew}>Start a new case</Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** Resume a sealed case by id. */
function ResumedSealedCase({ caseId, onStartNew, onLoaded }: {
  caseId: string;
  onStartNew: () => void;
  onLoaded: () => void;
}) {
  const setResult = useCaseStore((s) => s.setResult);
  const { data, error, isLoading } = useCaseResult(caseId);

  useEffect(() => {
    if (!data) return;
    setResult(data);
    onLoaded();
  }, [data, setResult, onLoaded]);

  if (isLoading || data) {
    return (
      <Panel icon={<Loader2 className="h-5 w-5 animate-spin text-cyan-400" />} title="Opening your case">
        <p>Fetching the sealed files for this case.</p>
      </Panel>
    );
  }
  return <NotFound error={error} onStartNew={onStartNew} />;
}

/** Resume a render that was still running when the page went away. */
function ResumedJob({ jobId, onStartNew, onLoaded }: {
  jobId: string;
  onStartNew: () => void;
  onLoaded: () => void;
}) {
  const setResult = useCaseStore((s) => s.setResult);
  const poll = usePollRenderJob(jobId, { unknownIsFinal: true });

  useEffect(() => {
    if (!poll.result) return;
    setResult(poll.result);
    onLoaded();
  }, [poll.result, setResult, onLoaded]);

  if (poll.result) return null;

  if (poll.error) {
    if (poll.error instanceof ApiError && poll.error.status === 404) {
      return <NotFound error={poll.error} onStartNew={onStartNew} />;
    }
    return (
      <Panel icon={<AlertCircle className="h-5 w-5 text-red-400" />}
             title="This case did not finish" onStartNew={onStartNew}>
        <p>
          The work on this case stopped before it was sealed, so there is nothing to show
          yet. Your photos were not kept, so starting again is the way forward.
        </p>
      </Panel>
    );
  }

  const updatedAt = poll.status?.updated_at ? Date.parse(poll.status.updated_at) : NaN;
  const idleFor = Number.isNaN(updatedAt) ? 0 : Date.now() - updatedAt;
  if (idleFor > STALE_JOB_MS) {
    return (
      <Panel icon={<AlertCircle className="h-5 w-5 text-amber-400" />}
             title="This case stopped before it finished" onStartNew={onStartNew}>
        <p>
          Nothing has moved on this case for {elapsedLabel(idleFor)}, which is far longer
          than a case takes. It was most likely interrupted while it was being made.
        </p>
        <p>Starting again is the quickest way to get a sealed case.</p>
      </Panel>
    );
  }

  return (
    <Panel icon={<Loader2 className="h-5 w-5 animate-spin text-cyan-400" />}
           title="Your case is still being made">
      <p>
        This case was started before you came back, and it is still going. It will appear
        here as soon as it is sealed.
      </p>
      <p>
        Keep this page open if you can. The link in your address bar stays valid, so you
        can come back to it later.
      </p>
    </Panel>
  );
}

function NotFound({ error, onStartNew }: { error: unknown; onStartNew: () => void }) {
  const status = error instanceof ApiError ? error.status : undefined;
  const unreachable = status === undefined;
  return (
    <Panel icon={<Search className="h-5 w-5 text-amber-400" />}
           title="We could not find this case" onStartNew={onStartNew}>
      {unreachable ? (
        <p>
          We could not reach the service to look this case up. Check your connection and
          reload the page, or start again.
        </p>
      ) : (
        <>
          <p>
            Nothing here matches this link. It may have been mistyped, it may have expired,
            or the case may belong to a different account.
          </p>
          <p>
            If the case was made while signed in to another account, sign in with that
            account and open the link again.
          </p>
        </>
      )}
    </Panel>
  );
}

export function ResumedCase({ route, onStartNew, onLoaded }: {
  route: Route;
  onStartNew: () => void;
  onLoaded: () => void;
}) {
  if (route.kind === "case") {
    return <ResumedSealedCase caseId={route.id} onStartNew={onStartNew} onLoaded={onLoaded} />;
  }
  if (route.kind === "job") {
    return <ResumedJob jobId={route.id} onStartNew={onStartNew} onLoaded={onLoaded} />;
  }
  return (
    <Panel icon={<Search className="h-5 w-5 text-amber-400" />}
           title="This link does not name a case" onStartNew={onStartNew}>
      <p>
        The part of the address after the # is not a case link, so there is nothing to
        open. If someone sent you the link, ask them to send the whole thing.
      </p>
    </Panel>
  );
}
