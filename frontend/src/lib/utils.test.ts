import { describe, expect, it } from "vitest";
import {
  approxDuration,
  elapsedLabel,
  formatBytes,
  ghostSvg,
  illustrationIsPlayable,
  isPlayableUrl,
  shortHash,
  svgToDataUrl,
} from "./utils";

describe("svgToDataUrl", () => {
  it("produces a base64 svg data URL that round-trips (UTF-8 safe)", () => {
    const svg = "<svg><text>ILLUSTRATION — NOT EVIDENCE</text></svg>";
    const url = svgToDataUrl(svg);
    expect(url.startsWith("data:image/svg+xml;base64,")).toBe(true);
    const b64 = url.slice("data:image/svg+xml;base64,".length);
    // Reverse of unescape(encodeURIComponent(x)) is decodeURIComponent(escape(x)).
    expect(decodeURIComponent(escape(atob(b64)))).toBe(svg);
  });
});

describe("ghostSvg (AI-proposed geometry as a dashed ghost overlay)", () => {
  it("injects a style that transparentizes the background and dashes strokes", () => {
    const svg = '<svg viewBox="0 0 10 10"><rect fill="#111"/><line stroke="#0ff"/></svg>';
    const out = ghostSvg(svg);
    expect(out).toContain("<style>");
    expect(out).toContain("rect:first-of-type{opacity:0}");
    expect(out).toContain("stroke-dasharray:5 3");
    // Injected immediately after the opening <svg …> tag.
    expect(out.indexOf("<style>")).toBe(out.indexOf(">") + 1);
    // The original geometry is preserved (nothing is stripped).
    expect(out).toContain('<line stroke="#0ff"/>');
  });

  it("returns the input unchanged when there is no <svg> tag", () => {
    expect(ghostSvg("not an svg at all")).toBe("not an svg at all");
  });
});

describe("illustrationIsPlayable (playback-url selection)", () => {
  it("is false when the run degraded (deterministic offline bytes, not video)", () => {
    expect(illustrationIsPlayable({ degraded: true, illustration_url: "/cases/x/illustration" })).toBe(false);
  });
  it("is false without a usable url", () => {
    expect(illustrationIsPlayable({ degraded: false, illustration_url: null })).toBe(false);
    expect(illustrationIsPlayable({ degraded: false })).toBe(false);
  });
  it("is true for a real live clip via an api-relative or http url", () => {
    expect(illustrationIsPlayable({ degraded: false, illustration_url: "/cases/x/illustration" })).toBe(true);
    expect(illustrationIsPlayable({ degraded: false, illustration_url: "https://cdn/x.mp4" })).toBe(true);
  });
});

describe("isPlayableUrl", () => {
  it("accepts http(s) and api-relative, rejects b2:// and empty", () => {
    expect(isPlayableUrl("/cases/x/schematic")).toBe(true);
    expect(isPlayableUrl("https://x/y.mp4")).toBe(true);
    expect(isPlayableUrl("b2://bucket/key")).toBe(false);
    expect(isPlayableUrl(null)).toBe(false);
    expect(isPlayableUrl("")).toBe(false);
  });
});

describe("formatBytes / shortHash", () => {
  it("formats byte sizes", () => {
    expect(formatBytes(500)).toBe("500 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(null)).toBe("—");
  });
  it("truncates long hashes and passes short ones through", () => {
    expect(shortHash(null)).toBe("—");
    expect(shortHash("abcd")).toBe("abcd");
    expect(shortHash("0123456789abcdef0123456789abcdef")).toBe("01234567…89abcdef");
  });
});

describe("approxDuration (how long a render takes, said honestly)", () => {
  it("rounds to whole minutes above a minute and a half", () => {
    expect(approxDuration(240)).toBe("about 4 minutes");
    expect(approxDuration(255)).toBe("about 4 minutes");
    expect(approxDuration(100)).toBe("about 2 minutes");
    // Under 90s it stays in seconds rather than rounding "1 minute" onto a
    // wait that is nothing like a minute.
    expect(approxDuration(60)).toBe("about 60 seconds");
  });

  it("uses coarse seconds for short waits", () => {
    expect(approxDuration(42)).toBe("about 40 seconds");
    expect(approxDuration(3)).toBe("about 10 seconds");
  });

  it("returns null when there is nothing to report, so callers can say so", () => {
    expect(approxDuration(null)).toBeNull();
    expect(approxDuration(undefined)).toBeNull();
    expect(approxDuration(Number.NaN)).toBeNull();
    expect(approxDuration(-5)).toBeNull();
  });
});

describe("elapsedLabel", () => {
  it("counts up in minutes and seconds", () => {
    expect(elapsedLabel(0)).toBe("0 s");
    expect(elapsedLabel(45_000)).toBe("45 s");
    expect(elapsedLabel(80_000)).toBe("1 min 20 s");
    expect(elapsedLabel(600_000)).toBe("10 min 0 s");
  });

  it("never shows a negative wait", () => {
    expect(elapsedLabel(-1000)).toBe("0 s");
  });
});
