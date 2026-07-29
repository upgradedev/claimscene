import { useEffect, useRef } from "react";
import { Button } from "./ui/button";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  cancelLabel?: string;
  /** Styles the confirm action as destructive (red) rather than primary. */
  destructive?: boolean;
  /** Disables both actions while the confirmed operation is in flight. */
  busy?: boolean;
  /** Shown inside the dialog (never closes it) when the confirmed action failed. */
  error?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

/** A small, hand-rolled accessible confirmation dialog: `role="dialog"` +
 *  `aria-modal`, focus moves to Cancel on open and returns to the trigger on
 *  close, Tab/Shift+Tab is trapped inside, and Escape cancels. No portal —
 *  a fixed, high-z-index overlay is enough here since this is always mounted
 *  near the root of whichever view opens it.
 *
 *  Ported from our other MIT entry, Cinemory, which pioneered this pattern
 *  for its own delete-my-data confirmation. */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = "Cancel",
  destructive = false,
  busy = false,
  error = null,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  // Move focus in on open (defaulting to Cancel, not the destructive action),
  // and restore it to whatever triggered the dialog on close.
  useEffect(() => {
    if (!open) return;
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    cancelRef.current?.focus();
    return () => {
      previouslyFocused.current?.focus();
    };
  }, [open]);

  // Escape-to-cancel and a Tab focus trap confined to the dialog's own
  // focusable elements.
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
        return;
      }
      if (e.key !== "Tab") return;
      const focusables = dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (!focusables || focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (!first || !last) return;
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-steel-950/80 p-4 backdrop-blur-sm"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        aria-describedby="confirm-dialog-description"
        tabIndex={-1}
        className="sheet w-full max-w-sm p-6 outline-none"
      >
        <h2 id="confirm-dialog-title" className="font-mono text-lg font-semibold text-blueprint-text">
          {title}
        </h2>
        <p id="confirm-dialog-description" className="mt-2 text-sm text-blueprint-dim">
          {description}
        </p>
        {error && (
          <p role="alert" className="mt-3 text-xs text-red-300">
            {error}
          </p>
        )}
        <div className="mt-6 flex flex-wrap justify-end gap-3">
          <Button ref={cancelRef} variant="ghost" size="sm" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button
            variant={destructive ? "danger" : "primary"}
            size="sm"
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "Working…" : confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
