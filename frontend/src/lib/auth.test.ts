import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

// firebase/app and firebase/auth are mocked at the module level so this file
// never touches the real Firebase SDK or network — deterministic, offline,
// matching every other test in this repo. Both are reached ONLY through the
// dynamic imports inside lib/auth.ts, so mocking them here also proves those
// dynamic imports resolve to the right module shape. Built with vi.hoisted()
// (rather than plain outer `const`s referenced from the vi.mock factories)
// because vi.mock calls are hoisted above imports — vi.hoisted is the
// documented-safe way to share mock references across that boundary.
const mocks = vi.hoisted(() => {
  const authInstance: {
    currentUser: null | { getIdToken: (force?: boolean) => Promise<string> };
  } = { currentUser: null };
  return {
    getApps: vi.fn(() => [] as unknown[]),
    getApp: vi.fn(() => ({ name: "[DEFAULT]" })),
    initializeApp: vi.fn((config: unknown) => ({ name: "[DEFAULT]", config })),
    authInstance,
    getAuth: vi.fn(() => authInstance),
    onAuthStateChanged: vi.fn(),
    signInWithPopup: vi.fn().mockResolvedValue(undefined),
    firebaseSignOut: vi.fn().mockResolvedValue(undefined),
    GoogleAuthProvider: class GoogleAuthProvider {},
  };
});

vi.mock("firebase/app", () => ({
  getApps: mocks.getApps,
  getApp: mocks.getApp,
  initializeApp: mocks.initializeApp,
}));

vi.mock("firebase/auth", () => ({
  getAuth: mocks.getAuth,
  onAuthStateChanged: mocks.onAuthStateChanged,
  signInWithPopup: mocks.signInWithPopup,
  signOut: mocks.firebaseSignOut,
  GoogleAuthProvider: mocks.GoogleAuthProvider,
}));

function enableAuthEnv() {
  vi.stubEnv("VITE_FIREBASE_API_KEY", "test-api-key");
  vi.stubEnv("VITE_FIREBASE_AUTH_DOMAIN", "test.firebaseapp.com");
  vi.stubEnv("VITE_FIREBASE_PROJECT_ID", "test-project");
  vi.stubEnv("VITE_FIREBASE_APP_ID", "1:123:web:abc");
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.clearAllMocks();
  // clearAllMocks resets call history but NOT implementations set via
  // .mockImplementation/.mockReturnValue in an individual test — reset the
  // ones tests override explicitly so no test's setup can leak into the next.
  mocks.authInstance.currentUser = null;
  mocks.getApps.mockReset().mockReturnValue([]);
  mocks.onAuthStateChanged.mockReset();
});

describe("isAuthEnabled", () => {
  it("is false when no VITE_FIREBASE_* vars are set (today's default)", async () => {
    const { isAuthEnabled } = await import("./auth");
    expect(isAuthEnabled()).toBe(false);
  });

  it("is false when only some of the four vars are set", async () => {
    vi.stubEnv("VITE_FIREBASE_API_KEY", "only-one");
    const { isAuthEnabled } = await import("./auth");
    expect(isAuthEnabled()).toBe(false);
  });

  it("is true only once all four vars are present", async () => {
    enableAuthEnv();
    const { isAuthEnabled } = await import("./auth");
    expect(isAuthEnabled()).toBe(true);
  });

  it("is false when a var is present but empty", async () => {
    enableAuthEnv();
    vi.stubEnv("VITE_FIREBASE_APP_ID", "");
    const { isAuthEnabled } = await import("./auth");
    expect(isAuthEnabled()).toBe(false);
  });
});

describe("disabled (no Firebase config) — every export is inert", () => {
  it("getIdToken resolves null without ever reaching Firebase", async () => {
    const { getIdToken } = await import("./auth");
    await expect(getIdToken()).resolves.toBeNull();
    expect(mocks.getAuth).not.toHaveBeenCalled();
  });

  it("signInWithGoogle is a no-op", async () => {
    const { signInWithGoogle } = await import("./auth");
    await signInWithGoogle();
    expect(mocks.signInWithPopup).not.toHaveBeenCalled();
  });

  it("signOut is a no-op", async () => {
    const { signOut } = await import("./auth");
    await signOut();
    expect(mocks.firebaseSignOut).not.toHaveBeenCalled();
  });

  it("onAuthChange calls cb(null) synchronously and its unsubscribe is a safe no-op", async () => {
    const { onAuthChange } = await import("./auth");
    const cb = vi.fn();
    const unsub = onAuthChange(cb);
    expect(cb).toHaveBeenCalledTimes(1);
    expect(cb).toHaveBeenCalledWith(null);
    expect(mocks.onAuthStateChanged).not.toHaveBeenCalled();
    expect(() => unsub()).not.toThrow();
  });

  it("useAuthUser settles to {user: null, loading: false} without touching Firebase", async () => {
    const { useAuthUser } = await import("./auth");
    const { result } = renderHook(() => useAuthUser());
    expect(result.current).toEqual({ user: null, loading: false });
    expect(mocks.getAuth).not.toHaveBeenCalled();
  });
});

