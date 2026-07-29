/// <reference types="vitest" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Single-origin contract (see firebase.json + the Dockerfile): in production the
// FastAPI container serves BOTH the API and the compiled client, so the browser
// always talks same-origin — zero CORS. In dev we reproduce that with Vite's
// proxy: the browser calls http://localhost:5173/scenarios and Vite forwards it
// to VITE_API_BASE (a local backend or the Cloud Run URL). The app therefore
// uses relative paths everywhere and needs no CORS.
const API_ROUTES = ["/health", "/scenarios", "/cases", "/me"];

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const proxyTarget =
    env.VITE_DEV_PROXY_TARGET || env.VITE_API_BASE || "http://localhost:8000";

  return {
    plugins: [react()],
    resolve: {
      alias: { "@": path.resolve(__dirname, "./src") },
    },
    server: {
      port: 5173,
      proxy: Object.fromEntries(
        API_ROUTES.map((route) => [
          route,
          { target: proxyTarget, changeOrigin: true, secure: true },
        ]),
      ),
    },
    build: {
      outDir: "dist",
      sourcemap: false,
    },
    test: {
      globals: true,
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      css: true,
      // Vitest owns the jsdom unit/component suite under src/. The Playwright
      // end-to-end specs live in e2e/ (real browser) and must never be picked
      // up by Vitest's default `**/*.spec.ts` discovery.
      include: ["src/**/*.test.{ts,tsx}"],
      exclude: ["e2e/**", "node_modules/**", "dist/**"],
      coverage: {
        provider: "v8",
        // Count EVERY source file, not only the ones a test imports — so the
        // percentage is honest and an untested component shows as 0%, never
        // silently excluded from the denominator.
        all: true,
        include: ["src/**/*.{ts,tsx}"],
        exclude: [
          "src/main.tsx", // React root bootstrap (createRoot glue) — no logic
          "src/test/**", // test setup + fixtures
          "src/**/*.test.{ts,tsx}",
          "src/**/*.d.ts",
        ],
        reporter: ["text-summary", "text", "html"],
        // Calibrated to what the suite actually achieves (measured: lines +
        // statements 96%, branches 87%, functions 85%). Lines/statements gate
        // at 90 (a firm floor well above the 85% target); branches/functions at
        // 85 (their honest achieved floor). Has teeth without being brittle.
        thresholds: { lines: 90, statements: 90, branches: 85, functions: 85 },
      },
    },
  };
});
