import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { cn, shortHash } from "@/lib/utils";

export function HashChip({
  hash,
  label,
  className,
}: {
  hash: string | null | undefined;
  label?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    if (!hash) return;
    try {
      await navigator.clipboard.writeText(hash);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable — non-fatal */
    }
  };

  return (
    <button
      type="button"
      onClick={copy}
      disabled={!hash}
      title={hash ? `${label ? label + ": " : ""}${hash} — click to copy` : "unavailable"}
      className={cn(
        "group inline-flex items-center gap-1.5 rounded border border-steel-700 bg-steel-950/70 px-2 py-1 font-mono text-xs text-blueprint-text transition-colors hover:border-cyan-400/50 hover:text-cyan-200 disabled:opacity-50",
        className,
      )}
    >
      <span className="tabular-nums">{shortHash(hash)}</span>
      {copied ? (
        <Check className="h-3 w-3 text-cyan-300" />
      ) : (
        <Copy className="h-3 w-3 opacity-50 group-hover:opacity-100" />
      )}
    </button>
  );
}
