import { useState } from "react";
import { BadgeCheck, Database, FileCheck2, Loader2, ShieldAlert, ShieldCheck } from "lucide-react";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { HashChip } from "./HashChip";
import { useManifest } from "@/lib/queries";
import { verifyCaseProvenance, type ProvenanceVerification } from "@/lib/provenance-verify";
import type { RenderResponse } from "@/lib/api";

type VerifyUiState = { phase: "idle" | "verifying" } | (ProvenanceVerification & { phase: "done" });

const SOURCE_LABEL: Record<string, string> = {
  user_upload: "user upload",
  staged_demo: "staged demo",
  public_domain: "public domain",
  licensed: "licensed",
  synthetic_generated: "synthetic",
};

export function ProvenancePanel({ result }: { result: RenderResponse }) {
  const { data: manifest, isLoading } = useManifest(result.case_id);
  const [verify, setVerify] = useState<VerifyUiState>({ phase: "idle" });

  const onVerify = async () => {
    setVerify({ phase: "verifying" });
    setVerify({ phase: "done", ...(await verifyCaseProvenance(result.case_id, result.manifest_hash)) });
  };

  return (
    <section
      aria-labelledby="provenance-heading"
      className="sheet sheet-ticks p-5"
    >
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
               className={verify.phase === "done" && verify.state === "failed"
                 ? "mt-2 text-xs text-red-300" : "mt-2 text-xs text-blueprint-dim"}>
              {verify.phase === "done"
                ? verify.detail
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

function SealBadge({ verify }: { verify: VerifyUiState }) {
  if (verify.phase === "verifying") {
    return (
      <Badge variant="neutral">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Verifying…
      </Badge>
    );
  }
  if (verify.phase === "done") {
    if (verify.state === "verified") {
      return (
        <Badge variant="verified">
          <ShieldCheck className="h-3.5 w-3.5" /> Verified ✓
        </Badge>
      );
    }
    if (verify.state === "failed") {
      return (
        <Badge variant="danger">
          <ShieldAlert className="h-3.5 w-3.5" /> Verification failed
        </Badge>
      );
    }
    return (
      <Badge variant="muted" title={verify.detail}>
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
