import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MyCases } from "./MyCases";
import { ApiError, claimsceneApi, type LibraryCase } from "@/lib/api";

// MyCases reads the current user via useAuthUser (lib/auth) but talks to the
// backend through claimsceneApi (lib/api) via the real queries.ts hooks, so
// react-query's real loading/success/error machinery is exercised.
const mocks = vi.hoisted(() => ({ useAuthUser: vi.fn() }));
vi.mock("@/lib/auth", () => ({ useAuthUser: mocks.useAuthUser }));

const SIGNED_IN = {
  user: { uid: "u1", displayName: "Ada", email: "ada@example.com", photoURL: null },
  loading: false,
};

function renderMyCases(onBack: () => void = vi.fn()) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MyCases onBack={onBack} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("<MyCases /> — loading and empty", () => {
  it("shows a loading skeleton while the library is being fetched, then the empty state", async () => {
    mocks.useAuthUser.mockReturnValue(SIGNED_IN);
    let resolveLibrary!: (value: LibraryCase[]) => void;
    const pending = new Promise<LibraryCase[]>((resolve) => {
      resolveLibrary = resolve;
    });
    vi.spyOn(claimsceneApi, "myLibrary").mockReturnValue(pending);

    const { container } = renderMyCases();
    expect(container.querySelector(".animate-pulse")).not.toBeNull();

    resolveLibrary([]);
    await waitFor(() =>
      expect(screen.getByText(/you have not saved any cases yet/i)).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: /delete all my data/i })).not.toBeInTheDocument();
  });
});

describe("<MyCases /> — list", () => {
  const CASES: LibraryCase[] = [
    { case_id: "rear-end-2026", manifest_hash: "a".repeat(16), created_at: "2026-01-15T10:00:00Z" },
    { case_id: "left-cross", manifest_hash: null, created_at: null },
  ];

  beforeEach(() => {
    mocks.useAuthUser.mockReturnValue(SIGNED_IN);
    vi.spyOn(claimsceneApi, "myLibrary").mockResolvedValue(CASES);
  });

  it("renders every saved case by id", async () => {
    renderMyCases();
    await waitFor(() => expect(screen.getByText("rear-end-2026")).toBeInTheDocument());
    expect(screen.getByText("left-cross")).toBeInTheDocument();
  });

  it("shows the delete-all control once cases exist", async () => {
    renderMyCases();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /delete all my data/i })).toBeInTheDocument(),
    );
  });

  it("shows 'Date unknown' for a case with no created_at", async () => {
    renderMyCases();
    await waitFor(() => expect(screen.getByText("left-cross")).toBeInTheDocument());
    const row = screen.getByText("left-cross").closest("li") as HTMLElement;
    expect(within(row).getByText("Date unknown")).toBeInTheDocument();
  });

  it("links each row to its case, schematic and illustration", async () => {
    renderMyCases();
    await waitFor(() => expect(screen.getByText("rear-end-2026")).toBeInTheDocument());
    const row = screen.getByText("rear-end-2026").closest("li") as HTMLElement;
    expect(within(row).getByRole("link", { name: /case/i })).toHaveAttribute(
      "href",
      "/cases/rear-end-2026",
    );
    expect(within(row).getByRole("link", { name: /schematic/i })).toHaveAttribute(
      "href",
      "/cases/rear-end-2026/schematic",
    );
    expect(within(row).getByRole("link", { name: /illustration/i })).toHaveAttribute(
      "href",
      "/cases/rear-end-2026/illustration",
    );
  });
});

describe("<MyCases /> — 401 graceful degrade vs generic error", () => {
  beforeEach(() => mocks.useAuthUser.mockReturnValue(SIGNED_IN));

  it("shows the honest degrade note on a 401 (backend multitenancy not enabled here)", async () => {
    vi.spyOn(claimsceneApi, "myLibrary").mockRejectedValue(new ApiError("nope", 401));
    renderMyCases();
    await waitFor(() =>
      expect(screen.getByText(/case library is not available on this deployment/i)).toBeInTheDocument(),
    );
  });

  it("shows a generic retry message on a non-401 ApiError", async () => {
    vi.spyOn(claimsceneApi, "myLibrary").mockRejectedValue(new ApiError("boom", 500));
    renderMyCases();
    await waitFor(() =>
      expect(screen.getByText(/could not load your cases right now/i)).toBeInTheDocument(),
    );
  });

  it("shows the same generic message for a non-ApiError failure (e.g. a network error)", async () => {
    vi.spyOn(claimsceneApi, "myLibrary").mockRejectedValue(new Error("network down"));
    renderMyCases();
    await waitFor(() =>
      expect(screen.getByText(/could not load your cases right now/i)).toBeInTheDocument(),
    );
    expect(
      screen.queryByText(/case library is not available on this deployment/i),
    ).not.toBeInTheDocument();
  });
});

