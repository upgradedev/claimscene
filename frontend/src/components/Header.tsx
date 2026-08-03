import { Wordmark } from "./Wordmark";
import { Badge } from "./ui/badge";
import { AuthMenu } from "./AuthMenu";
import { useHealth } from "@/lib/queries";

/** `onOpenLibrary` is only ever invoked from AuthMenu's signed-in "My cases"
 *  item, which itself renders nothing when Firebase config is absent (see
 *  lib/auth.ts::isAuthEnabled) — a build with no VITE_FIREBASE_* vars (every
 *  build today) never shows the control that could call it. */
export function Header({ onOpenLibrary }: { onOpenLibrary?: () => void }) {
  const health = useHealth();

  return (
    <header className="sticky top-0 z-40 border-b border-steel-700/70 bg-steel-950/80 backdrop-blur-md">
      <div className="container flex h-14 items-center justify-between">
        {/* min-h-11 gives the home link a ≥44px tap target on mobile without
            enlarging the 36px wordmark itself; reset from `sm` up. */}
        <a
          href="/"
          className="inline-flex min-h-11 items-center rounded sm:min-h-0"
          aria-label="ClaimScene home"
        >
          <Wordmark />
        </a>
        <div className="flex flex-wrap items-center justify-end gap-2">
          {/* Deliberately no "backend healthy" badge: that's an internal ops
              signal, not something a driver or a claims adjuster needs to see,
              and the old tooltip leaked developer vocabulary ("VLM", "B2
              storage") at people reading it after a crash. The health check
              itself (useHealth() above) still runs; only the success-path
              badge is gone. An unreachable backend is still worth surfacing
              below, since it explains why rendering might not work. */}
          {health.isError && (
            <Badge variant="muted" title="Backend unreachable">
              API offline
            </Badge>
          )}
          <AuthMenu onOpenLibrary={onOpenLibrary ?? (() => {})} />
        </div>
      </div>
    </header>
  );
}
