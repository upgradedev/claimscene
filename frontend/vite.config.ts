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
const API_ROUTES = ["/health", "/scenarios", "/cases"];

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
    },
  };
});
