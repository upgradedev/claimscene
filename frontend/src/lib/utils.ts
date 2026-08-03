import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge Tailwind class names with conflict resolution (shadcn convention). */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** Truncate a hex hash for compact display: `a1b2c3d4…e5f6a7b8`. */
export function shortHash(hash: string | null | undefined, edge = 8): string {
  if (!hash) return "—";
  if (hash.length <= edge * 2 + 1) return hash;
  return `${hash.slice(0, edge)}…${hash.slice(-edge)}`;
}

/** Human-readable byte size. */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || Number.isNaN(bytes)) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[i]}`;
}

/** A rough, honest duration: "about 4 minutes", "about 40 seconds".
 *
 *  Deliberately coarse. The number behind it is a median over a handful of
 *  real renders, so quoting it to the second would claim a precision we do not
 *  have. Under 90 seconds reads in 10-second steps, above that in whole
 *  minutes. Returns null for anything that is not a usable number, so a caller
 *  can say "we have not measured this" instead of printing a broken value. */
export function approxDuration(seconds: number | null | undefined): string | null {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return null;
  if (seconds < 90) {
    const step = Math.max(10, Math.round(seconds / 10) * 10);
    return `about ${step} seconds`;
  }
  const minutes = Math.max(1, Math.round(seconds / 60));
  return `about ${minutes} minute${minutes === 1 ? "" : "s"}`;
}

/** Elapsed wall-clock, counted up: "1 min 20 s", "45 s". */
export function elapsedLabel(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return minutes ? `${minutes} min ${seconds} s` : `${seconds} s`;
}

/** Encode a server-produced SVG string as a base64 data URL for an <img>.
 *
 *  We render every schematic preview through an <img src="data:image/svg+xml…">
 *  rather than injecting the SVG into the DOM: an <img>-loaded SVG cannot run
 *  scripts, so even if a hostile string slipped past the backend's text-escape
 *  it could never execute. `unescape(encodeURIComponent(...))` makes btoa
 *  UTF-8-safe (the schematic contains an em-dash watermark). */
export function svgToDataUrl(svg: string): string {
  const utf8 = unescape(encodeURIComponent(svg));
  return `data:image/svg+xml;base64,${btoa(utf8)}`;
}

/** Turn a schematic SVG into a "ghost" overlay of the AI-PROPOSED geometry:
 *  transparent background, dashed strokes, and semi-transparent fills, so it can
 *  be drawn over the SOLID human-confirmed schematic without hiding it — the
 *  human-in-the-loop delta made visible on the artifact. The rules are injected
 *  as an internal <style>; because the SVG is still rendered via <img>, the
 *  style applies but no script can run. Returns the input unchanged when it has
 *  no <svg> tag (defensive). */
export function ghostSvg(svg: string): string {
  const style =
    "<style>rect:first-of-type{opacity:0}" +
    "line,rect,circle,polygon,path{stroke-dasharray:5 3}" +
    "polygon,circle{fill-opacity:.4}</style>";
  return svg.replace(/(<svg\b[^>]*>)/i, `$1${style}`);
}

/** Whether a URL is loadable in the browser (http/https/relative api routes). */
export function isPlayableUrl(url: string | null | undefined): url is string {
  return !!url && (/^https?:\/\//i.test(url) || url.startsWith("/"));
}

/** Resolve whether the illustration clip is real, decodable video.
 *
 *  Offline (or on an honest per-request degrade) the "clip" is deterministic
 *  fake bytes — a real sealed artifact, but NOT decodable video. It must never
 *  reach a <video> element; the UI shows the disclosed placeholder instead. The
 *  factual-layer schematic, by contrast, is genuine in every mode. */
export function illustrationIsPlayable(render: {
  degraded?: boolean;
  illustration_url?: string | null;
}): render is { illustration_url: string } {
  return render.degraded !== true && isPlayableUrl(render.illustration_url);
}