describe("<MyCases /> — delete all my data", () => {
  const CASES: LibraryCase[] = [{ case_id: "c1", manifest_hash: null, created_at: null }];

  beforeEach(() => mocks.useAuthUser.mockReturnValue(SIGNED_IN));

  it("opens the confirm dialog, calls DELETE /me/data on confirm, and refreshes to the empty state", async () => {
    const myLibrary = vi
      .spyOn(claimsceneApi, "myLibrary")
      .mockResolvedValueOnce(CASES)
      .mockResolvedValueOnce([]);
    const deleteMyData = vi.spyOn(claimsceneApi, "deleteMyData").mockResolvedValue({ deleted: 1 });
    renderMyCases();

    await waitFor(() => expect(screen.getByText("c1")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /delete all my data/i }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /delete everything/i }));
    expect(deleteMyData).toHaveBeenCalledTimes(1);

    await waitFor(() =>
      expect(screen.getByText(/all your data has been deleted/i)).toBeInTheDocument(),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText(/you have not saved any cases yet/i)).toBeInTheDocument(),
    );
    expect(myLibrary).toHaveBeenCalledTimes(2); // initial fetch + refetch after invalidation
  });

  it("does not call DELETE /me/data when the dialog is cancelled", async () => {
    vi.spyOn(claimsceneApi, "myLibrary").mockResolvedValue(CASES);
    const deleteMyData = vi.spyOn(claimsceneApi, "deleteMyData");
    renderMyCases();

    await waitFor(() => expect(screen.getByText("c1")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /delete all my data/i }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(deleteMyData).not.toHaveBeenCalled();
  });

  it("shows an error inside the dialog and keeps it open when the delete fails", async () => {
    vi.spyOn(claimsceneApi, "myLibrary").mockResolvedValue(CASES);
    vi.spyOn(claimsceneApi, "deleteMyData").mockRejectedValue(new Error("network down"));
    renderMyCases();

    await waitFor(() => expect(screen.getByText("c1")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /delete all my data/i }));
    await userEvent.click(screen.getByRole("button", { name: /delete everything/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/could not delete your data/i),
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByText(/all your data has been deleted/i)).not.toBeInTheDocument();
  });

  it("clears the pending auto-dismiss timer on unmount without throwing", async () => {
    vi.spyOn(claimsceneApi, "myLibrary").mockResolvedValueOnce(CASES).mockResolvedValueOnce([]);
    vi.spyOn(claimsceneApi, "deleteMyData").mockResolvedValue({ deleted: 1 });
    const { unmount } = renderMyCases();

    await waitFor(() => expect(screen.getByText("c1")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /delete all my data/i }));
    await userEvent.click(screen.getByRole("button", { name: /delete everything/i }));
    await waitFor(() =>
      expect(screen.getByText(/all your data has been deleted/i)).toBeInTheDocument(),
    );

    expect(() => unmount()).not.toThrow();
  });
});

describe("<MyCases /> — back navigation and the sign-out guard", () => {
  it("calls onBack when the Back control is clicked", async () => {
    mocks.useAuthUser.mockReturnValue(SIGNED_IN);
    vi.spyOn(claimsceneApi, "myLibrary").mockResolvedValue([]);
    const onBack = vi.fn();
    renderMyCases(onBack);
    await userEvent.click(screen.getByRole("button", { name: /back/i }));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it("calls onBack automatically once auth resolves to signed-out", async () => {
    mocks.useAuthUser.mockReturnValue({ user: null, loading: false });
    const onBack = vi.fn();
    renderMyCases(onBack);
    await waitFor(() => expect(onBack).toHaveBeenCalledTimes(1));
  });

  it("does not call onBack while the initial auth state is still loading", () => {
    mocks.useAuthUser.mockReturnValue({ user: null, loading: true });
    const onBack = vi.fn();
    renderMyCases(onBack);
    expect(onBack).not.toHaveBeenCalled();
  });
});
