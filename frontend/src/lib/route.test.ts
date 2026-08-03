import { describe, expect, it, beforeEach } from "vitest";
import { caseHash, caseLink, jobHash, parseHash, replaceHash, START_HASH } from "./route";

describe("hash routes (what makes a case survive a lost tab)", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("reads a sealed case link", () => {
    expect(parseHash("#case/left-cross-8f2ad91c")).toEqual({
      kind: "case", id: "left-cross-8f2ad91c",
    });
  });

  it("reads an in-flight render link", () => {
    expect(parseHash("#job/rW8kQ2zt-Ab_1")).toEqual({ kind: "job", id: "rW8kQ2zt-Ab_1" });
  });

  it("keeps the existing #start deep link working", () => {
    expect(parseHash("#start")).toEqual({ kind: "start" });
    expect(parseHash("")).toEqual({ kind: "home" });
    expect(parseHash("#")).toEqual({ kind: "home" });
  });

  it("treats an in-page anchor as no route at all", () => {
    // The skip link navigates to #main. That must not read as a broken case
    // link, or using the keyboard would throw someone out of their case.
    expect(parseHash("#main")).toEqual({ kind: "home" });
  });

  it("decodes a percent-encoded id", () => {
    expect(parseHash("#case/a%2Db")).toEqual({ kind: "case", id: "a-b" });
  });

  it.each([
    ["#case/", "empty id"],
    ["#case/../../etc/passwd", "traversal"],
    ["#case/has spaces", "space"],
    ["#job/%zz", "malformed escape"],
    [`#case/${"x".repeat(129)}`, "absurdly long"],
    ["#case/a<script>", "markup"],
  ])("rejects %s (%s) before any request is made", (hash) => {
    expect(parseHash(hash)).toEqual({ kind: "unreadable" });
  });

  it("builds links that round-trip", () => {
    const id = "rear-end.v2_9c1";
    expect(parseHash(caseHash(id))).toEqual({ kind: "case", id });
    expect(parseHash(jobHash(id))).toEqual({ kind: "job", id });
  });

  it("replaceHash changes the URL without adding history or firing hashchange", () => {
    const before = window.history.length;
    let fired = 0;
    const onHash = () => (fired += 1);
    window.addEventListener("hashchange", onHash);

    replaceHash(caseHash("sealed-1"));
    expect(window.location.hash).toBe("#case/sealed-1");
    expect(window.history.length).toBe(before);

    window.removeEventListener("hashchange", onHash);
    // jsdom dispatches hashchange asynchronously for location assignment; a
    // replaceState write dispatches nothing at all, sync or async.
    expect(fired).toBe(0);
  });

  it("replaceHash is a no-op when the URL already says that", () => {
    replaceHash(START_HASH);
    const length = window.history.length;
    replaceHash(START_HASH);
    expect(window.location.hash).toBe(START_HASH);
    expect(window.history.length).toBe(length);
  });

  it("caseLink is a whole address, not just a fragment", () => {
    const link = caseLink("sealed-1");
    expect(link.startsWith("http")).toBe(true);
    expect(link.endsWith("#case/sealed-1")).toBe(true);
  });
});
