import { useState } from "react";
import {
  BadgeCheck,
  Check,
  Database,
  FileCheck2,
  Loader2,
  ShieldAlert,
  ShieldCheck,
  UserCheck,
  X,
} from "lucide-react";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { HashChip } from "./HashChip";
import { useManifest } from "@/lib/queries";
import {
  fetchCaseVerification,
  verifyCaseProvenance,
  type CaseVerification,
  type ProvenanceVerification,
} from "@/lib/provenance-verify";
import type { RenderResponse, Review, ReviewDiffRow } from "@/lib/api";

type VerifyUiState =
  | { phase: "idle" | "verifying" }
  | { phase: "done"; seal: ProvenanceVerification; checks: CaseVerification };

const SOURCE_LABEL: Record<string, string> = {
  user_upload: "user upload",
  staged_demo: "staged demo",
  public_domain: "public domain",
  licensed: "licensed",
  synthetic_generated: "synthetic",
};

// The honesty taxonomy, spelled out so a demo click is never dressed up as an
// authenticated approval.
const CLASSIFICATION: Record<
  string,
  { label: string; variant: "amber" | "verified" | "muted"; note: string }
> = {
  interactive_demo: {
    label: "interactive demo",
    variant: "amber",
    note: "an interactive demo click — not an authenticated approval",
  },
  authenticated_human: {
    label: "authenticated human",
    variant: "verified",
    note: "sealed by an authenticated reviewer",
  },
  unverified_no_baseline: {
    label: "no AI baseline",
    variant: "muted",
    note: "no AI-proposed scene was recorded to diff against",
  },
};

function fmtValue(v: unknown): string {
  if (v === null || v === undefined) return "∅";
  if (Array.isArray(v)) return v.length ? `[${v.join(", ")}]` : "[]";
  return String(v);
}

