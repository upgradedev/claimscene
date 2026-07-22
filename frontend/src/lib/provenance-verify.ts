// In-browser provenance verification (ported from Cinemory, MIT — same sealing
// foundation; adapted to the ClaimScene case manifest + endpoint).
//
// The backend seals every manifest with a canonical SHA-256
// (src/claimscene/provenance.py): `json.dumps(body, sort_keys=True,
// separators=(",", ":"))` over the manifest WITHOUT its top-level
// `manifest_hash`, UTF-8 encoded. This module recomputes that exact hash in the
// browser with WebCrypto, from the raw bytes served by `GET /cases/{id}`.
//
// Byte-exactness — why we never JSON.parse + JSON.stringify: Python and JS
// format numbers differently (`96.0` in Python is `96` in JS), so re-serializing
// parsed values can't reproduce the hashed bytes. Instead a mini parser keeps
// every number lexeme VERBATIM from the served text, and the emitter re-applies
// Python's canonical form: keys sorted by code point, compact separators, and
// `ensure_ascii=True` escaping (lowercase \uXXXX, surrogate pairs). The
// golden-fixture test pins this against a manifest from the real backend — whose
// sealed disclosure carries an em-dash, so the \uXXXX path is exercised.
import { API_BASE } from "./api";

// ── lexeme-preserving JSON tree ──────────────────────────────────────────────
export type JsonNode =
  | { kind: "obj"; entries: Array<[string, JsonNode]> }
  | { kind: "arr"; items: JsonNode[] }
  | { kind: "str"; value: string }
  /** Numbers and the `true`/`false`/`null` literals, kept verbatim. */
  | { kind: "raw"; lexeme: string };

const WS = new Set([" ", "\t", "\n", "\r"]);
const NUM_CHARS = new Set("-+.eE0123456789");
const SHORT_ESCAPES: Record<string, string> = {
  '"': '"', "\\": "\\", "/": "/", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t",
};

/** Parse JSON text into a tree that preserves raw number lexemes. Throws
 *  `SyntaxError` on malformed input. */
export function parseJsonPreservingLexemes(text: string): JsonNode {
  let i = 0;
  const fail = (msg: string): never => {
    throw new SyntaxError(`${msg} at offset ${i}`);
  };
  const skipWs = () => {
    while (i < text.length && WS.has(text[i] as string)) i += 1;
  };

  const parseString = (): string => {
    if (text[i] !== '"') fail("expected string");
    i += 1;
    let out = "";
    for (;;) {
      const c = text[i];
      if (c === undefined) fail("unterminated string");
      if (c === '"') {
        i += 1;
        return out;
      }
      if (c === "\\") {
        const e = text[i + 1];
        if (e === undefined) fail("unterminated escape");
        i += 2;
        if (e === "u") {
          const hex = text.slice(i, i + 4);
          if (!/^[0-9a-fA-F]{4}$/.test(hex)) fail("bad \\u escape");
          out += String.fromCharCode(parseInt(hex, 16));
          i += 4;
        } else {
          const mapped = SHORT_ESCAPES[e as string];
          if (mapped === undefined) fail(`bad escape \\${e}`);
          out += mapped;
        }
      } else {
        out += c;
        i += 1;
      }
    }
  };

  const parseValue = (): JsonNode => {
    skipWs();
    const c = text[i];
    if (c === undefined) fail("unexpected end of input");
    if (c === "{") return parseObject();
    if (c === "[") return parseArray();
    if (c === '"') return { kind: "str", value: parseString() };
    for (const lit of ["true", "false", "null"] as const) {
      if (text.startsWith(lit, i)) {
        i += lit.length;
        return { kind: "raw", lexeme: lit };
      }
    }
    let j = i;
    while (j < text.length && NUM_CHARS.has(text[j] as string)) j += 1;
    if (j === i) fail(`unexpected character ${JSON.stringify(c)}`);
    const lexeme = text.slice(i, j);
    i = j;
    return { kind: "raw", lexeme };
  };

  const parseObject = (): JsonNode => {
    i += 1; // consume "{"
    const entries: Array<[string, JsonNode]> = [];
    skipWs();
    if (text[i] === "}") {
      i += 1;
      return { kind: "obj", entries };
    }
    for (;;) {
      skipWs();
      const key = parseString();
      skipWs();
      if (text[i] !== ":") fail("expected ':'");
      i += 1;
      entries.push([key, parseValue()]);
      skipWs();
      if (text[i] === ",") {
        i += 1;
        continue;
      }
      if (text[i] !== "}") fail("expected ',' or '}'");
      i += 1;
      return { kind: "obj", entries };
    }
  };

  const parseArray = (): JsonNode => {
    i += 1; // consume "["
    const items: JsonNode[] = [];
    skipWs();
    if (text[i] === "]") {
      i += 1;
      return { kind: "arr", items };
    }
    for (;;) {
      items.push(parseValue());
      skipWs();
      if (text[i] === ",") {
        i += 1;
        continue;
      }
      if (text[i] !== "]") fail("expected ',' or ']'");
      i += 1;
      return { kind: "arr", items };
    }
  };

  const root = parseValue();
  skipWs();
  if (i !== text.length) fail("trailing characters");
  return root;
}

// ── Python-canonical emitter ─────────────────────────────────────────────────

/** Escape a string body exactly like Python `json.dumps(..., ensure_ascii=True)`:
 *  short escapes for the JSON specials, lowercase `\uXXXX` for everything else
 *  outside printable ASCII (0x20–0x7E). JS strings are UTF-16, so astral
 *  characters naturally emit as the surrogate pairs Python emits. */
