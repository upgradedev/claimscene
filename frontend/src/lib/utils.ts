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