export function ProvenancePanel({ result }: { result: RenderResponse }) {
  const { data: manifest, isLoading } = useManifest(result.case_id);
  const [verify, setVerify] = useState<VerifyUiState>({ phase: "idle" });

  const onVerify = async () => {
    setVerify({ phase: "verifying" });
    // Recompute the seal in-browser AND fetch the server-side named-check
    // receipt (each check re-run from stored bytes) — one Verify action, both
    // attestations.
    const [seal, checks] = await Promise.all([
      verifyCaseProvenance(result.case_id, result.manifest_hash),
      fetchCaseVerification(result.case_id),
    ]);
    setVerify({ phase: "done", seal, checks });
  };

  return (
    <section aria-labelledby="provenance-heading" className="sheet sheet-ticks p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-cyan-400" />
            <h3 id="provenance-heading" className="font-mono text-lg font-semibold text-blueprint-text">
              Provenance
            </h3>
          </div>
          <p className="mt-1 text-sm text-blueprint-dim">
            Every artifact — and every input photo&apos;s source — is content-addressed
            by SHA-256 and sealed. Tampering with any field breaks the hash.
          </p>
        </div>
        <SealBadge verify={verify} />
      </div>

      {/* Seal + storage */}
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <div className="rounded border border-steel-700 bg-steel-950/50 p-3">
          <p className="label-caps">manifest seal</p>
          <div className="mt-2">
            <HashChip hash={result.manifest_hash} label="manifest_hash" />
          </div>
          <div className="mt-3">
            <Button variant="outline" size="sm" onClick={onVerify} disabled={verify.phase === "verifying"}
                    aria-describedby="verify-outcome">
              {verify.phase === "verifying" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <BadgeCheck className="h-3.5 w-3.5" />
              )}
              {verify.phase === "done" ? "Re-verify" : "Verify in browser"}
            </Button>
            <p id="verify-outcome" role="status" aria-live="polite"
               className={verify.phase === "done" && verify.seal.state === "failed"
                 ? "mt-2 text-xs text-red-300" : "mt-2 text-xs text-blueprint-dim"}>
              {verify.phase === "done"
                ? verify.seal.detail
                : verify.phase === "verifying"
                  ? "Re-fetching the manifest and recomputing its SHA-256 in your browser…"
                  : "Re-fetch the manifest and recompute its SHA-256 right here, in your browser."}
            </p>
          </div>
        </div>
        <div className="rounded border border-steel-700 bg-steel-950/50 p-3">
          <p className="label-caps">storage</p>
          <div className="mt-2 flex items-center gap-2">
            <span className="grid h-6 w-6 place-items-center rounded bg-amber-400/15 text-amber-300">
              <Database className="h-3.5 w-3.5" />
            </span>
            <span className="font-mono text-sm text-blueprint-text">Backblaze B2 · content-addressed</span>
          </div>
          {manifest && (
            <p className="mt-2 font-mono text-[11px] text-blueprint-dim">
              illustration: {manifest.illustration.provider} · {manifest.illustration.model}
              {manifest.illustration.degraded && " · offline"}
            </p>
          )}
        </div>
      </div>

      {/* Server-side named-check receipt (shown after Verify). */}
      {verify.phase === "done" && <VerificationChecks checks={verify.checks} />}

      {/* Review ledger — the sealed AI→human approval receipt. */}
      {manifest?.review && <ReviewLedger review={manifest.review} />}

      {/* Per-input attribution — ClaimScene's distinctive provenance surface. */}
      <div className="mt-4">
        <p className="label-caps mb-2">input photos · source attribution</p>
        {isLoading && (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="h-10 animate-pulse rounded border border-steel-700 bg-steel-900/50" />
            ))}
          </div>
        )}
        {manifest && (
          <ul className="space-y-2">
            {manifest.inputs.map((inp, i) => (
              <li key={i} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded border border-steel-700 bg-steel-950/50 p-2.5">
                <span className="font-mono text-xs text-blueprint-text">{inp.filename}</span>
                <Badge variant="neutral">{inp.role.replace("_photo", "")}</Badge>
                <Badge variant="amber">{SOURCE_LABEL[inp.source] ?? inp.source}</Badge>
                {inp.attribution && (
                  <span className="text-[11px] text-blueprint-dim">{inp.attribution}</span>
                )}
                {inp.license && (
                  <span className="font-mono text-[11px] text-blueprint-dim">{inp.license}</span>
                )}
                <span className="ml-auto">
                  <HashChip hash={inp.sha256} />
                </span>
              </li>
            ))}
          </ul>
        )}
        {!isLoading && !manifest && (
          <p className="rounded border border-steel-700 bg-steel-950/50 p-3 text-xs text-blueprint-dim">
            The full input manifest is served by the indexed store. On a live B2 path the
            provenance is sealed inside the case container and verified on download.
          </p>
        )}
      </div>
    </section>
  );
}

/** The sealed AI→human approval receipt: honesty counts, an expandable
 *  before→after list of the human's corrections, and the classification badge. */
