import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// AuthMenu reads isAuthEnabled/useAuthUser/signInWithGoogle/signOut directly
// from lib/auth — mock the whole module so no real (or even lazily-imported
// stub) Firebase code ever runs in a component test.
const mocks = vi.hoisted(() => ({
  isAuthEnabled: vi.fn(),
  useAuthUser: vi.fn(),
  signInWithGoogle: vi.fn(),
  signOut: vi.fn(),
}));

vi.mock("@/lib/auth", () => ({
  isAuthEnabled: mocks.isAuthEnabled,
  useAuthUser: mocks.useAuthUser,
  signInWithGoogle: mocks.signInWithGoogle,
  signOut: mocks.signOut,
}));

import { AuthMenu } from "./AuthMenu";

afterEach(() => {
  vi.restoreAllMocks();
  mocks.isAuthEnabled.mockReset();
  mocks.useAuthUser.mockReset();
  mocks.signInWithGoogle.mockReset().mockResolvedValue(undefined);
  mocks.signOut.mockReset().mockResolvedValue(undefined);
});

describe("<AuthMenu /> — auth disabled (guest, unchanged)", () => {
  it("renders nothing at all", () => {
    mocks.isAuthEnabled.mockReturnValue(false);
    mocks.useAuthUser.mockReturnValue({ user: null, loading: false });
    const { container } = render(<AuthMenu onOpenLibrary={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("<AuthMenu /> — auth enabled, signed out", () => {
  beforeEach(() => {
    mocks.isAuthEnabled.mockReturnValue(true);
    mocks.useAuthUser.mockReturnValue({ user: null, loading: false });
  });

  it("shows a Sign in with Google button", () => {
    render(<AuthMenu onOpenLibrary={vi.fn()} />);
    expect(screen.getByRole("button", { name: /sign in with google/i })).toBeInTheDocument();
  });

  it("calls signInWithGoogle on click", async () => {
    render(<AuthMenu onOpenLibrary={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /sign in with google/i }));
    expect(mocks.signInWithGoogle).toHaveBeenCalledTimes(1);
  });
});

describe("<AuthMenu /> — auth enabled, signed in", () => {
  const user = { uid: "u1", displayName: "Ada Lovelace", email: "ada@example.com", photoURL: null };

  beforeEach(() => {
    mocks.isAuthEnabled.mockReturnValue(true);
    mocks.useAuthUser.mockReturnValue({ user, loading: false });
  });

  it("shows the user's display name", () => {
    render(<AuthMenu onOpenLibrary={vi.fn()} />);
    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
  });

  it("falls back to email when displayName is null", () => {
    mocks.useAuthUser.mockReturnValue({ user: { ...user, displayName: null }, loading: false });
    render(<AuthMenu onOpenLibrary={vi.fn()} />);
    expect(screen.getByText("ada@example.com")).toBeInTheDocument();
  });

  it("opens the panel on click, showing My cases and Sign out", async () => {
    render(<AuthMenu onOpenLibrary={vi.fn()} />);
    const trigger = screen.getByRole("button", { name: "Ada Lovelace" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: /my cases/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign out/i })).toBeInTheDocument();
  });

  it("calls onOpenLibrary and closes the panel when My cases is clicked", async () => {
    const onOpenLibrary = vi.fn();
    render(<AuthMenu onOpenLibrary={onOpenLibrary} />);
    await userEvent.click(screen.getByRole("button", { name: "Ada Lovelace" }));
    await userEvent.click(screen.getByRole("button", { name: /my cases/i }));
    expect(onOpenLibrary).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: /my cases/i })).not.toBeInTheDocument();
  });

  it("calls signOut when Sign out is clicked", async () => {
    render(<AuthMenu onOpenLibrary={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Ada Lovelace" }));
    await userEvent.click(screen.getByRole("button", { name: /sign out/i }));
    expect(mocks.signOut).toHaveBeenCalledTimes(1);
  });

  it("closes the panel on Escape", async () => {
    render(<AuthMenu onOpenLibrary={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Ada Lovelace" }));
    expect(screen.getByRole("button", { name: /my cases/i })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("button", { name: /my cases/i })).not.toBeInTheDocument();
  });

  it("closes the panel on an outside click", async () => {
    render(
      <div>
        <button type="button">outside</button>
        <AuthMenu onOpenLibrary={vi.fn()} />
      </div>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Ada Lovelace" }));
    expect(screen.getByRole("button", { name: /my cases/i })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "outside" }));
    expect(screen.queryByRole("button", { name: /my cases/i })).not.toBeInTheDocument();
  });

  it("shows an avatar image when photoURL is present", () => {
    mocks.useAuthUser.mockReturnValue({
      user: { ...user, photoURL: "https://x.example/y.png" },
      loading: false,
    });
    // A decorative avatar (alt="") is intentionally NOT exposed with role
    // "img" to assistive tech, so it's queried directly rather than via
    // getByRole — the point of alt="" is that it should NOT be announced.
    const { container } = render(<AuthMenu onOpenLibrary={vi.fn()} />);
    const img = container.querySelector("img");
    expect(img).toHaveAttribute("src", "https://x.example/y.png");
    expect(img).toHaveAttribute("alt", "");
  });
});
