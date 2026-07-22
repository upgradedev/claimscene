import { useEffect, useState } from "react";
import { Header } from "./components/Header";
import { Footer } from "./components/Footer";
import { DisclosureBanner } from "./components/DisclosureBanner";
import { Hero } from "./components/Hero";
import { Studio } from "./components/Studio";
import { useCaseStore } from "./store/useCaseStore";

export default function App() {
  const [started, setStarted] = useState(false);
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
      <DisclosureBanner />
      <Header />
      <main className="flex-1">{started ? <Studio /> : <Hero onStart={start} />}</main>
      <Footer />
    </div>
  );
}