describe("enabled (Firebase config present)", () => {
  beforeEach(() => {
    enableAuthEnv();
  });

  it("getIdToken resolves null when nobody is signed in", async () => {
    mocks.authInstance.currentUser = null;
    const { getIdToken } = await import("./auth");
    await expect(getIdToken()).resolves.toBeNull();
  });

  it("getIdToken asks the SDK fresh on every call — never caches", async () => {
    const sdkGetIdToken = vi.fn().mockResolvedValueOnce("token-1").mockResolvedValueOnce("token-2");
    mocks.authInstance.currentUser = { getIdToken: sdkGetIdToken };
    const { getIdToken } = await import("./auth");
    await expect(getIdToken()).resolves.toBe("token-1");
    await expect(getIdToken()).resolves.toBe("token-2");
    expect(sdkGetIdToken).toHaveBeenCalledTimes(2);
  });

  it("signInWithGoogle opens the Google popup with a GoogleAuthProvider", async () => {
    const { signInWithGoogle } = await import("./auth");
    await signInWithGoogle();
    expect(mocks.signInWithPopup).toHaveBeenCalledTimes(1);
    const [, provider] = mocks.signInWithPopup.mock.calls[0] as [unknown, unknown];
    expect(provider).toBeInstanceOf(mocks.GoogleAuthProvider);
  });

  it("signOut calls the SDK's signOut", async () => {
    const { signOut } = await import("./auth");
    await signOut();
    expect(mocks.firebaseSignOut).toHaveBeenCalledTimes(1);
  });

  it("initializeApp is skipped when an app already exists", async () => {
    mocks.getApps.mockReturnValue([{ name: "[DEFAULT]" }]);
    const { getIdToken } = await import("./auth");
    await getIdToken();
    expect(mocks.getApp).toHaveBeenCalled();
    expect(mocks.initializeApp).not.toHaveBeenCalled();
  });

  it("onAuthChange subscribes and maps a Firebase user to AuthUser", async () => {
    let captured: ((user: unknown) => void) | undefined;
    mocks.onAuthStateChanged.mockImplementation((_auth: unknown, cb: (user: unknown) => void) => {
      captured = cb;
      return vi.fn();
    });
    const { onAuthChange } = await import("./auth");
    const cb = vi.fn();
    onAuthChange(cb);
    await waitFor(() => expect(mocks.onAuthStateChanged).toHaveBeenCalledTimes(1));

    captured?.({
      uid: "u1",
      displayName: "Ada",
      email: "ada@example.com",
      photoURL: null,
      extra: "ignored",
    });
    expect(cb).toHaveBeenLastCalledWith({
      uid: "u1",
      displayName: "Ada",
      email: "ada@example.com",
      photoURL: null,
    });

    captured?.(null);
    expect(cb).toHaveBeenLastCalledWith(null);
  });

  it("onAuthChange leaves no live subscription when unsubscribed before the lazy import settles", async () => {
    const realUnsub = vi.fn();
    mocks.onAuthStateChanged.mockImplementation(() => realUnsub);
    const cb = vi.fn();
    const { onAuthChange } = await import("./auth");
    const unsub = onAuthChange(cb);
    unsub(); // synchronously, before getFirebaseAuth()'s promise has resolved
    // Let the pending lazy import settle (a macrotask runs after every
    // queued microtask, so the dynamic-import chain has finished by here).
    await new Promise((resolve) => setTimeout(resolve, 0));
    // Having been cancelled first, it never subscribes at all: there is no
    // subscription to leak, and the consumer's callback can never fire after
    // it unsubscribed.
    expect(mocks.onAuthStateChanged).not.toHaveBeenCalled();
    expect(realUnsub).not.toHaveBeenCalled();
    expect(cb).not.toHaveBeenCalled();
  });

  it("useAuthUser resolves to the signed-in user once onAuthStateChanged fires", async () => {
    mocks.onAuthStateChanged.mockImplementation((_auth: unknown, cb: (user: unknown) => void) => {
      cb({ uid: "u2", displayName: null, email: "u2@example.com", photoURL: "https://x/y.png" });
      return vi.fn();
    });
    const { useAuthUser } = await import("./auth");
    const { result } = renderHook(() => useAuthUser());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.user).toEqual({
      uid: "u2",
      displayName: null,
      email: "u2@example.com",
      photoURL: "https://x/y.png",
    });
  });

  it("useAuthUser unsubscribes on unmount", async () => {
    const realUnsub = vi.fn();
    mocks.onAuthStateChanged.mockImplementation(() => realUnsub);
    const { useAuthUser } = await import("./auth");
    const { unmount, result } = renderHook(() => useAuthUser());
    await waitFor(() => expect(mocks.onAuthStateChanged).toHaveBeenCalled());
    unmount();
    // Never resolved (this mock never invokes its callback with a user), so
    // the last observed state before unmount is still the loading one.
    expect(result.current.loading).toBe(true);
    expect(realUnsub).toHaveBeenCalledTimes(1);
  });
});
