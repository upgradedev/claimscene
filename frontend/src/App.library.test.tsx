import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { claimsceneApi } from "@/lib/api";

// A true end-to-end path through App -> Header -> AuthMenu -> MyCases, with
// auth mocked as enabled + signed in. src/App.test.tsx covers the guest path
// (no auth mocking at all — isAuthEnabled() reads the real, unset env vars
// there) and is left completely untouched; this file only adds the
// signed-in library-navigation path App.tsx's new libraryOpen state exists
// for.
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

function renderApp() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <App />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.spyOn(claimsceneApi, "health").mockResolvedValue({
    status: "ok", service: "claimscene-api", mode: "offline",
  });
  vi.spyOn(claimsceneApi, "scenarios").mockResolvedValue([]);
  vi.spyOn(claimsceneApi, "myLibrary").mockResolvedValue([]);
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  mocks.isAuthEnabled.mockReturnValue(true);
  mocks.useAuthUser.mockReturnValue({
    user: { uid: "u1", displayName: "Ada", email: "ada@example.com", photoURL: null },
    loading: false,
  });
  mocks.signInWithGoogle.mockResolvedValue(undefined);
  mocks.signOut.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
  window.location.hash = "";
});

describe("<App /> — signed-in My cases navigation", () => {
  it("opens My cases from the header menu and returns to the landing on Back", async () => {
    renderApp();
    await userEvent.click(screen.getByRole("button", { name: "Ada" }));
    await userEvent.click(screen.getByRole("button", { name: /my cases/i }));

    expect(await screen.findByRole("heading", { name: /my cases/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Start a case/i })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /^back$/i }));
    expect(screen.getByRole("button", { name: /^Start a case/i })).toBeInTheDocument();
  });

  it("still enters the normal studio wizard when the visitor does not open the library", async () => {
    renderApp();
    await userEvent.click(screen.getByRole("button", { name: /^Start a case/i }));
    expect(screen.getByText(/Step 1 of 4/i)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /my cases/i })).not.toBeInTheDocument();
  });
});
