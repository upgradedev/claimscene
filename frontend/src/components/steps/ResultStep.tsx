import { useState } from "react";
import { AlertTriangle, Download, FileText, Film, Link2, Plus, Ruler, ShieldAlert } from "lucide-react";
import { API_BASE, type RenderResponse } from "@/lib/api";
import { DEGRADE_CONSEQUENCE, DEGRADE_TITLE, degradeCause } from "@/lib/degrade";
import { caseLink } from "@/lib/route";
import { useCaseStore } from "@/store/useCaseStore";
import { illustrationIsPlayable } from "@/lib/utils";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { HashChip } from "../HashChip";
import { Markdown } from "../Markdown";
import { ProvenancePanel } from "../ProvenancePanel";

/** The link that reopens this exact sealed case.
 *
 *  Worth its own card rather than a line in the downloads row: the sealed case
 *  is what a claimant may want to send to an adjuster next week, and until now
 *  it existed only in the tab that made it. Copying is a convenience, so a
 *  browser that refuses clipboard access is told plainly to select the address
 *  instead, and the link itself is always on screen to be read. */
function CaseLinkCard({ caseId }: { caseId: string }) {
  const link = caseLink(caseId);
  const [copied, setCopied] = useState<"idle" | "done" | "failed">("idle");

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      setCopied("done");
    } catch {
      setCopied("failed");
    }
  };

  return (
    <section className="sheet p-4">
      <h3 className="label-caps mb-2 flex items-center gap-1.5">
        <Link2 className="h-3 w-3 text-cyan-400" /> keep this link
      </h3>
      <p className="text-sm leading-relaxed text-blueprint-dim">
        Opening this link later brings this sealed case back, files and all. If you made
        it while signed in, only that account can open it.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <code className="min-w-0 max-w-full break-all rounded border border-steel-700 bg-steel-950/80 px-2 py-1.5 font-mono text-xs text-cyan-200">
          {link}
        </code>
        <Button size="md" className="h-11" onClick={copy}>
          {copied === "done" ? "Link copied" : "Copy link"}
        </Button>
      </div>
      {copied === "failed" && (
        <p role="status" className="mt-2 text-sm text-amber-200">
          Your browser did not allow copying. Select the address above instead.
        </p>
      )}
    </section>
  );
}

