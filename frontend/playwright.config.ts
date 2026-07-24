import { defineConfig, devices } from "@playwright/test";

/**
 * Real-browser end-to-end gate for the ClaimScene web client.
 *
 * Why this exists: the wizard's step transitions and the whole review → render →
 * sealed-case journey are driven by animation + async state that jsdom never
 * runs, so the Vitest suite passes synchronously even when the app dead-ends in
 * a real browser (it did — the `AnimatePresence mode="wait"` step-2 blank). These
 * specs drive the *built* app in headless Chromium with the API fully mocked via
 * `page.route` (see e2e/_mocks.ts), so they are deterministic and need no
 * backend, no credentials, and no network.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  timeout: 30_000,
  expect: { timeout: 7_000 },

  use: {
    baseURL: "http://localhost:4173",
    trace: "on-first-retry",
    // Deterministic rendering surface for the responsive + a11y assertions.
    viewport: { width: 1280, height: 800 },
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  // Build once, then serve the production bundle exactly as Firebase would.
  webServer: {
    command: "npm run build && npm run preview -- --port 4173 --strictPort",
    url: "http://localhost:4173",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});
