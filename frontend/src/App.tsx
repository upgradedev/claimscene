import { useCallback, useEffect, useState } from "react";
import { Header } from "./components/Header";
import { Footer } from "./components/Footer";
import { DisclosureBanner } from "./components/DisclosureBanner";
import { Hero } from "./components/Hero";
import { Studio } from "./components/Studio";
import { MyCases } from "./components/MyCases";
import { ResumedCase } from "./components/ResumedCase";
import { caseHash, parseHash, replaceHash, type Route } from "./lib/route";
import { useCaseStore } from "./store/useCaseStore";

const readRoute = (): Route =>
  parseHash(typeof window === "undefined" ? "" : window.location.hash);

export default function App() {
  const [started, setStarted] = useState(false);
  // Only ever set true from the header's signed-in "My cases" menu item,
  // which itself renders nothing without Firebase config — so a guest build
  // (every build today) never has any control that could flip this, and this
  // branch of the render below is dead code for guest, not just untriggered.
  const [libraryOpen, setLibraryOpen] = useState(false);
  // What the URL names. A case id in the address bar is what lets a case
  // survive a refresh, an accidental close, or being reopened tomorrow. Once
  // a resumed case is handed to the store this drops back to `home`: the URL
  // has done its job and the studio takes over, exactly as after a fresh
  // render (the address bar keeps showing the case, written by the store).
  const [route, setRoute] = useState<Route>(readRoute);
  const reset = useCaseStore((s) => s.reset);

  const start = useCallback(() => {
    reset(); // also points the URL back at #start
    setRoute({ kind: "start" });
    setStarted(true);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [reset]);

  // React to REAL navigation only: a pasted link, the back button, someone
  // editing the address bar. The app's own URL writes go through
  // history.replaceState (see lib/route.ts), which fires no hashchange, so
  // sealing a case can never bounce the wizard into its own resume view.
  useEffect(() => {
    const onHashChange = () => {
      const raw = window.location.hash;
      const next = parseHash(raw);
      // An in-page anchor is not a route: the skip link at the top of this
      // file navigates to #main, and treating that as "no case named" would
      // both throw the visitor out of whatever they were doing and drop the
      // case id from the address bar. Ignore it, and put the open case's own
      // link back so it survives a later refresh.
      if (next.kind === "home" && raw !== "" && raw !== "#") {
        const open = useCaseStore.getState().result;
        if (open) replaceHash(caseHash(open.case_id));
        return;
      }
      // Already showing exactly the case the URL names (this is the URL the
      // store itself wrote when the case was sealed or resumed). Re-entering
      // the resume view for it would replace a finished case with a loading
      // panel that never resolves, because the case is already here.
      if (next.kind === "case" && useCaseStore.getState().result?.case_id === next.id) {
        return;
      }
      setRoute(next);
      if (next.kind === "start") {
        // #start means start. Arriving here with a case still open (someone
        // edited the address bar, or followed a #start link in a tab that
        // already had one) must give them the beginning of a new case, not
        // the last one they looked at.
        if (useCaseStore.getState().result) reset();
        setStarted(true);
      }
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [reset]);

  // Deep-link on first load: /#start jumps straight into the studio.
  useEffect(() => {
    if (route.kind === "start") setStarted(true);
    // First load only; later changes arrive through the hashchange listener.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A resumed case reached the store — hand the visitor to the studio, which
  // shows its result step. From there it behaves exactly like a case rendered
  // a moment ago: same panels, downloads, provenance and "New case" button.
  const onResumed = useCallback(() => {
    setRoute({ kind: "home" });
    setStarted(true);
  }, []);

  const onStartNewFromResume = useCallback(() => {
    setRoute({ kind: "start" });
    start();
  }, [start]);

  const resuming =
    route.kind === "case" || route.kind === "job" || route.kind === "unreadable";

  return (
    <div className="flex min-h-dvh flex-col">
      {/* Keyboard-first: the very first Tab reveals a skip link straight to the
          main region (bypassing the disclosure banner + header). */}
      <a
        href="#main"
        // Padding/background live behind `focus:` — applied to a bare `sr-only`
        // element they'd win over its 1px box (border-box) and leave a stray
        // visible sliver. Off-screen until focused, a real panel once focused.
        className="sr-only rounded font-mono text-sm text-blueprint-text focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:bg-steel-800 focus:px-4 focus:py-2 focus:ring-2 focus:ring-cyan-400"
      >
        Skip to main content
      </a>
      <DisclosureBanner />
      <Header
        onOpenLibrary={() => {
          setLibraryOpen(true);
          window.scrollTo({ top: 0, behavior: "smooth" });
        }}
      />
      <main id="main" tabIndex={-1} className="flex-1 outline-none">
        {libraryOpen ? (
          <MyCases onBack={() => setLibraryOpen(false)} />
        ) : resuming ? (
          <ResumedCase route={route} onStartNew={onStartNewFromResume} onLoaded={onResumed} />
        ) : started ? (
          <Studio />
        ) : (
          <Hero onStart={start} />
        )}
      </main>
      <Footer />
    </div>
  );
}