export function ResultStep({ result }: { result: RenderResponse }) {
  const reset = useCaseStore((s) => s.reset);
  const playable = illustrationIsPlayable(result);
  const reportHref = `data:text/markdown;charset=utf-8,${encodeURIComponent(result.report_markdown)}`;

  return (
    <div className="animate-fade-up space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-mono text-2xl font-semibold text-blueprint-text">Case sealed</h2>
          <p className="mt-1 flex flex-wrap items-center gap-2 text-sm text-blueprint-dim">
            <span className="font-mono text-cyan-200">{result.case_id}</span>
            <HashChip hash={result.manifest_hash} label="manifest_hash" />
            {result.degraded ? (
              <Badge variant="amber" title="The illustration is deterministic offline bytes, not a live generative render.">
                illustration: offline
              </Badge>
            ) : (
              <Badge variant="verified">illustration: {result.provider}</Badge>
            )}
          </p>
        </div>
        <Button variant="secondary" onClick={reset}>
          <Plus className="h-4 w-4" /> New case
        </Button>
      </div>

      {/* What "sealed" does and doesn't mean -- reuses the DisclosureBanner's
          amber/#1c1608 styling so it reads as the same honesty line, right
          where "Case sealed" could otherwise be misread as "case verified". */}
      <div className="flex items-start gap-2.5 rounded border border-amber-400/30 bg-[#1c1608] px-4 py-2.5">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" aria-hidden />
        <p className="font-mono text-xs leading-relaxed tracking-wide text-amber-200">
          <span className="font-semibold">
            Sealed means the files are unchanged, not that the account is verified.
          </span>{" "}
          <span className="text-amber-200/70">
            This case documents what was reported and reviewed. It is not an official police
            report and does not establish fault.
          </span>
        </p>
      </div>

      {/* The live picture failed on THIS case. Said plainly, in the same place
          the sealed manifest records it, with the one fact that actually
          matters here: the schematic carries the facts and is untouched. */}
      {result.provider_degraded && (
        <div className="flex items-start gap-2.5 rounded border border-amber-400/40 bg-[#1c1608] px-4 py-3">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" aria-hidden />
          <div className="space-y-1.5 text-sm leading-relaxed text-amber-100">
            <p className="font-semibold">{DEGRADE_TITLE}</p>
            <p className="text-amber-200/80">{degradeCause(result.degrade_kind)}</p>
            <p className="text-amber-200/80">{DEGRADE_CONSEQUENCE}</p>
          </div>
        </div>
      )}

      {/* Media: factual schematic + disclosed illustration, side by side.
          grid-cols-1 base keeps the single mobile column container-bounded. */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Factual layer: presented as the reconstruction, ranked first --
            the AI clip alongside it is the illustration, never the reverse
            (see provenance.AUTHORSHIP_NOTE and the Provenance panel below). */}
        <figure className="sheet overflow-hidden">
          <figcaption className="flex items-center justify-between border-b border-steel-700/70 px-3 py-1.5">
            <span className="label-caps flex items-center gap-1.5">
              <Ruler className="h-3 w-3 text-cyan-400" /> Reconstruction · schematic
            </span>
            <Badge variant="amber">deterministic</Badge>
          </figcaption>
          <div className="aspect-[4/3] w-full bg-steel-850">
            {result.has_schematic_animation ? (
              <video
                src={`${API_BASE}${result.schematic_url}`}
                className="h-full w-full object-contain"
                controls
                loop
                muted
                playsInline
              />
            ) : (
              <img
                src={`${API_BASE}${result.schematic_url}`}
                alt="Sealed top-down accident schematic"
                className="h-full w-full object-contain"
              />
            )}
          </div>
        </figure>

        {/* Disclosed illustration */}
        <figure className="sheet overflow-hidden">
          <figcaption className="flex items-center justify-between border-b border-steel-700/70 px-3 py-1.5">
            <span className="label-caps flex items-center gap-1.5">
              <Film className="h-3 w-3 text-amber-400" /> AI illustration
            </span>
            <Badge variant="danger">not evidence</Badge>
          </figcaption>
          <div className="relative aspect-[4/3] w-full bg-steel-850">
            <div className="pointer-events-none absolute left-0 right-0 top-0 z-10 flex items-center justify-center gap-1.5 bg-gradient-to-b from-black/70 to-transparent py-1.5 font-mono text-[11px] font-semibold tracking-wide text-amber-300">
              <ShieldAlert className="h-3.5 w-3.5" /> AI ILLUSTRATION — NOT EVIDENCE
            </div>
            {playable ? (
              <video
                src={`${API_BASE}${result.illustration_url}`}
                className="h-full w-full object-contain"
                controls
                loop
                muted
                playsInline
              />
            ) : (
              <div className="grid h-full w-full place-items-center p-6 text-center">
                <p className="font-mono text-xs text-blueprint-dim">
                  The generative clip runs on the live path. This offline run sealed a
                  deterministic placeholder — its provenance still verifies below. Deploy
                  in live mode (GMI + B2) to render the illustration.
                </p>
              </div>
            )}
          </div>
        </figure>
      </div>

      {/* The case's own link. A sealed case is the durable thing here, and
          before this it could only be seen in the tab that made it. */}
      <CaseLinkCard caseId={result.case_id} />

      {/* Downloads */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="label-caps mr-1">download</span>
        <Button asChild variant="secondary" size="sm">
          <a href={`${API_BASE}${result.schematic_url}`} target="_blank" rel="noreferrer">
            <Ruler className="h-3.5 w-3.5" /> schematic
          </a>
        </Button>
        {playable && (
          <Button asChild variant="secondary" size="sm">
            <a href={`${API_BASE}${result.illustration_url}`} target="_blank" rel="noreferrer">
              <Film className="h-3.5 w-3.5" /> illustration
            </a>
          </Button>
        )}
        <Button asChild variant="secondary" size="sm">
          <a href={reportHref} download={`${result.case_id}-report.md`}>
            <FileText className="h-3.5 w-3.5" /> report.md
          </a>
        </Button>
        <Button asChild variant="secondary" size="sm">
          <a href={`${API_BASE}${result.manifest_url}`} target="_blank" rel="noreferrer">
            <Download className="h-3.5 w-3.5" /> manifest.json
          </a>
        </Button>
      </div>

      {/* Incident report */}
      <section className="sheet p-5">
        <h3 className="label-caps mb-3 flex items-center gap-1.5">
          <FileText className="h-3 w-3 text-cyan-400" /> Incident report · factual layer
        </h3>
        <Markdown source={result.report_markdown} />
      </section>

      {/* Provenance */}
      <ProvenancePanel result={result} />
    </div>
  );
}