export function pythonEscapeString(s: string): string {
  let out = "";
  for (let idx = 0; idx < s.length; idx += 1) {
    const ch = s[idx] as string;
    const code = s.charCodeAt(idx);
    if (ch === '"') out += '\\"';
    else if (ch === "\\") out += "\\\\";
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (ch === "\t") out += "\\t";
    else if (ch === "\b") out += "\\b";
    else if (ch === "\f") out += "\\f";
    else if (code < 0x20 || code > 0x7e) out += `\\u${code.toString(16).padStart(4, "0")}`;
    else out += ch;
  }
  return out;
}

const byCodePoint = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

/** Emit `json.dumps(value, sort_keys=True, separators=(",", ":"))` with
 *  `ensure_ascii=True`, reusing the preserved number lexemes verbatim.
 *  `dropTopLevelKey` mirrors the backend's exclusion of `manifest_hash`. */
export function pythonCanonicalJson(node: JsonNode, dropTopLevelKey?: string): string {
  const emit = (n: JsonNode): string => {
    switch (n.kind) {
      case "obj": {
        const entries = [...n.entries].sort((a, b) => byCodePoint(a[0], b[0]));
        return `{${entries.map(([k, v]) => `"${pythonEscapeString(k)}":${emit(v)}`).join(",")}}`;
      }
      case "arr":
        return `[${n.items.map(emit).join(",")}]`;
      case "str":
        return `"${pythonEscapeString(n.value)}"`;
      case "raw":
        return n.lexeme;
    }
  };
  if (node.kind === "obj" && dropTopLevelKey !== undefined) {
    return emit({ kind: "obj", entries: node.entries.filter(([k]) => k !== dropTopLevelKey) });
  }
  return emit(node);
}

/** SHA-256 of the UTF-8 encoding of `text`, as lowercase hex (WebCrypto). */
export async function sha256HexUtf8(text: string): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) throw new Error("WebCrypto (crypto.subtle) is not available");
  const digest = await subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

// ── manifest verification ────────────────────────────────────────────────────
export interface ManifestVerification {
  verified: boolean;
  claimedHash: string | null;
  computedHash: string;
}

/** Recompute the canonical manifest hash from the raw served JSON text and
 *  compare it to the seal recorded inside — the browser-side mirror of the
 *  backend's `verify_manifest`. Throws on malformed JSON / missing WebCrypto. */
export async function verifyManifestText(rawText: string): Promise<ManifestVerification> {
  const tree = parseJsonPreservingLexemes(rawText);
  if (tree.kind !== "obj") throw new SyntaxError("manifest is not a JSON object");
  const claimedNode = tree.entries.find(([k]) => k === "manifest_hash")?.[1];
  const claimedHash = claimedNode?.kind === "str" ? claimedNode.value : null;
  const computedHash = await sha256HexUtf8(pythonCanonicalJson(tree, "manifest_hash"));
  return { verified: claimedHash === computedHash, claimedHash, computedHash };
}

export type ProvenanceVerifyState = "verified" | "failed" | "unavailable";

export interface ProvenanceVerification {
  state: ProvenanceVerifyState;
  detail: string;
  claimedHash?: string | null;
  computedHash?: string | null;
}

/** Fetch `GET /cases/{id}` and verify its seal in the browser, additionally
 *  comparing against the `manifest_hash` already on screen so a swapped
 *  manifest can't masquerade as the displayed one.
 *
 *  - `verified`    — recomputed hash matches the seal (and the displayed hash)
 *  - `failed`      — the bytes do NOT hash to the recorded seal (tamper signal)
 *  - `unavailable` — the manifest couldn't be fetched here (404 / network) */
export async function verifyCaseProvenance(
  caseId: string,
  displayedHash: string | null | undefined,
  fetchImpl: typeof fetch = fetch,
): Promise<ProvenanceVerification> {
  let res: Response;
  try {
    res = await fetchImpl(`${API_BASE}/cases/${encodeURIComponent(caseId)}`, {
      headers: { Accept: "application/json" },
    });
  } catch {
    return {
      state: "unavailable",
      detail: "The manifest couldn't be fetched — check your connection and retry.",
    };
  }
  if (!res.ok) {
    return {
      state: "unavailable",
      detail:
        res.status === 404
          ? "This deployment doesn't serve the manifest for re-fetching, so it can't be re-checked here."
          : `The manifest couldn't be fetched (HTTP ${res.status}).`,
    };
  }
  let outcome: ManifestVerification;
  try {
    outcome = await verifyManifestText(await res.text());
  } catch (err) {
    return {
      state: "failed",
      detail: `The served manifest could not be canonicalized (${err instanceof Error ? err.message : String(err)}).`,
    };
  }
  const { claimedHash, computedHash } = outcome;
  if (!outcome.verified) {
    return {
      state: "failed",
      detail:
        "Recomputed SHA-256 does NOT match the sealed manifest_hash — the manifest bytes have changed.",
      claimedHash,
      computedHash,
    };
  }
  if (displayedHash && claimedHash !== displayedHash) {
    return {
      state: "failed",
      detail:
        "The re-fetched manifest is internally sealed but does not match the manifest_hash on screen.",
      claimedHash,
      computedHash,
    };
  }
  return {
    state: "verified",
    detail:
      "Recomputed the canonical SHA-256 in your browser from the re-fetched manifest — it matches the sealed manifest_hash.",
    claimedHash,
    computedHash,
  };
}
