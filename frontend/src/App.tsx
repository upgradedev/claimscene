import { useEffect, useState } from "react";
import { Header } from "./components/Header";
import { Footer } from "./components/Footer";
import { DisclosureBanner } from "./components/DisclosureBanner";
import { Hero } from "./components/Hero";
import { Studio } from "./components/Studio";
import { MyCases } from "./components/MyCases";
import { useCaseStore } from "./store/useCaseStore";

export default function App() {
  const [started, setStarted] = useState(false);
  // Only ever set true from the header's signed-in "My cases" menu item,
  // which itself renders nothing without Firebase config — so a guest build
  // (every build today) never has any control that could flip this, and this
  // branch of the render below is dead code for guest, not just untriggered.
  const [libraryOpen, setLibraryOpen] = useState(false);
  const reset = useCaseStore((s) => s.reset);

  const start = () => {
    reset();
    setStarted(true);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  // Deep-link: /#start jumps straight into the studio.
  useEffect(() => {
    if (window.location.hash === "#start") setStarted(true);
  }, []);

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
