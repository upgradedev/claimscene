import { useEffect, useRef, useState } from "react";
import { FolderOpen, LogOut, User as UserIcon } from "lucide-react";
import { Button } from "./ui/button";
import { isAuthEnabled, signInWithGoogle, signOut, useAuthUser } from "@/lib/auth";

/** Header sign-in control. Renders NOTHING unless isAuthEnabled() — the
 *  guard is checked here too (not only by the caller) so this component is
 *  safe to mount unconditionally. Signed out: a "Sign in with Google"
 *  button. Signed in: the user's name/avatar with a small disclosure panel
 *  (a plain trigger + `<div>` of buttons, not `role="menu"` — that ARIA
 *  pattern carries strict keyboard/structure rules this doesn't need).
 *
 *  Ported from our other MIT entry, Cinemory, which pioneered this pattern
 *  for its own optional sign-in. */
export function AuthMenu({ onOpenLibrary }: { onOpenLibrary: () => void }) {
  const { user } = useAuthUser();
  const [open, setOpen] = useState(false);
  const [signingIn, setSigningIn] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocPointerDown = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onDocPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  if (!isAuthEnabled()) return null;

  const handleSignIn = async () => {
    setSigningIn(true);
    try {
      await signInWithGoogle();
    } finally {
      setSigningIn(false);
    }
  };

  const handleSignOut = async () => {
    setOpen(false);
    await signOut();
  };

  if (!user) {
    return (
      <Button variant="secondary" size="sm" onClick={handleSignIn} disabled={signingIn}>
        <UserIcon className="h-4 w-4" />
        {signingIn ? "Signing in…" : "Sign in with Google"}
      </Button>
    );
  }

  const label = user.displayName || user.email || "Signed in";

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls="account-panel"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex min-h-11 items-center gap-2 rounded-full border border-steel-600 bg-steel-800/70 px-3 py-1.5 font-mono text-sm text-blueprint-text transition-colors hover:border-cyan-400/50 hover:text-cyan-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/80 focus-visible:ring-offset-2 focus-visible:ring-offset-steel-950 sm:min-h-0"
      >
        {user.photoURL ? (
          <img
            src={user.photoURL}
            alt=""
            referrerPolicy="no-referrer"
            className="h-6 w-6 rounded-full"
          />
        ) : (
          <span className="grid h-6 w-6 place-items-center rounded-full bg-cyan-400/15 text-cyan-300">
            <UserIcon className="h-3.5 w-3.5" />
          </span>
        )}
        <span className="max-w-[9rem] truncate">{label}</span>
      </button>

      {open && (
        <div
          id="account-panel"
          className="sheet absolute right-0 top-full z-50 mt-2 w-48 overflow-hidden p-0"
        >
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onOpenLibrary();
            }}
            className="flex min-h-11 w-full items-center gap-2 px-4 py-2.5 text-left font-mono text-sm text-blueprint-text transition-colors hover:bg-white/[0.04]"
          >
            <FolderOpen className="h-4 w-4 text-cyan-400" />
            My cases
          </button>
          <button
            type="button"
            onClick={handleSignOut}
            className="flex min-h-11 w-full items-center gap-2 px-4 py-2.5 text-left font-mono text-sm text-blueprint-text transition-colors hover:bg-white/[0.04]"
          >
            <LogOut className="h-4 w-4 text-blueprint-dim" />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
