import { describe, expect, it } from "vitest";
import { DEGRADE_CONSEQUENCE, DEGRADE_TITLE, degradeCause } from "./degrade";

describe("plain-language wording for a live-illustration failure", () => {
  it.each([
    ["credit", /out of credit/i],
    ["auth", /did not accept our sign-in/i],
    ["rate_limit", /too many requests/i],
    ["timeout", /took too long/i],
    ["unavailable", /could not be reached/i],
  ])("says what kind of failure %s was", (kind, expected) => {
    expect(degradeCause(kind)).toMatch(expected);
  });

  it("admits it does not know rather than guessing", () => {
    expect(degradeCause("unknown")).toMatch(/did not say why/i);
    expect(degradeCause(undefined)).toMatch(/did not say why/i);
    expect(degradeCause(null)).toMatch(/did not say why/i);
    // A kind this build has never heard of must not blank the notice.
    expect(degradeCause("some_future_kind")).toMatch(/did not say why/i);
  });

  it("never names the provider, a status code or an account", () => {
    const all = [
      DEGRADE_TITLE,
      DEGRADE_CONSEQUENCE,
      ...["credit", "auth", "rate_limit", "timeout", "unavailable", "unknown", "x"]
        .map(degradeCause),
    ].join(" ");
    for (const leak of [/gmi/i, /genblaze/i, /\b40\d\b/, /\b5\d\d\b/, /api key/i,
                        /token/i, /http/i]) {
      expect(all, `wording leaked ${leak.source}`).not.toMatch(leak);
    }
  });

  it("always tells the visitor the schematic is unaffected", () => {
    // The illustration is the disclosed layer; the schematic carries the
    // facts. Losing the picture must never read as losing the case.
    expect(DEGRADE_CONSEQUENCE).toMatch(/schematic/i);
    expect(DEGRADE_CONSEQUENCE).toMatch(/unaffected/i);
    expect(DEGRADE_CONSEQUENCE).toMatch(/sealed/i);
  });

  it("has no em-dashes anywhere in the visitor-facing copy", () => {
    const all = [DEGRADE_TITLE, DEGRADE_CONSEQUENCE, degradeCause("credit")].join(" ");
    expect(all).not.toMatch(/—/);
  });
});
