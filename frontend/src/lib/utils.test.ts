import { describe, expect, it } from "vitest";
import {
  formatBytes,
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
