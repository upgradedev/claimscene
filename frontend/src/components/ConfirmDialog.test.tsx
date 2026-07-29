import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfirmDialog } from "./ConfirmDialog";

const baseProps = {
  title: "Delete all your data?",
  description: "This cannot be undone.",
  confirmLabel: "Delete everything",
};

function Harness({
  onConfirm = vi.fn(),
  onCancel = vi.fn(),
  ...rest
}: Partial<{
  onConfirm: () => void;
  onCancel: () => void;
  busy: boolean;
  error: string | null;
  destructive: boolean;
}> = {}) {
  return (
    <div>
      <button type="button">trigger</button>
      <ConfirmDialog {...baseProps} open onConfirm={onConfirm} onCancel={onCancel} {...rest} />
    </div>
  );
}

/** Mirrors real usage (MyCases): a trigger button flips `open`, and both
 *  onConfirm/onCancel close it — used specifically for the focus-return
 *  assertion, which needs a REAL open/close transition, not a static prop. */
function StatefulHarness() {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" onClick={() => setOpen(true)}>
        trigger
      </button>
      <ConfirmDialog
        {...baseProps}
        open={open}
        onConfirm={() => setOpen(false)}
        onCancel={() => setOpen(false)}
      />
    </div>
  );
}

afterEach(() => vi.restoreAllMocks());

describe("<ConfirmDialog /> — closed", () => {
  it("renders nothing", () => {
    const { container } = render(
      <ConfirmDialog {...baseProps} open={false} onConfirm={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe("<ConfirmDialog /> — open", () => {
  it("has an accessible dialog role labelled by the title", () => {
    render(<Harness />);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAccessibleName("Delete all your data?");
    expect(dialog).toHaveAttribute("aria-modal", "true");
  });

  it("moves focus to Cancel on open", () => {
    render(<Harness />);
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
  });

  it("calls onConfirm when the confirm button is clicked", async () => {
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} />);
    await userEvent.click(screen.getByRole("button", { name: "Delete everything" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("calls onCancel when Cancel is clicked", async () => {
    const onCancel = vi.fn();
    render(<Harness onCancel={onCancel} />);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("calls onCancel on Escape", async () => {
    const onCancel = vi.fn();
    render(<Harness onCancel={onCancel} />);
    await userEvent.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("calls onCancel when the overlay backdrop is clicked", async () => {
    const onCancel = vi.fn();
    render(<Harness onCancel={onCancel} />);
    const overlay = screen.getByRole("dialog").parentElement as HTMLElement;
    await userEvent.click(overlay);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("does not call onCancel when clicking inside the dialog panel", async () => {
    const onCancel = vi.fn();
    render(<Harness onCancel={onCancel} />);
    await userEvent.click(screen.getByText("This cannot be undone."));
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("traps Tab from the last focusable back to the first", async () => {
    render(<Harness />);
    const cancel = screen.getByRole("button", { name: "Cancel" });
    const confirm = screen.getByRole("button", { name: "Delete everything" });
    confirm.focus();
    expect(confirm).toHaveFocus();
    await userEvent.tab();
    expect(cancel).toHaveFocus();
  });

  it("traps Shift+Tab from the first focusable back to the last", async () => {
    render(<Harness />);
    const confirm = screen.getByRole("button", { name: "Delete everything" });
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(confirm).toHaveFocus();
  });

  it("returns focus to whatever triggered it, on close", async () => {
    render(<StatefulHarness />);
    const trigger = screen.getByRole("button", { name: "trigger" });
    await userEvent.click(trigger);
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(trigger).toHaveFocus();
  });

  it("shows the error message when provided, without closing", () => {
    const onCancel = vi.fn();
    render(<Harness onCancel={onCancel} error="Could not delete your data right now. Try again." />);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not delete your data right now.");
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("renders no alert when error is absent", () => {
    render(<Harness />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("disables both actions and shows Working… while busy", () => {
    render(<Harness busy />);
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Working…" })).toBeDisabled();
  });

  it("uses the primary variant by default and danger (red) when destructive", () => {
    const { rerender } = render(<Harness />);
    const primaryConfirm = screen.getByRole("button", { name: "Delete everything" });
    expect(primaryConfirm.className).not.toMatch(/border-red-400/);

    rerender(<Harness destructive />);
    const destructiveConfirm = screen.getByRole("button", { name: "Delete everything" });
    expect(destructiveConfirm.className).toMatch(/border-red-400/);
  });

  it("uses the custom cancel label when provided", () => {
    render(
      <ConfirmDialog {...baseProps} open onConfirm={vi.fn()} onCancel={vi.fn()} cancelLabel="Never mind" />,
    );
    expect(screen.getByRole("button", { name: "Never mind" })).toBeInTheDocument();
  });
});
