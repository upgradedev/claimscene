/**
 * Hash routes, so a case survives the tab that made it.
 *
 * A render runs as a background job and takes minutes. Before this, the job id
 * and the sealed case lived only in the page's memory: a refresh, an
 * accidental close, or a browser reload lost the case even though the work
 * carried on server-side and every byte of the sealed case stayed fetchable by
 * id. Putting the id in the URL makes the case resumable, and makes a sealed
 * case something a claimant can actually keep and send to an adjuster.
 *
 * The app has no router library and uses `window.location.hash` already
 * (`#start`), so this stays a hash route: no dependency, no server rewrite
 * rules, and a link that works on any static host.
 *
 *   #start          the studio, from the beginning (unchanged)
 *   #job/<id>       a render in flight; resume polling it
 *   #case/<id>      a sealed case; fetch and show it
 *
 * Ids are validated here, before any network call. A case id is sanitised
 * server-side into `[A-Za-z0-9._-]` (see `claimscene.keys.safe_component`) and
 * a job id is a `secrets.token_urlsafe` token, so anything outside that
 * charset cannot name a real case: it is `unreadable`, and the UI says the
 * link looks wrong instead of firing a request that would 404 anyway.
 */

/** What the current URL names. */
export type Route =
  | { kind: "home" }
  | { kind: "start" }
  | { kind: "job"; id: string }
  | { kind: "case"; id: string }
  /** A hash that claims to name a case but cannot: wrong shape, empty or
   *  over-long id, characters no real id can contain. */
  | { kind: "unreadable" };

/** Both id families fit inside this charset; the cap is well above any real
 *  id (a case id is capped at 64 server-side) and keeps a pathological URL
 *  out of a request path. */
const ID = /^[A-Za-z0-9._-]{1,128}$/;

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#/, "");
  if (raw === "") return { kind: "home" };
  if (raw === "start") return { kind: "start" };

  const match = /^(job|case)\/(.*)$/.exec(raw);
  if (!match) return { kind: "home" };

  let id: string;
  try {
    // A pasted link may be percent-encoded. Decoding first means a malformed
    // escape (`%zz`) is caught here rather than thrown at whoever uses the id.
    id = decodeURIComponent(match[2] ?? "");
  } catch {
    return { kind: "unreadable" };
  }
  if (!ID.test(id)) return { kind: "unreadable" };
  return { kind: match[1] === "job" ? "job" : "case", id };
}

export const jobHash = (id: string): string => `#job/${encodeURIComponent(id)}`;
export const caseHash = (id: string): string => `#case/${encodeURIComponent(id)}`;
export const START_HASH = "#start";

/**
 * Point the URL at `hash` WITHOUT adding a history entry and WITHOUT firing a
 * `hashchange`.
 *
 * Both properties are load-bearing. `location.hash = ...` fires `hashchange`,
 * which the app listens to in order to react to a real navigation (a pasted
 * link, the back button); if our own writes fired it too, submitting a render
 * would bounce the app back into its own resume view mid-flow. And a history
 * entry per step would turn "back" into a walk through the wizard rather than
 * a way out of the app.
 *
 * Falls back to a direct assignment where `replaceState` is unavailable, since
 * a correct URL matters more than a tidy history.
 */
export function replaceHash(hash: string): void {
  if (typeof window === "undefined") return;
  if (window.location.hash === hash) return;
  try {
    window.history.replaceState(null, "", hash);
  } catch {
    window.location.hash = hash;
  }
}

/** The shareable absolute link for a sealed case. */
export function caseLink(caseId: string): string {
  if (typeof window === "undefined") return caseHash(caseId);
  const { origin, pathname } = window.location;
  return `${origin}${pathname}${caseHash(caseId)}`;
}