function ReviewLedger({ review }: { review: Review }) {
  const cls = CLASSIFICATION[review.classification] ?? {
    label: review.classification,
    variant: "muted" as const,
    note: "",
  };
  const changed = review.diff.filter((r: ReviewDiffRow) => r.changed);
  const hasBaseline = review.scene_proposed_sha256 !== null;

  return (
    <div className="mt-4 rounded border border-cyan-400/20 bg-cyan-400/[0.03] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="label-caps flex items-center gap-1.5">
          <UserCheck className="h-3.5 w-3.5 text-cyan-400" /> review ledger · AI proposed, you confirmed
        </p>
        <Badge variant={cls.variant} title={cls.note}>{cls.label}</Badge>
      </div>

      {hasBaseline ? (
        <>
          <p className="mt-2 font-mono text-xs text-blueprint-text">
            <span className="text-cyan-200">{review.counts.proposed_fields}</span> fields AI-proposed ·{" "}
            <span className="text-amber-300">{review.counts.human_changed}</span> human-corrected ·{" "}
            <span className="text-blueprint-dim">{review.counts.unchanged}</span> unchanged
          </p>
          <p className="mt-1 text-[11px] text-blueprint-dim">{cls.note} · sealed into the manifest hash</p>

          {changed.length > 0 ? (
            <details className="group mt-2">
              <summary className="cursor-pointer font-mono text-[11px] text-cyan-200 hover:text-cyan-100">
                {changed.length} corrected {changed.length === 1 ? "field" : "fields"} — before → after
              </summary>
              <ul className="mt-2 space-y-1">
                {changed.map((row: ReviewDiffRow) => (
                  <li key={row.path}
                      className="flex flex-wrap items-center gap-2 rounded border border-steel-700 bg-steel-950/60 px-2 py-1 font-mono text-[11px]">
                    <span className="text-blueprint-text">{row.path}</span>
                    <span className="text-blueprint-dim line-through">{fmtValue(row.proposed)}</span>
                    <span aria-hidden className="text-blueprint-dim">→</span>
                    <span className="text-amber-200">{fmtValue(row.confirmed)}</span>
                  </li>
                ))}
              </ul>
            </details>
          ) : (
            <p className="mt-2 font-mono text-[11px] text-blueprint-dim">
              You confirmed the AI proposal with no changes.
            </p>
          )}
        </>
      ) : (
        <p className="mt-2 font-mono text-[11px] text-blueprint-dim">
          No AI baseline was recorded for this render, so no approval delta is sealed.
        </p>
      )}

      {review.prior_confidence_notes.length > 0 && (
        <div className="mt-3">
          <p className="label-caps mb-1">AI notes · carried forward</p>
          <ul className="space-y-1">
            {review.prior_confidence_notes.map((n, i) => (
              <li key={i} className="text-[11px] text-blueprint-dim">{n}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** The server-side named-check receipt (GET /cases/{id}/verify): each check
 *  re-run from stored bytes, with pass/fail + the evidence line. */
function VerificationChecks({ checks }: { checks: CaseVerification }) {
  if (checks.state === "unavailable") {
    return (
      <p className="mt-3 rounded border border-steel-700 bg-steel-950/50 p-2 text-[11px] text-blueprint-dim">
        {checks.detail}
      </p>
    );
  }
  const { receipt } = checks;
  const passed = receipt.checks.filter((c) => c.passed).length;

  return (
    <div className="mt-3 rounded border border-steel-700 bg-steel-950/50 p-3">
      <p className="label-caps mb-2 flex items-center justify-between gap-2">
        <span>server-side checks · {passed}/{receipt.checks.length} from stored bytes</span>
        <Badge variant={receipt.success ? "verified" : "danger"}>
          {receipt.success ? "all passed" : "failed"}
        </Badge>
      </p>
      <ul className="space-y-1">
        {receipt.checks.map((c) => (
          <li key={c.id}
              className="flex items-start gap-2 rounded border border-steel-700 bg-steel-900/40 px-2 py-1">
            {c.passed ? (
              <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-cyan-300" aria-label="passed" />
            ) : (
              <X className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-300" aria-label="failed" />
            )}
            <span className="min-w-0">
              <span className="block font-mono text-[11px] text-blueprint-text">{c.label}</span>
              <span className="block text-[11px] text-blueprint-dim">{c.evidence}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function SealBadge({ verify }: { verify: VerifyUiState }) {
  if (verify.phase === "verifying") {
    return (
      <Badge variant="neutral">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Verifying…
      </Badge>
    );
  }
  if (verify.phase === "done") {
    if (verify.seal.state === "verified") {
      return (
        <Badge variant="verified">
          <ShieldCheck className="h-3.5 w-3.5" /> Verified ✓
        </Badge>
      );
    }
    if (verify.seal.state === "failed") {
      return (
        <Badge variant="danger">
          <ShieldAlert className="h-3.5 w-3.5" /> Verification failed
        </Badge>
      );
    }
    return (
      <Badge variant="muted" title={verify.seal.detail}>
        Can&apos;t verify here
      </Badge>
    );
  }
  return (
    <Badge variant="verified">
      <FileCheck2 className="h-3.5 w-3.5" /> Sealed
    </Badge>
  );
}
