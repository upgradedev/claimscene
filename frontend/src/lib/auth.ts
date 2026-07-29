// Optional Google sign-in via Firebase Auth — gated entirely on the public
// Firebase web config being present at build time (see isAuthEnabled). This
// module is imported unconditionally from lib/api.ts, but it never pulls
// Firebase's JS into the bundle by doing so: `firebase/app` and `firebase/auth`
// are ONLY ever reached through a dynamic import inside getFirebaseAuth(), and
// every exported function checks isAuthEnabled() (or, for getIdToken, relies
// on getFirebaseAuth() never being called) before that import can happen. A
// guest build (the default: no VITE_FIREBASE_* vars, exactly today's
// production config) never executes that branch, so it never fetches the
// Firebase chunk at all.
//
// getIdToken() always asks the SDK for a fresh token: Firebase's own
// `user.getIdToken()` transparently refreshes the underlying JWT when the
// cached one is stale, so callers must never cache the raw string themselves
// (see the withAuth() helper in lib/api.ts, which calls this on every request
// instead of storing a token).
//
// Ported from our other MIT entry, Cinemory, which pioneered this pattern for
// its own frontend (see that repo's identical lib/auth.ts).
import { useEffect, useState } from "react";

export interface AuthUser {
  uid: string;
  displayName: string | null;
  email: string | null;
  photoURL: string | null;
}

const REQUIRED_VARS = [
  "VITE_FIREBASE_API_KEY",
  "VITE_FIREBASE_AUTH_DOMAIN",
  "VITE_FIREBASE_PROJECT_ID",
  "VITE_FIREBASE_APP_ID",
] as const;

/** All four public Firebase web-config vars are present at build time. When
 *  this is false, every other export below is inert (a safe no-op) and the
 *  sign-in UI must never render — see AuthMenu/MyCases.
 *
 *  Deliberately a dynamic `env[key]` lookup in a loop, not four static
 *  `import.meta.env.VITE_FIREBASE_API_KEY`-style member accesses: Vite
 *  statically replaces literal `import.meta.env.X` reads at build time, which
 *  would bake this to a fixed `false` in the production bundle regardless of
 *  runtime env and — worse for tests — make `vi.stubEnv` unable to flip it. */
export function isAuthEnabled(): boolean {
  const env = import.meta.env as unknown as Record<string, string | undefined>;
  return REQUIRED_VARS.every((key) => !!env[key]);
}

/** Only ever called after isAuthEnabled() has confirmed all four vars are
 *  non-empty (see getFirebaseAuth) — the `?? ""` fallbacks exist solely to
 *  give this a concrete `string`-typed return (matching FirebaseOptions)
 *  rather than `string | undefined`; they are unreachable in practice. */
function firebaseConfig(): {
  apiKey: string;
  authDomain: string;
  projectId: string;
  appId: string;
} {
  const env = import.meta.env as unknown as Record<string, string | undefined>;
  return {
    apiKey: env.VITE_FIREBASE_API_KEY ?? "",
    authDomain: env.VITE_FIREBASE_AUTH_DOMAIN ?? "",
    projectId: env.VITE_FIREBASE_PROJECT_ID ?? "",
    appId: env.VITE_FIREBASE_APP_ID ?? "",
  };
}

/** Lazily imports firebase/app + firebase/auth and resolves the singleton
 *  Auth instance. Never called unless isAuthEnabled() is true. */
async function getFirebaseAuth() {
  const [appMod, authMod] = await Promise.all([import("firebase/app"), import("firebase/auth")]);
  const app = appMod.getApps().length ? appMod.getApp() : appMod.initializeApp(firebaseConfig());
  return { auth: authMod.getAuth(app), authMod };
}

function toAuthUser(user: {
  uid: string;
  displayName: string | null;
  email: string | null;
  photoURL: string | null;
}): AuthUser {
  return { uid: user.uid, displayName: user.displayName, email: user.email, photoURL: user.photoURL };
}

/** Opens the Google sign-in popup. A no-op when auth is disabled. */
export async function signInWithGoogle(): Promise<void> {
  if (!isAuthEnabled()) return;
  const { auth, authMod } = await getFirebaseAuth();
  await authMod.signInWithPopup(auth, new authMod.GoogleAuthProvider());
}

/** Signs the current user out. A no-op when auth is disabled. */
export async function signOut(): Promise<void> {
  if (!isAuthEnabled()) return;
  const { auth, authMod } = await getFirebaseAuth();
  await authMod.signOut(auth);
}

/** Subscribes to auth state. `cb` fires once with the signed-in user (or
 *  null) and again on every sign-in/out. The returned unsubscribe is always
 *  safe to call, even before the lazy Firebase import resolves.
 *
 *  When auth is disabled, `cb(null)` fires synchronously, the returned
 *  unsubscribe is a no-op, and Firebase is never imported. */
export function onAuthChange(cb: (user: AuthUser | null) => void): () => void {
  if (!isAuthEnabled()) {
    cb(null);
    return () => {};
  }
  let cancelled = false;
  let unsub: (() => void) | undefined;
  getFirebaseAuth().then(({ auth, authMod }) => {
    if (cancelled) return;
    unsub = authMod.onAuthStateChanged(auth, (user) => cb(user ? toAuthUser(user) : null));
  });
  return () => {
    cancelled = true;
    unsub?.();
  };
}

/** A fresh ID token for the signed-in user, or null when signed out or auth
 *  is disabled. Always re-asks the SDK — never caches the raw JWT — so a
 *  long-lived tab keeps sending a valid, non-expired token. */
export async function getIdToken(): Promise<string | null> {
  if (!isAuthEnabled()) return null;
  const { auth } = await getFirebaseAuth();
  const user = auth.currentUser;
  if (!user) return null;
  return user.getIdToken();
}

/** React hook wrapper over onAuthChange: the current signed-in user (or
 *  null) plus whether the initial auth state is still resolving. When auth
 *  is disabled this settles to `{user: null, loading: false}` on the first
 *  render and never touches Firebase. */
export function useAuthUser(): { user: AuthUser | null; loading: boolean } {
  const [state, setState] = useState<{ user: AuthUser | null; loading: boolean }>(() => ({
    user: null,
    loading: isAuthEnabled(),
  }));

  useEffect(() => {
    if (!isAuthEnabled()) return;
    const unsub = onAuthChange((user) => setState({ user, loading: false }));
    return unsub;
  }, []);

  return state;
}
