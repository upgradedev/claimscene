import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Download, FolderOpen, Film, Ruler, Trash2 } from "lucide-react";
import { Button } from "./ui/button";
import { HashChip } from "./HashChip";
import { ConfirmDialog } from "./ConfirmDialog";
import { useAuthUser } from "@/lib/auth";
import { ApiError, API_BASE, type LibraryCase } from "@/lib/api";
import { useDeleteMyData, useMyLibrary } from "@/lib/queries";

function formatDate(iso: string | null): string {
  if (!iso) return "Date unknown";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "Date unknown";
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/** The signed-in tenant's own case library — only ever mounted from the
 *  header's account menu, which itself only renders "My cases" when a user
 *  is signed in. Still defends against a client-side sign-out happening
 *  WHILE this view is open (the header stays mounted and reachable): once
 *  the auth state has definitively resolved to signed-out (not just still
 *  loading), it hands control back via `onBack` rather than keep showing a
 *  stale library.
 *
 *  Structurally ported from our other MIT entry, Cinemory's MyReels — this
 *  is the ClaimScene-flavoured equivalent (cases, not reels). */
export function MyCases({ onBack }: { onBack: () => void }) {
  const { user, loading } = useAuthUser();
  const library = useMyLibrary(user !== null);
  const deleteAll = useDeleteMyData();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [justDeleted, setJustDeleted] = useState(false);
  const dismissTimer = useRef<number | null>(null);

  useEffect(() => {
    if (!loading && user === null) onBack();
  }, [loading, user, onBack]);

  useEffect(
    () => () => {
      if (dismissTimer.current !== null) window.clearTimeout(dismissTimer.current);
    },
    [],
  );

  const unavailable =
    library.isError && library.error instanceof ApiError && library.error.status === 401;

  const handleDelete = () => {
    deleteAll.mutate(undefined, {
      onSuccess: () => {
        setConfirmOpen(false);
        setJustDeleted(true);
        if (dismissTimer.current !== null) window.clearTimeout(dismissTimer.current);
        dismissTimer.current = window.setTimeout(() => setJustDeleted(false), 4000);
      },
    });
  };

  return (
    <section className="container max-w-3xl py-12 md:py-16">
      <button
        type="button"
        onClick={onBack}
        className="mb-6 inline-flex min-h-11 items-center gap-1.5 font-mono text-sm text-blueprint-dim transition-colors hover:text-blueprint-text sm:min-h-0"
      >
        <ArrowLeft className="h-4 w-4" />
        Back
      </button>

      <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-mono text-3xl font-semibold text-blueprint-text">My cases</h1>
          <p className="mt-2 text-blueprint-dim">Every case sealed to your account.</p>
        </div>
        {!!library.data?.length && (
          <Button variant="danger" size="sm" onClick={() => setConfirmOpen(true)}>
            <Trash2 className="h-4 w-4" />
            Delete all my data
          </Button>
        )}
      </div>

      {justDeleted && (
        <p
          role="status"
          className="mb-6 rounded border border-cyan-400/20 bg-cyan-400/5 p-4 font-mono text-sm text-cyan-200"
        >
          All your data has been deleted.
        </p>
      )}

      {library.isLoading && (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-16 animate-pulse rounded border border-steel-700 bg-steel-900/50" />
          ))}
        </div>
      )}

      {unavailable && (
        <p className="rounded border border-steel-700 bg-steel-900/50 p-4 text-sm text-blueprint-dim">
          Case library is not available on this deployment.
        </p>
      )}

      {library.isError && !unavailable && (
        <p className="rounded border border-steel-700 bg-steel-900/50 p-4 text-sm text-blueprint-dim">
          Could not load your cases right now. Try again in a moment.
        </p>
      )}

      {library.data?.length === 0 && (
        <div className="rounded border border-steel-700 bg-steel-900/50 p-8 text-center">
          <FolderOpen className="mx-auto h-8 w-8 text-blueprint-dim" aria-hidden />
          <p className="mt-3 text-sm text-blueprint-dim">You have not saved any cases yet.</p>
        </div>
      )}

      {!!library.data?.length && (
        <ul className="space-y-3">
          {library.data.map((c) => (
            <CaseRow key={c.case_id} item={c} />
          ))}
        </ul>
      )}

      <ConfirmDialog
        open={confirmOpen}
        title="Delete all your data?"
        description="This permanently deletes every case linked to your account. This cannot be undone."
        confirmLabel="Delete everything"
        destructive
        busy={deleteAll.isPending}
        error={deleteAll.isError ? "Could not delete your data right now. Try again." : null}
        onConfirm={handleDelete}
        onCancel={() => setConfirmOpen(false)}
      />
    </section>
  );
}

function CaseRow({ item }: { item: LibraryCase }) {
  return (
    <li className="sheet flex flex-wrap items-center justify-between gap-3 p-4">
      <div className="min-w-0">
        <p className="truncate font-mono text-sm font-medium text-blueprint-text">{item.case_id}</p>
        <p className="text-xs text-blueprint-dim">{formatDate(item.created_at)}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {item.manifest_hash && <HashChip hash={item.manifest_hash} label="manifest_hash" />}
        <Button asChild variant="secondary" size="sm">
          <a href={`${API_BASE}/cases/${encodeURIComponent(item.case_id)}`} target="_blank" rel="noreferrer">
            <Download className="h-3.5 w-3.5" /> case
          </a>
        </Button>
        <Button asChild variant="secondary" size="sm">
          <a
            href={`${API_BASE}/cases/${encodeURIComponent(item.case_id)}/schematic`}
            target="_blank"
            rel="noreferrer"
          >
            <Ruler className="h-3.5 w-3.5" /> schematic
          </a>
        </Button>
        <Button asChild variant="secondary" size="sm">
          <a
            href={`${API_BASE}/cases/${encodeURIComponent(item.case_id)}/illustration`}
            target="_blank"
            rel="noreferrer"
          >
            <Film className="h-3.5 w-3.5" /> illustration
          </a>
        </Button>
      </div>
    </li>
  );
}
