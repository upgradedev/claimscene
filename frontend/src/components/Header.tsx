import { Wordmark } from "./Wordmark";
import { Badge } from "./ui/badge";
import { useHealth } from "@/lib/queries";

/** Plain-English tooltip for the health badge. */
export function modeTooltip(mode: string): string {
  if (mode === "live") return "Backend: live = real B2 storage + VLM + Genblaze wired";
  if (mode === "offline")
    return "Backend: offline = running on the built-in deterministic backends (no cloud credentials needed)";
  return `Backend mode "${mode}"`;
}

export function Header() {
  const health = useHealth();

  return (
    <header className="sticky top-0 z-40 border-b border-steel-700/70 bg-steel-950/80 backdrop-blur-md">
      <div className="container flex h-14 items-center justify-between">
        <a href="/" className="rounded" aria-label="ClaimScene home">
          <Wordmark />
        </a>
        <div className="flex items-center gap-2">
          {health.isSuccess && (
            <Badge variant="verified" title={modeTooltip(health.data.mode)}>
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping-soft rounded-full bg-cyan-400 opacity-75" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-cyan-400" />
              </span>
              API · {health.data.mode}
            </Badge>
          )}
          {health.isError && (
            <Badge variant="muted" title="Backend unreachable">
              API offline
            </Badge>
          )}
        </div>
      </div>
    </header>
  );
}
