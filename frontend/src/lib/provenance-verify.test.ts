import { describe, expect, it, vi } from "vitest";
import {
  parseJsonPreservingLexemes,
  pythonCanonicalJson,
  pythonEscapeString,
  sha256HexUtf8,
  verifyCaseProvenance,
  verifyManifestText,
} from "./provenance-verify";
// The GOLDEN fixture is the VERBATIM raw HTTP body of GET /cases/{id} produced
// by the REAL ClaimScene backend (FastAPI TestClient, offline). Its sealed
// disclosure carries an em-dash (U+2014), so the fixture exercises Python's
// ensure_ascii \uXXXX escaping — the exact path a naive JS canonicalizer breaks.
import goldenRaw from "../test/fixtures/golden-manifest.json?raw";
import goldenExpected from "../test/fixtures/golden-manifest.expected.json";

describe("pythonEscapeString (json.dumps ensure_ascii=True parity)", () => {
  it("escapes non-ASCII as lowercase \\uXXXX (the sealed em-dash)", () => {
    expect(pythonEscapeString("—")).toBe("\\u2014");
    expect(pythonEscapeString("μ")).toBe("\\u03bc");
  });
  it("escapes astral characters as surrogate pairs (Python parity)", () => {
    expect(pythonEscapeString("🎬")).toBe("\\ud83c\\udfac");
  });
  it("uses short escapes and escapes DEL/control like Python", () => {
    expect(pythonEscapeString('a"b\\c\nd\te')).toBe('a\\"b\\\\c\\nd\\te');
    expect(pythonEscapeString("\x7f")).toBe("\\u007f");
    expect(pythonEscapeString("\x01")).toBe("\\u0001");
  });
});

describe("lexeme-preserving canonicalization", () => {
  it("preserves Python float lexemes that JSON.stringify would destroy", () => {
    const tree = parseJsonPreservingLexemes('{"b": 96.0, "a": 4, "c": 1e-05}');
    expect(pythonCanonicalJson(tree)).toBe('{"a":4,"b":96.0,"c":1e-05}');
  });
  it("sorts keys by code point and uses compact separators", () => {
    const tree = parseJsonPreservingLexemes('{"z": 1, "a": true, "m": null}');
    expect(pythonCanonicalJson(tree)).toBe('{"a":true,"m":null,"z":1}');
  });
  it("drops ONLY the top-level manifest_hash, never nested keys", () => {
    const tree = parseJsonPreservingLexemes(
      '{"manifest_hash": "x", "nested": {"manifest_hash": "keep"}}',
    );
    expect(pythonCanonicalJson(tree, "manifest_hash")).toBe('{"nested":{"manifest_hash":"keep"}}');
  });
  it("throws SyntaxError on malformed JSON", () => {
    expect(() => parseJsonPreservingLexemes('{"a": }')).toThrow(SyntaxError);
    expect(() => parseJsonPreservingLexemes('{"a": 1} trailing')).toThrow(SyntaxError);
  });
});

describe("sha256HexUtf8", () => {
  it("matches a known SHA-256 vector", async () => {
    expect(await sha256HexUtf8("abc")).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
  });
});

describe("verifyManifestText against the GOLDEN backend fixture", () => {
  it("recomputes the exact manifest_hash sealed by the real backend", async () => {
    const outcome = await verifyManifestText(goldenRaw);
    expect(outcome.claimedHash).toBe(goldenExpected.manifest_hash);
    expect(outcome.computedHash).toBe(goldenExpected.manifest_hash);
    expect(outcome.verified).toBe(true);
  });

  it("detects re-badging an input source (the seal's promise)", async () => {
    const tampered = goldenRaw.replace('"synthetic_generated"', '"Synthetic_generated"');
    expect(tampered).not.toBe(goldenRaw);
    const outcome = await verifyManifestText(tampered);
    expect(outcome.verified).toBe(false);
    expect(outcome.computedHash).not.toBe(outcome.claimedHash);
  });
});

describe("verifyCaseProvenance", () => {
  const hash = goldenExpected.manifest_hash;
  const okResponse = (text: string) =>
    ({ ok: true, status: 200, text: async () => text }) as Response;

  it("verifies a genuine manifest against the displayed hash", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(okResponse(goldenRaw));
    const res = await verifyCaseProvenance("golden-case", hash, fetchImpl);
    expect(res.state).toBe("verified");
    expect(res.computedHash).toBe(hash);
    expect(fetchImpl).toHaveBeenCalledWith(
      "/cases/golden-case",
      expect.objectContaining({ headers: { Accept: "application/json" } }),
    );
  });

  it("fails when the manifest bytes were tampered with", async () => {
    const tampered = goldenRaw.replace('"synthetic_generated"', '"Synthetic_generated"');
    const fetchImpl = vi.fn().mockResolvedValue(okResponse(tampered));
    const res = await verifyCaseProvenance("golden-case", hash, fetchImpl);
    expect(res.state).toBe("failed");
    expect(res.detail).toMatch(/does not match/i);
  });

  it("fails when the re-fetched manifest doesn't match the hash on screen", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(okResponse(goldenRaw));
    const res = await verifyCaseProvenance("golden-case", "f".repeat(64), fetchImpl);
    expect(res.state).toBe("failed");
    expect(res.detail).toMatch(/on screen/i);
  });

  it("is honest (unavailable, not failed) when the manifest can't be fetched", async () => {
    const notFound = vi
      .fn()
      .mockResolvedValue({ ok: false, status: 404, text: async () => "" } as Response);
    expect((await verifyCaseProvenance("x", hash, notFound)).state).toBe("unavailable");

    const netFail = vi.fn().mockRejectedValue(new TypeError("network down"));
    expect((await verifyCaseProvenance("x", hash, netFail)).state).toBe("unavailable");
  });

  it("url-encodes case ids on the manifest fetch", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(okResponse(goldenRaw));
    await verifyCaseProvenance("case μ/../x", null, fetchImpl);
    expect(fetchImpl.mock.calls[0]?.[0]).toBe("/cases/case%20%CE%BC%2F..%2Fx");
  });
});
