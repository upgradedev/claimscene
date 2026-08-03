/**
 * Plain-language wording for a live-illustration failure.
 *
 * The app already degrades honestly when the live provider fails: it re-seals
 * the same reviewed scene with the offline placeholder, so the case still
 * completes and the manifest still records that the illustration is not a real
 * generative render. What a visitor saw, though, was a long wait and then a
 * grey box. Our sibling entry lived exactly that: the shared account ran out of
 * credit, the failure was written plainly to the logs, and the person on the
 * page had no way to know any of it.
 *
 * The server sends one token from a closed set (see `claimscene/degrade.py`).
 * This turns that token into a sentence, and nothing else: no status codes, no
 * upstream message, no account or service name. The operator's copy of the
 * failure stays in the logs, where the detail belongs.
 *
 * It matters more here than in a photo app. The illustration is the DISCLOSED
 * layer; the schematic carries the facts. So every message below is paired
 * with the same reassurance, because it is true: the schematic is drawn by
 * this codebase from the scene the human confirmed, and a failure at the
 * illustration provider cannot touch it.
 */

/** The honest fallback: used for `unknown`, for a missing kind, and for any
 *  token this build has not heard of. */
const FALLBACK = "The illustration service failed, and it did not say why.";

/** What the visitor is told, per failure kind. */
const CAUSE: Record<string, string> = {
  credit: "The illustration service was out of credit, so it would not run.",
  auth: "The illustration service did not accept our sign-in, so it would not run.",
  rate_limit: "The illustration service was handling too many requests and turned us away.",
  timeout: "The illustration service took too long to answer, so we stopped waiting.",
  unavailable: "The illustration service could not be reached.",
  unknown: FALLBACK,
};

/** The one-line cause, in words a claimant can read. An unrecognised kind
 *  (a server that learned a new one) falls back to the honest "we do not
 *  know" sentence rather than blanking the notice. */
export function degradeCause(kind: string | undefined | null): string {
  if (!kind) return FALLBACK;
  return CAUSE[kind] ?? FALLBACK;
}

/** What it means for this case. Constant on purpose: the consequence does not
 *  depend on which way the service failed, and the schematic is unaffected
 *  every single time. */
export const DEGRADE_CONSEQUENCE =
  "Your case is still sealed and complete. The schematic below is the factual layer, " +
  "drawn here from the scene you confirmed, and it is unaffected. Only the AI picture " +
  "is missing, and it was never evidence.";

/** Heading for the notice. */
export const DEGRADE_TITLE = "The AI picture could not be made this time";
